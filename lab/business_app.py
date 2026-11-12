"""Small real business workload. No analysis/reporting logic lives here.

All workload modes are fixed, bounded and expire without a controller lease.
Metrics are observations; the control mode/expected diagnosis are never exported.
"""
from collections import deque
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from pathlib import Path
import queue
import statistics
import threading
import time

import pymysql

MODES = {'normal', 'query', 'pool', 'temporary', 'queue', 'view'}


class Workload:
    allowed_modes = MODES
    def __init__(self, config):
        self.config = config
        self.started = time.monotonic()
        self.stop = threading.Event()
        self.lock = threading.RLock()
        self.mode, self.owner, self.expires = 'normal', None, 0
        self.pool = queue.Queue(maxsize=6)
        self.pending = queue.Queue(maxsize=24)
        self.samples = deque(maxlen=160)
        self.total, self.errors, self.dropped = 0, 0, 0
        self.waiters, self.active = 0, 0
        self.held = []
        self.view = None
        self.writer = None
        self.threads = []
        self.last_diagnostics, self.diagnostics_at = {}, 0
        self.epoch = None
        self.previous = {}

    def connect(self, monitor=False):
        cfg = self.config
        c = pymysql.connect(host=cfg['database_host'], port=3306,
            user=cfg['monitor_user'] if monitor else cfg['database_user'],
            password=cfg['monitor_password'] if monitor else cfg['database_password'],
            database=None if monitor else 'workload', autocommit=True, connect_timeout=3,
            read_timeout=3, write_timeout=3, cursorclass=pymysql.cursors.DictCursor)
        with c.cursor() as cur:
            cur.execute('SET SESSION MAX_EXECUTION_TIME=1200')
            cur.execute('SET SESSION wait_timeout=20')
        return c

    def start(self):
        for _ in range(6): self.pool.put(self.connect())
        for target in [self.produce, self.maintain, self.consume, self.consume]:
            t = threading.Thread(target=target, daemon=True)
            self.threads.append(t)
            t.start()

    def current_mode(self):
        with self.lock:
            return self.mode if time.monotonic() < self.expires else 'normal'

    def control(self, mode, owner, ttl=12):
        if mode not in self.allowed_modes or not isinstance(owner, str) or not 16 <= len(owner) <= 80:
            raise ValueError('invalid control')
        with self.lock:
            if self.owner and self.owner != owner and time.monotonic() < self.expires:
                raise ValueError('another owner is active')
            self.mode, self.owner, self.expires = mode, owner, time.monotonic()+min(15, max(1, ttl))
        return {'accepted': True}

    def produce(self):
        while not self.stop.wait(.25):
            try: self.pending.put_nowait(time.monotonic())
            except queue.Full:
                with self.lock: self.dropped += 1

    @staticmethod
    def execute_query(cur,mode):
        if mode=='temporary':
            try:
                cur.execute('SET SESSION tmp_table_size=16384')
                cur.execute('SET SESSION max_heap_table_size=16384')
                cur.execute("SELECT /*+ MAX_EXECUTION_TIME(1200) */ customer_id,CONCAT(payload,id) AS v,COUNT(*) AS n FROM orders GROUP BY customer_id,v ORDER BY v DESC LIMIT 50")
                cur.fetchall()
            finally:
                cur.execute('SET SESSION tmp_table_size=DEFAULT')
                cur.execute('SET SESSION max_heap_table_size=DEFAULT')
        else:
            if mode=='query':
                cur.execute("SELECT /*+ MAX_EXECUTION_TIME(1200) */ SUM(o.amount) AS total FROM orders o JOIN orders p ON MOD(p.customer_id,17)=MOD(o.customer_id,17) WHERE o.status LIKE '%pend%' AND p.id<800")
            else:cur.execute('SELECT SUM(amount) AS total FROM orders WHERE id BETWEEN 1 AND 20')
            cur.fetchall()

    def consume(self):
        while not self.stop.is_set():
            try: started = self.pending.get(timeout=.3)
            except queue.Empty: continue
            acquired, db_ms, failed, connection, query_started, waiting = 0, None, False, None, None, True
            with self.lock: self.waiters += 1
            wait_started = time.monotonic()
            try:
                connection = self.pool.get(timeout=.6)
                acquired = (time.monotonic()-wait_started)*1000
                with self.lock:self.waiters-=1;self.active+=1;waiting=False
                mode = self.current_mode()
                if mode == 'queue': self.stop.wait(.8)
                query_started = time.monotonic()
                with connection.cursor() as cur:
                    self.execute_query(cur,mode)
                db_ms = (time.monotonic()-query_started)*1000
            except (queue.Empty, pymysql.MySQLError, OSError): failed = True
            finally:
                if query_started is not None and db_ms is None:db_ms=(time.monotonic()-query_started)*1000
                with self.lock:
                    if waiting:self.waiters -= 1
                    if connection:
                        self.active -= 1
                        self.pool.put(connection)
                    self.total += 1
                    self.errors += int(failed)
                    self.samples.append({'at':time.monotonic(), 'request_ms':(time.monotonic()-started)*1000,
                        'db_ms':db_ms, 'pool_wait_ms':acquired if connection else 600, 'failed':failed})
                self.pending.task_done()

    def release_special(self):
        for c in self.held:
            try:
                c.ping(reconnect=True)
                self.pool.put_nowait(c)
            except (pymysql.MySQLError, queue.Full):
                try: c.close()
                except Exception: pass
        self.held.clear()
        for name in ('view', 'writer'):
            c = getattr(self, name)
            if c:
                try: c.rollback(); c.close()
                except Exception: pass
                setattr(self, name, None)

    def maintain(self):
        previous = 'normal'
        committed_versions = 0
        while not self.stop.wait(.5):
            mode = self.current_mode()
            try:
                with self.lock:
                    if mode != previous:
                        self.release_special()
                        committed_versions = 0
                        if mode == 'view':
                            self.view, self.writer = self.connect(), self.connect()
                            with self.view.cursor() as cur:
                                cur.execute('SET SESSION TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                                cur.execute('START TRANSACTION WITH CONSISTENT SNAPSHOT')
                                cur.execute('SELECT version FROM ledger WHERE id=1'); cur.fetchone()
                        previous = mode
                    if mode == 'pool':
                        while len(self.held) < 6:
                            try: self.held.append(self.pool.get_nowait())
                            except queue.Empty: break
                        for c in self.held:
                            with c.cursor() as cur: cur.execute('SELECT 1'); cur.fetchone()
                    # Fixed data budget even if a visitor leaves ON indefinitely.
                    # Keep the old snapshot; stop adding versions at this ceiling.
                    if mode == 'view' and self.writer and committed_versions < 600:
                        with self.writer.cursor() as cur: cur.execute('UPDATE ledger SET version=version+1,updated_at=UTC_TIMESTAMP(6) WHERE id=1')
                        committed_versions += 1
                    if mode == 'view' and self.view:
                        with self.view.cursor() as cur: cur.execute('SELECT version FROM ledger WHERE id=1'); cur.fetchone()
            except (pymysql.MySQLError, OSError):
                with self.lock: self.release_special(); previous = 'normal'
        with self.lock: self.release_special()

    @staticmethod
    def percentile(values, fraction=.95):
        values = sorted(values)
        return values[min(len(values)-1, int((len(values)-1)*fraction))] if values else None

    def metrics(self):
        now = time.monotonic()
        with self.lock:
            recent = [s for s in self.samples if now-s['at'] <= 30]
            samples = [s['request_ms'] for s in recent]
            db = [s['db_ms'] for s in recent if s['db_ms'] is not None]
            return {'sample_time':int(time.time()), 'request_total':self.total, 'error_total':self.errors,
                'dropped_total':self.dropped, 'request_rate':len(recent)/max(.001,min(30,now-self.started)),
                'request_p95_ms':self.percentile(samples), 'database_p95_ms':self.percentile(db),
                'pool_wait_p95_ms':self.percentile([s['pool_wait_ms'] for s in recent]),
                'error_ratio':sum(s['failed'] for s in recent)/len(recent) if recent else None,
                'queue_depth':self.pending.qsize(), 'pool_capacity':6, 'pool_in_use':6-self.pool.qsize(),
                'pool_waiters':self.waiters, 'workers_active':self.active,
                'recent_samples':len(recent)}

    def diagnostics(self):
        # Serialize polling: one read-only diagnostic connection at a time.
        with self.lock:
            if time.monotonic()-self.diagnostics_at < 3: return self.last_diagnostics
            with self.connect(monitor=True) as c, c.cursor() as cur:
                cur.execute('SET SESSION TRANSACTION READ ONLY')
                cur.execute('SELECT @@server_uuid AS uuid')
                uuid = cur.fetchone()['uuid']
                cur.execute("SHOW GLOBAL STATUS WHERE Variable_name IN ('Uptime','Threads_connected','Threads_running','Questions','Created_tmp_disk_tables')")
                status = {r['Variable_name']:int(r['Value']) for r in cur.fetchall()}
                cur.execute('SELECT @@max_connections AS capacity')
                capacity = cur.fetchone()['capacity']
                cur.execute('SELECT COUNT(*) AS n,COALESCE(MAX(TIMESTAMPDIFF(SECOND,TRX_STARTED,UTC_TIMESTAMP())),0) AS age FROM information_schema.INNODB_TRX')
                transactions = cur.fetchone()
                cur.execute("SELECT COUNT AS n FROM information_schema.INNODB_METRICS WHERE NAME='trx_rseg_history_len'")
                history = cur.fetchone()
                cur.execute("SELECT DIGEST,LEFT(DIGEST_TEXT,500) AS DIGEST_TEXT,COUNT_STAR,SUM_TIMER_WAIT,MAX_TIMER_WAIT,SUM_ROWS_EXAMINED,SUM_ROWS_SENT,SUM_CREATED_TMP_DISK_TABLES,FIRST_SEEN,LAST_SEEN FROM performance_schema.events_statements_summary_by_digest WHERE SCHEMA_NAME='workload' AND DIGEST IS NOT NULL ORDER BY SUM_TIMER_WAIT DESC LIMIT 15")
                digest = cur.fetchall()
            now = time.monotonic()
            counters={r['DIGEST']:(int(r['COUNT_STAR']),int(r['SUM_TIMER_WAIT']),int(r['SUM_ROWS_EXAMINED']),int(r['SUM_ROWS_SENT']),int(r['SUM_CREATED_TMP_DISK_TABLES']),str(r['FIRST_SEEN'])) for r in digest}
            reset = any(k in self.previous and (v[5] != self.previous[k][5] or any(a<b for a,b in zip(v[:5],self.previous[k][:5]))) for k,v in counters.items())
            if self.epoch is None or reset or status['Uptime'] < self.last_diagnostics.get('uptime_seconds',0):
                self.epoch = uuid+':'+str(int(time.time()*1000))
            deltas=[tuple(a-b for a,b in zip(v[:5],self.previous[k][:5])) for k,v in counters.items() if k in self.previous and v[5]==self.previous[k][5] and all(a>=b for a,b in zip(v[:5],self.previous[k][:5]))]
            sums=[sum(v[i] for v in deltas) for i in range(5)]
            elapsed=max(1,now-self.diagnostics_at)
            self.last_diagnostics={'sample_time':int(time.time()),'uptime_seconds':status['Uptime'],
                'connections':status['Threads_connected'],'connections_capacity':int(capacity),'threads_running':status['Threads_running'],
                'oldest_transaction_seconds':int(transactions['age']),'transaction_count':transactions['n'],
                'history_list_length':history['n'] if history else 0,
                'query_average_ms':sums[1]/1e9/sums[0] if sums[0] else None,
                'rows_examined_per_execution':sums[2]/sums[0] if sums[0] else None,
                'rows_sent_per_execution':sums[3]/sums[0] if sums[0] else None,
                'temporary_disk_tables_rate':sums[4]/elapsed if self.previous and not reset else None,
                'window_valid':int(bool(self.previous) and not reset),'window_seconds':elapsed if self.previous and not reset else None,
                'statement_count_delta':sums[0], 'counter_reset':int(reset),
                'statement_snapshot':{'schema_version':1,'timer_unit':'picoseconds','cumulative_counters':True,
                    'counter_epoch':self.epoch,'scope':'since_server_start_or_last_digest_reset',
                    'collected_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'rows':digest}}
            self.previous, self.diagnostics_at = counters, now
            return self.last_diagnostics

    def close(self):
        self.stop.set()
        for t in self.threads: t.join(timeout=4)
        self.release_special()
        while not self.pool.empty():
            self.pool.get_nowait().close()


def main():
    cfg = json.loads(Path(os.environ.get('BUSINESS_CONFIG','/run/secrets/business.json')).read_text())
    if cfg.get('source_host'):
        from replica_app import ReplicaWorkload
        work = ReplicaWorkload(cfg)
    else:work = Workload(cfg)
    work.start()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self,status,data):
            raw=json.dumps(data,default=str,separators=(',',':')).encode()
            self.send_response(status); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
        def do_GET(self):
            try:
                if self.path=='/control' and hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+cfg['control_token']):
                    with work.lock:
                        return self.send(200,{'normal':work.current_mode()=='normal','held_connections':len(work.held),'held_read_view':work.view is not None})
                if self.path=='/metrics': return self.send(200,work.metrics())
                if self.path=='/database': return self.send(200,work.diagnostics())
                if self.path=='/health': return self.send(200,{'ready':True})
                self.send(404,{'error':'not_found'})
            except Exception: self.send(503,{'error':'measurement_unavailable'})
        def do_PUT(self):
            if self.path!='/control' or not hmac.compare_digest(self.headers.get('Authorization',''),'Bearer '+cfg['control_token']):
                return self.send(403,{'error':'forbidden'})
            try:
                size=int(self.headers.get('Content-Length',0))
                if not 0<size<1024: raise ValueError()
                data=json.loads(self.rfile.read(size))
                if set(data)!={'mode','owner','ttl'}: raise ValueError()
                self.send(200,work.control(**data))
            except (ValueError,TypeError): self.send(409,{'error':'control_rejected'})
    try: ThreadingHTTPServer(('0.0.0.0',8080),Handler).serve_forever()
    finally: work.close()


if __name__=='__main__': main()
