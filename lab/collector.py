import json
import time
from datetime import datetime, timezone
from pathlib import Path
from lab.db import connect, config, identity
from lab.core import delta, send
from lab.metrics import HOSTS

def utc(): return datetime.now(timezone.utc).isoformat()

def linux_counters(proc='/host/proc'):
    cpu=list(map(int,Path(proc,'stat').read_text().splitlines()[0].split()[1:9]))
    # Linux CPU guest fields excluded: already included in user/nice.
    total=sum(cpu); idle=cpu[3]+cpu[4]
    reads=writes=0
    for line in Path(proc,'diskstats').read_text().splitlines():
        fields=line.split(); name=fields[2]
        # Whole virtual/physical disks only; avoid partitions and loop devices.
        if (name.startswith(('sd','vd','xvd')) and not name[-1].isdigit()) or (name.startswith('nvme') and 'p' not in name):
            reads+=int(fields[5])*512; writes+=int(fields[9])*512
    return dict(cpu_total=total,cpu_idle=idle,reads=reads,writes=writes)

class Collector:
    def __init__(self,cfg=None): self.cfg=cfg or config(); self.prev=None
    def sample(self):
        started=time.monotonic(); timestamp=utc(); values={}; errors=[]
        provenance={'source_mode':'live_lab','collected_at':timestamp,'target_uuid':self.cfg['server_uuid'],'nominal_sample_period_ms':3000,
          'queries':['SHOW GLOBAL STATUS','performance_schema.data_lock_waits','performance_schema.events_statements_summary_by_digest'],
          'host_counters':'VM /proc/stat and /proc/diskstats; aggregate VM, not target-only CPU',
          'network_probe':'probe container -> target:3306 authenticated SELECT 1; includes SQL roundtrip, not network-only latency',
          'missing':[]}
        counters={}
        try:
            with connect(self.cfg) as conn,conn.cursor() as cur:
                cur.execute("SHOW GLOBAL STATUS WHERE Variable_name IN ('Queries','Threads_running')")
                status={r['Variable_name']:int(r['Value']) for r in cur.fetchall()}
                counters['queries']=status['Queries']; values['sl.mysql.threads_running']=status['Threads_running']
                cur.execute('SELECT COUNT(*) AS n FROM performance_schema.data_lock_waits')
                values['sl.mysql.lock_waits']=cur.fetchone()['n']
                cur.execute('SELECT ENGINE, REQUESTING_ENGINE_TRANSACTION_ID, BLOCKING_ENGINE_TRANSACTION_ID, REQUESTING_THREAD_ID, BLOCKING_THREAD_ID FROM performance_schema.data_lock_waits LIMIT 20')
                values['sl.mysql.lock_snapshot']=json.dumps({'source_mode':'live_lab','collected_at':timestamp,'rows':cur.fetchall()},default=str)
                cur.execute("SELECT DIGEST, DIGEST_TEXT, COUNT_STAR, SUM_TIMER_WAIT, MAX_TIMER_WAIT, SUM_ROWS_EXAMINED FROM performance_schema.events_statements_summary_by_digest WHERE SCHEMA_NAME='lab_orders' ORDER BY SUM_TIMER_WAIT DESC LIMIT 8")
                values['sl.mysql.sql_summary']=json.dumps({'source_mode':'live_lab','collected_at':timestamp,'timer_unit':'picoseconds','cumulative_since_server_start':True,'rows':cur.fetchall()},default=str)
        except Exception as exc: errors.append('mysql:'+type(exc).__name__)
        try: counters.update(linux_counters())
        except Exception as exc: errors.append('host:'+type(exc).__name__)
        probe_start=time.monotonic()
        try:
            with connect(self.cfg) as conn,conn.cursor() as cur: cur.execute('SELECT 1'); cur.fetchone()
            values['sl.net.mysql_latency_ms']=(time.monotonic()-probe_start)*1000
            values['sl.net.mysql_available']=1
        except Exception as exc:
            values['sl.net.mysql_available']=0
            errors.append('probe:'+type(exc).__name__)
        if self.prev:
            previous,previous_time=self.prev; elapsed=started-previous_time
            for counter,key in [('queries','sl.mysql.qps'),('reads','sl.host.disk_read_bps'),('writes','sl.host.disk_write_bps')]:
                rate=delta(previous.get(counter),counters.get(counter,0),elapsed) if counter in counters else None
                if rate is not None: values[key]=rate
                else: provenance['missing'].append(key+':initial/reset/gap/unavailable')
            total=counters.get('cpu_total',0)-previous.get('cpu_total',0)
            idle=counters.get('cpu_idle',0)-previous.get('cpu_idle',0)
            if all(k in counters and k in previous for k in ('cpu_total','cpu_idle')) and 0<elapsed<=30 and total>0 and 0<=idle<=total: values['sl.host.cpu_pct']=100*(total-idle)/total
            else: provenance['missing'].append('sl.host.cpu_pct:reset/gap/unavailable')
        else: provenance['missing'].append('rate metrics: first sample requires second counter observation')
        self.prev=(counters,started)
        provenance['errors']=errors
        return {'timestamp':timestamp,'clock':int(time.time()),'values':values,'provenance':provenance}
    def publish(self,sample):
        rows=[]
        for key,value in sample['values'].items():
            layer='host' if key.startswith('sl.host.') else 'net' if key.startswith('sl.net.') else 'db'
            rows.append(dict(host=HOSTS[layer],key=key,value=str(value),clock=sample['clock']))
        for layer in HOSTS:
            rows.append(dict(host=HOSTS[layer],key='sl.provenance',value=json.dumps(sample['provenance']),clock=sample['clock']))
        return send(rows)

def collect_forever(interval=3):
    collector=Collector(); identity(collector.cfg)
    Path('.runtime/samples').mkdir(parents=True,exist_ok=True)
    while True:
        sample=collector.sample()
        try: sample['sender']=collector.publish(sample)
        except Exception as exc: sample['sender_error']=str(exc)
        with Path('.runtime/samples/observations.jsonl').open('a') as stream: stream.write(json.dumps(sample)+'\n')
        print(json.dumps({'timestamp':sample['timestamp'],'lock_waits':sample['values'].get('sl.mysql.lock_waits'),'sender':sample.get('sender'),'error':sample.get('sender_error')}),flush=True)
        time.sleep(interval)
