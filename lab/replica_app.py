"""Bounded real source writes and replica reads, separate from analysis."""
import time
import pymysql
try:
    from .business_app import Workload
except ImportError:
    from business_app import Workload


def replication_observations(row):
    if not row:return {'replica_io_running':None,'replica_sql_running':None,'replica_lag_seconds':None}
    return {'replica_io_running':int(row.get('Replica_IO_Running')=='Yes'),
            'replica_sql_running':int(row.get('Replica_SQL_Running')=='Yes'),
            'replica_lag_seconds':row.get('Seconds_Behind_Source')}


class ReplicaWorkload(Workload):
    allowed_modes={'normal','replica'}

    def __init__(self,config):
        super().__init__(config)
        self.replica_values={'read_version_gap':None,'read_freshness_age_seconds':None}

    def source_connect(self):
        return pymysql.connect(host=self.config['source_host'],port=3306,
            user=self.config['source_user'],password=self.config['source_password'],
            database='workload',autocommit=True,connect_timeout=3,read_timeout=3,write_timeout=3,
            cursorclass=pymysql.cursors.DictCursor)

    def maintain(self):
        source=None
        while not self.stop.wait(.5):
            try:
                if source is None:source=self.source_connect()
                with self.lock:
                    if self.current_mode()=='replica' and self.view is None:
                        self.view=self.connect()
                        with self.view.cursor() as cur:
                            cur.execute('SET SESSION innodb_lock_wait_timeout=2')
                            cur.execute('START TRANSACTION')
                            cur.execute('SELECT version FROM ledger WHERE id=1 FOR UPDATE');cur.fetchone()
                    elif self.current_mode()!='replica' and self.view:
                        self.release_special()
                    if self.view:
                        # Keep the owned transaction alive while its lease is valid.
                        # Session wait_timeout must not silently end a visible ON.
                        with self.view.cursor() as cur:
                            cur.execute('SELECT version FROM ledger WHERE id=1 FOR UPDATE');cur.fetchone()
                with source.cursor() as cur:
                    # Bound the backlog even when ON stays set indefinitely.
                    # Resume real writes as soon as the replica catches up.
                    if (self.replica_values.get('read_version_gap') or 0)<600:
                        cur.execute('UPDATE ledger SET version=version+1,updated_at=UTC_TIMESTAMP(6) WHERE id=1')
                    cur.execute('SELECT version,updated_at FROM ledger WHERE id=1');latest=cur.fetchone()
                with self.connect(monitor=True) as c,c.cursor() as cur:
                    cur.execute('SELECT version,updated_at FROM workload.ledger WHERE id=1');observed=cur.fetchone()
                gap=int(latest['version'])-int(observed['version'])
                values={'read_version_gap':gap,
                    'read_freshness_age_seconds':max(0,(latest['updated_at']-observed['updated_at']).total_seconds()) if gap>0 else 0}
                with self.lock:self.replica_values=values
            except (pymysql.MySQLError,OSError,TypeError,KeyError):
                with self.lock:
                    self.replica_values={'read_version_gap':None,'read_freshness_age_seconds':None}
                    self.release_special()
                if source:
                    try:source.close()
                    except Exception:pass
                source=None
        self.release_special()
        if source:source.close()

    def metrics(self):
        result=super().metrics()
        with self.lock:result.update(self.replica_values)
        return result

    def diagnostics(self):
        result=dict(super().diagnostics())
        with self.connect(monitor=True) as c,c.cursor() as cur:
            cur.execute('SHOW REPLICA STATUS');result.update(replication_observations(cur.fetchone()))
        return result
