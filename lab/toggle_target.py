"""Fixed rollback-only Lab workload, independent of any analysis platform.

The two workload connections use a dedicated account capped by MySQL at two
sessions. A short holder idle lease is renewed only by the owning controller;
process/network loss therefore cannot leave a permanent blocking transaction.
"""
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
from pathlib import Path
import threading
import time

import paramiko
import pymysql

SQL = 'UPDATE lab_orders.orders SET amount_cents=amount_cents+1 WHERE id=1'
SCENARIO_USER = 'demo_scenario'
HOLDER_LEASE_SECONDS = 15
MARKER = {'marker':'signallens-lab-v1','container':'signallens-lab-target',
          'owner':'signallens-lab','role':'target','database':'lab_orders'}


def utc():
    return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


class TargetError(RuntimeError):
    def __init__(self, code='target_unavailable'):
        self.code=code
        super().__init__(code)


def load_target(path):
    try:
        path=Path(path)
        if not path.is_absolute():raise ValueError()
        cfg=json.loads(path.read_text(encoding='utf-8-sig'))
        profile=cfg['profile']
        if (profile['mode']!='ssh' or profile.get('tls_mode')!='disabled'
            or not ipaddress.ip_address(profile['db_host']).is_private
            or profile['db_port']!=3306 or not profile['expected_server_uuid']
            or cfg['scenario']['username']!=SCENARIO_USER
            or cfg['monitor']['username']!='monitor'
            or cfg['expected_marker']!=MARKER
            or len(cfg['baseline_sha256'])!=64
            or any(c not in '0123456789abcdef' for c in cfg['baseline_sha256'])
            or not Path(cfg['ssh']['known_hosts']).is_absolute()):raise ValueError()
        return cfg
    except (OSError,ValueError,KeyError,TypeError):
        raise TargetError('target_configuration_invalid') from None


def validate_container(data,cfg):
    try:
        labels=data['Config']['Labels'];networks=data['NetworkSettings']['Networks']
        if (data['Name']!='/signallens-lab-target' or data['State']['Running'] is not True
            or data['HostConfig']['NetworkMode']=='host'
            or labels.get('sl.owner')!='signallens-lab'
            or labels.get('sl.role')!='target'
            or labels.get('com.docker.compose.project')!='signallens-lab'
            or set(networks)!={'signallens-lab_lab','signallens-internal'}
            or networks['signallens-lab_lab']['IPAddress']!=cfg['profile']['db_host']
            or any(data['NetworkSettings'].get('Ports',{}).values())):raise ValueError()
    except (ValueError,KeyError,TypeError):
        raise TargetError('target_identity_mismatch') from None


@contextmanager
def _ssh(cfg):
    client=paramiko.SSHClient()
    try:
        value=cfg['ssh']
        client.load_host_keys(value['known_hosts'])
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
        client.connect(value['host'],port=int(value.get('port',22)),username=value['username'],
                       password=value.get('password'),key_filename=value.get('key_filename'),
                       timeout=3,auth_timeout=5,banner_timeout=5,allow_agent=False,look_for_keys=False)
        yield client
    finally:
        client.close()


def _container_guard(cfg):
    with _ssh(cfg) as ssh:
        _,stdout,_=ssh.exec_command('docker inspect signallens-lab-target',timeout=5)
        raw=stdout.read(65537)
        if len(raw)>65536 or stdout.channel.recv_exit_status()!=0:raise TargetError('target_identity_mismatch')
        validate_container(json.loads(raw)[0],cfg)


@contextmanager
def _connect(cfg,identity):
    with _ssh(cfg) as ssh:
        channel=ssh.get_transport().open_channel('direct-tcpip',(cfg['profile']['db_host'],3306),('127.0.0.1',0),timeout=3)
        connection=None
        try:
            value=cfg[identity]
            connection=pymysql.Connection(host=cfg['profile']['db_host'],port=3306,
                user=value['username'],password=value['password'],database=None,
                autocommit=True,charset='utf8mb4',cursorclass=pymysql.cursors.DictCursor,
                connect_timeout=3,read_timeout=4,write_timeout=3,ssl_disabled=True,defer_connect=True)
            connection.connect(sock=channel)
            with connection.cursor() as cursor:
                cursor.execute("SET SESSION time_zone='+00:00'")
                cursor.execute('SET SESSION MAX_EXECUTION_TIME=2000')
                cursor.execute('SELECT @@server_uuid AS server_uuid')
                if cursor.fetchone().get('server_uuid')!=cfg['profile']['expected_server_uuid']:
                    raise TargetError('target_identity_mismatch')
            yield connection
        finally:
            if connection is not None:
                try:connection.close()
                except Exception:pass
            channel.close()


def _marker(connection,cfg):
    with connection.cursor() as cursor:
        cursor.execute('SELECT marker,container,owner,role,`database` FROM lab_orders.identity_marker WHERE id=1')
        if cursor.fetchone()!=cfg['expected_marker']:raise TargetError('target_identity_mismatch')


def _digest(connection):
    digest=hashlib.sha256()
    with connection.cursor() as cursor:
        cursor.execute('SELECT id,customer_id,amount_cents,status FROM lab_orders.orders ORDER BY id')
        for row in cursor.fetchall():
            digest.update((json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode())
    return digest.hexdigest()


def _idle(connection,cfg):
    with connection.cursor() as cursor:
        cursor.execute('SELECT COUNT(*) AS n FROM performance_schema.threads WHERE PROCESSLIST_USER=%s',(SCENARIO_USER,))
        sessions=int(cursor.fetchone()['n'])
        cursor.execute('SELECT COUNT(*) AS n FROM performance_schema.data_lock_waits w '
                       'JOIN performance_schema.threads r ON r.THREAD_ID=w.REQUESTING_THREAD_ID '
                       'JOIN performance_schema.threads b ON b.THREAD_ID=w.BLOCKING_THREAD_ID '
                       'WHERE r.PROCESSLIST_USER=%s OR b.PROCESSLIST_USER=%s',(SCENARIO_USER,SCENARIO_USER))
        waits=int(cursor.fetchone()['n'])
    return {'sessions_closed':sessions==0,'locks_cleared':waits==0,
            'dataset_unchanged':_digest(connection)==cfg['baseline_sha256'],'observed_at':utc()}


class FixedToggleFault:
    def __init__(self,target_config_path):
        self.cfg=load_target(target_config_path)
        self._stack=ExitStack()
        self._holder=self._waiter=self._monitor=None
        self._holder_id=self._waiter_id=None
        self._stop=threading.Event()
        self._thread=None
        self._waiter_failed=False

    def start(self):
        try:
            _container_guard(self.cfg)
            self._monitor=self._stack.enter_context(_connect(self.cfg,'monitor'))
            _marker(self._monitor,self.cfg)
            idle=_idle(self._monitor,self.cfg)
            if not all(idle[key] for key in ('sessions_closed','locks_cleared','dataset_unchanged')):
                raise TargetError('target_not_idle')
            self._holder=self._stack.enter_context(_connect(self.cfg,'scenario'))
            self._waiter=self._stack.enter_context(_connect(self.cfg,'scenario'))
            for conn in (self._holder,self._waiter):conn.autocommit(False)
            with self._waiter.cursor() as cursor:
                cursor.execute('SET SESSION innodb_lock_wait_timeout=2')
                cursor.execute('SET SESSION wait_timeout=10')
                cursor.execute('SELECT CONNECTION_ID() AS id');self._waiter_id=int(cursor.fetchone()['id'])
            with self._holder.cursor() as cursor:
                cursor.execute('SET SESSION innodb_lock_wait_timeout=2')
                cursor.execute('SELECT CONNECTION_ID() AS id');self._holder_id=int(cursor.fetchone()['id'])
                cursor.execute('SET SESSION wait_timeout=%s',(HOLDER_LEASE_SECONDS,))
                cursor.execute(SQL)
            self._thread=threading.Thread(target=self._wait,name='lab-toggle-waiter',daemon=True)
            self._thread.start()
            until=time.monotonic()+5
            while time.monotonic()<until:
                sample=self.sample()
                if sample['lock_waits']>0:return sample
                if self._stop.wait(.05):break
            raise TargetError('fault_not_observed')
        except Exception as exc:
            self.stop()
            if isinstance(exc,TargetError):raise
            raise TargetError() from None

    def _wait(self):
        while not self._stop.is_set():
            try:
                with self._waiter.cursor() as cursor:cursor.execute(SQL)
            except Exception as exc:
                if not getattr(exc,'args',()) or exc.args[0]!=1205:
                    self._waiter_failed=True
                    return
            finally:
                try:self._waiter.rollback()
                except Exception:self._waiter_failed=True
            self._stop.wait(.03)

    def sample(self):
        with self._monitor.cursor() as cursor:
            cursor.execute('SELECT COUNT(*) AS n FROM performance_schema.data_lock_waits w '
                           'JOIN performance_schema.threads r ON r.THREAD_ID=w.REQUESTING_THREAD_ID '
                           'JOIN performance_schema.threads b ON b.THREAD_ID=w.BLOCKING_THREAD_ID '
                           'WHERE r.PROCESSLIST_ID=%s AND b.PROCESSLIST_ID=%s '
                           'AND r.PROCESSLIST_USER=%s AND b.PROCESSLIST_USER=%s',
                           (self._waiter_id,self._holder_id,SCENARIO_USER,SCENARIO_USER))
            waits=int(cursor.fetchone()['n'])
        return {'active':not self._stop.is_set() and not self._waiter_failed,'lock_waits':waits,'observed_at':utc()}

    def heartbeat(self):
        if self._stop.is_set() or self._waiter_failed:raise TargetError('fault_connection_lost')
        # Deliberately no reconnect/replay: this exact transaction owns the row.
        with self._holder.cursor() as cursor:cursor.execute('SELECT 1')
        return self.sample()

    def stop(self):
        self._stop.set()
        if self._holder is not None:
            try:self._holder.rollback()
            except Exception:pass
            try:self._holder.close()
            except Exception:pass
        if self._thread and self._thread is not threading.current_thread():self._thread.join(timeout=4)
        if self._waiter is not None:
            try:self._waiter.rollback()
            except Exception:pass
            try:self._waiter.close()
            except Exception:pass
        try:self._stack.close()
        except Exception:pass
        self._holder=self._waiter=self._monitor=None
        return self.verify_idle()

    def verify_idle(self):
        try:
            _container_guard(self.cfg)
            with _connect(self.cfg,'monitor') as monitor:
                _marker(monitor,self.cfg)
                return _idle(monitor,self.cfg)
        except Exception:
            return {'sessions_closed':False,'locks_cleared':False,'dataset_unchanged':False,'error_code':'cleanup_unverified','observed_at':utc()}
