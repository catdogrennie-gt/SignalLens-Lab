"""One durable fault switch and a separate fixed-scope platform bridge."""
from datetime import datetime,timezone
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from urllib.parse import urlsplit

import httpx

from lab.toggle_target import FixedToggleFault, TargetError


def utc():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def _private_path(value):
    path=Path(value)
    if not path.is_absolute():raise ValueError('private_absolute_path_required')
    path=path.resolve()
    if any((parent/'.git').exists() for parent in (path,*path.parents)):
        raise ValueError('repository_private_path_forbidden')
    return str(path)


def _origin(value):
    p=urlsplit(value)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.query or p.fragment or p.path not in ('','/'):
        raise ValueError('invalid_origin')
    return value.rstrip('/')


def load_config(path=None):
    try:
        path=Path(path or os.environ['LAB_SHOWCASE_CONFIG'])
        cfg=json.loads(path.read_text(encoding='utf-8-sig'))
        cfg['public_origin']=_origin(cfg['public_origin'])
        cfg['allowed_origins']=[_origin(value) for value in cfg.get('allowed_origins',[])]
        for field in ('state_path','target_config_path'):cfg[field]=_private_path(cfg[field])
        platform=cfg['platform'];base=urlsplit(platform['api_url'])
        if (base.scheme not in ('http','https') or not base.hostname or base.username or base.password
                or base.query or base.fragment or base.path.rstrip('/')!='/api/v1/showcase'):
            raise ValueError('invalid_platform_api')
        platform['api_url']=platform['api_url'].rstrip('/')
        platform['token_path']=_private_path(platform['token_path'])
        if platform.get('public_url'):platform['public_url']=_origin(platform['public_url'])
        return cfg
    except (OSError,ValueError,KeyError,TypeError):
        raise ValueError('showcase_configuration_invalid') from None


class StateStore:
    def __init__(self,path):
        self.path=Path(path)
        self.path.parent.mkdir(parents=True,exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK(id=1), desired_on INTEGER NOT NULL, revision INTEGER NOT NULL, actual_state TEXT NOT NULL, updated_at TEXT NOT NULL, error_code TEXT)')
            columns={row['name'] for row in db.execute('PRAGMA table_info(state)')}
            for name in ('stop_revision','applied_stop_revision'):
                if name not in columns:db.execute('ALTER TABLE state ADD COLUMN '+name+' INTEGER NOT NULL DEFAULT 0')
            db.execute('INSERT OR IGNORE INTO state (id,desired_on,revision,actual_state,updated_at,error_code) VALUES (1,0,0,?,?,NULL)',('off',utc()))

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path,timeout=3)
        connection.row_factory=sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:connection.close()

    def read(self):
        with self.connect() as db:row=dict(db.execute('SELECT * FROM state WHERE id=1').fetchone())
        row.pop('id');row['desired_on']=bool(row['desired_on'])
        return row

    def desired(self,on):
        if type(on) is not bool:raise ValueError('boolean_required')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            if on:
                db.execute('UPDATE state SET desired_on=1,revision=revision+1,updated_at=? WHERE id=1 AND desired_on=0',(utc(),))
            else:
                # An accepted OFF is a durable rollback barrier. A later ON may
                # change the desired state but cannot erase this obligation.
                db.execute('UPDATE state SET desired_on=0,stop_revision=revision+1,revision=revision+1,updated_at=? WHERE id=1',(utc(),))
        return self.read()

    def acknowledge_stop(self,revision):
        with self.connect() as db:
            db.execute('UPDATE state SET applied_stop_revision=MAX(applied_stop_revision,?) WHERE id=1',(revision,))

    def actual(self,state,error=None):
        with self.connect() as db:
            db.execute('UPDATE state SET actual_state=?,updated_at=?,error_code=? WHERE id=1',(state,utc(),error))

    def reset(self):
        with self.connect() as db:
            db.execute("UPDATE state SET desired_on=0,stop_revision=revision+1,revision=revision+1,actual_state='unknown',updated_at=?,error_code='startup_verification_pending' WHERE id=1",(utc(),))


class ExclusiveController:
    def __init__(self,path):self.path=Path(str(path)+'.lock');self.stream=None
    def acquire(self):
        stream=self.path.open('a+b')
        try:
            stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            self.stream=stream
        except OSError:
            stream.close()
            raise RuntimeError('controller_already_running') from None
    def release(self):
        if self.stream:
            self.stream.close();self.stream=None


class PlatformClient:
    def __init__(self,cfg,transport=None):
        token=Path(cfg['token_path']).read_text(encoding='utf-8-sig').strip()
        if len(token)<16:raise ValueError('platform_token_invalid')
        self.client=httpx.Client(base_url=cfg['api_url']+'/',headers={'Authorization':'Bearer '+token},
                                 timeout=3,trust_env=False,follow_redirects=False,transport=transport)
    def _request(self,method,path,**kwargs):
        response=self.client.request(method,path,**kwargs)
        if response.status_code!=200:raise RuntimeError('platform_unavailable')
        if len(response.content)>1048576:raise RuntimeError('platform_response_too_large')
        result=response.json()
        if not isinstance(result,dict):raise RuntimeError('platform_response_invalid')
        return result
    def snapshot(self):return self._request('GET','snapshot')
    def pacing(self,enabled):return self._request('PUT','pacing',json={'enabled':enabled})
    def close(self):self.client.close()


class Controller:
    def __init__(self,config,*,fault_factory=None,platform_client=None):
        self.config=config
        self.store=StateStore(config['state_path'])
        self.exclusive=ExclusiveController(config['state_path'])
        self.fault_factory=fault_factory or (lambda:FixedToggleFault(config['target_config_path']))
        self.platform=platform_client or PlatformClient(config['platform'])
        self._fault=None
        self._needs_verification=False
        self._next_heartbeat=0
        self._retry_after=0
        self._exit=threading.Event();self._wake=threading.Event();self._platform_wake=threading.Event()
        self._threads=[];self._bridge_lock=threading.Lock()
        self._snapshot=None;self._platform_updated_at=None;self._platform_error=None
        self._platform_received=0
        self._pacing_intent=None;self._pacing_due=0
        self._started=False

    def state(self):
        state=self.store.read()
        with self._bridge_lock:
            state.update(platform=self._snapshot,platform_updated_at=self._platform_updated_at,
                         last_received_at=self._platform_updated_at,
                         platform_stale=(self._snapshot is None or self._platform_error is not None or time.monotonic()-self._platform_received>10),
                         platform_error_code=self._platform_error)
        state['platform_url']=self.config['platform'].get('public_url') or None
        return state

    def set_desired(self,on):
        self.store.desired(on)
        self._wake.set();self._platform_wake.set()
        return self.state()

    def _release_fault(self):
        if self._fault is None:return
        self.store.actual('stopping')
        fault=self._fault;self._fault=None
        try:
            clean=fault.stop()
            okay=all(clean.get(k) is True for k in ('sessions_closed','locks_cleared','dataset_unchanged'))
            self._needs_verification=not okay
            self.store.actual('off' if okay else 'unknown',None if okay else 'cleanup_unverified')
        except Exception:
            self._needs_verification=True
            self.store.actual('unknown','cleanup_unverified')
        self._retry_after=0

    def step(self):
        state=self.store.read()
        released_for_stop=False
        if state['stop_revision']>state['applied_stop_revision']:
            released_for_stop=self._fault is not None
            self._release_fault()
            # Only acknowledge the barrier read before rollback. If another OFF
            # arrived during IO, do not accidentally consume its newer revision.
            self.store.acknowledge_stop(state['stop_revision'])
            self._retry_after=0
            latest=self.store.read()
            if latest['stop_revision']>latest['applied_stop_revision']:return
            state=latest
        desired=state['desired_on']
        if not desired:
            if released_for_stop:return
            if self._fault is not None:
                self._release_fault()
            elif self._needs_verification and time.monotonic()>=self._retry_after:
                try:
                    clean=self.fault_factory().verify_idle()
                    okay=all(clean.get(k) is True for k in ('sessions_closed','locks_cleared','dataset_unchanged'))
                    self._needs_verification=not okay
                    self.store.actual('off' if okay else 'unknown',None if okay else 'cleanup_unverified')
                except Exception:self.store.actual('unknown','cleanup_unverified')
                self._retry_after=time.monotonic()+2
            return
        if self._fault is None:
            if time.monotonic()<self._retry_after:return
            self.store.actual('starting')
            try:
                fault=self.fault_factory()
                self._fault=fault
                fault.start()
                self._next_heartbeat=time.monotonic()+5
                self._needs_verification=False
                self._retry_after=0
                self.store.actual('on')
            except Exception as exc:
                if self._fault is not None:
                    try:self._fault.stop()
                    except Exception:pass
                self._fault=None
                self._retry_after=time.monotonic()+2
                self.store.actual('unknown',exc.code if isinstance(exc,TargetError) else 'target_unavailable')
        elif time.monotonic()>=self._next_heartbeat:
            try:
                sample=self._fault.heartbeat()
                if sample.get('active') is not True:raise TargetError('fault_connection_lost')
                self._next_heartbeat=time.monotonic()+5
            except Exception:
                self._release_fault()
                self._retry_after=time.monotonic()+2
                self.store.actual('unknown' if self._needs_verification else 'off','fault_connection_lost')

    def platform_step(self):
        desired=self.store.read()['desired_on'];now=time.monotonic()
        try:
            if desired!=self._pacing_intent or (desired and now>=self._pacing_due):
                self.platform.pacing(desired)
                self._pacing_intent=desired;self._pacing_due=now+10
            snapshot=self.platform.snapshot()
            with self._bridge_lock:
                self._snapshot=snapshot;self._platform_updated_at=utc();self._platform_received=time.monotonic();self._platform_error=None
        except Exception:
            with self._bridge_lock:self._platform_error='platform_unavailable'

    def start(self):
        if self._started:return
        self.exclusive.acquire()
        try:
            self.store.reset()
            self._needs_verification=True
            self._started=True
            def fault_loop():
                while not self._exit.is_set():
                    self._wake.clear()
                    try:self.step()
                    except Exception:self.store.actual('unknown','controller_unavailable')
                    self._wake.wait(.2)
                if self._fault is not None:
                    try:self._fault.stop()
                    except Exception:pass
                    self._fault=None
            def bridge_loop():
                while not self._exit.is_set():
                    self._platform_wake.clear();self.platform_step();self._platform_wake.wait(3)
                try:self.platform.pacing(False)
                except Exception:pass
            for name,run in [('lab-fault-controller',fault_loop),('lab-platform-proxy',bridge_loop)]:
                thread=threading.Thread(target=run,name=name,daemon=True);thread.start();self._threads.append(thread)
        except Exception:
            self.exclusive.release();self._started=False;raise

    def close(self):
        self.store.desired(False)
        self._exit.set();self._wake.set();self._platform_wake.set()
        for thread in self._threads:thread.join(timeout=20)
        if any(thread.is_alive() for thread in self._threads):
            # Keep the process lock until its actual owner is dead; never hand
            # ownership to a second controller while a slow rollback is running.
            return
        self.platform.close();self.exclusive.release();self._started=False
