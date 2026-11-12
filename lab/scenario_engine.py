"""One owner, one desired switch, leased workloads. Never schedules AI."""
from copy import deepcopy
from datetime import datetime, timezone, timedelta
import json
import os
from pathlib import Path
import threading
import time
import uuid

import httpx
from .scenario_catalog import SCENARIOS, public_catalog


def utc():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


class RevisionConflict(ValueError):
    pass


class SingleOwner:
    def __init__(self,path):self.path=Path(path);self.file=None
    def acquire(self):
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.file=self.path.open('a+b');self.file.seek(0);self.file.write(b'0');self.file.flush();self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            self.file.close();self.file=None
            raise RuntimeError('scenario_controller_already_running') from None
    def release(self):
        if self.file:
            self.file.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_UNLCK,1)
            self.file.close();self.file=None


class Controller:
    def __init__(self,config,client=None):
        self.config=config;self.client=client or httpx.Client(timeout=4,trust_env=False)
        self.owns_client=client is None
        self.lock=threading.RLock();self.stop=threading.Event();self.thread=None
        self.owner=uuid.uuid4().hex;self.guard=SingleOwner(config['owner_lock'])
        self.generation_id=uuid.uuid4().hex
        self.desired_key='query-efficiency';self.desired_on=False;self.revision=0
        self.control_deadline=None;self.control_deadline_at=None;self.lease_expired_revision=None
        self.validation_id=None
        self.active=None;self.actual_state='off';self.error=None;self.platform=None;self.platform_at=None
        self.updated_at=utc();self.last_snapshot=0
        self.target_caches={};self.target_cache_lock=threading.RLock()

    def row(self,key):
        row=next((r for r in SCENARIOS if r['key']==key),None)
        if not row or row['status']=='not_implemented' or row['service'] not in self.config['services']:
            raise ValueError('scenario_unavailable')
        return row

    def target_row(self,target_key):
        row=next((r for r in SCENARIOS if r['target_key']==target_key and r['service'] in self.config['services']),None)
        if row is None:raise ValueError('target_unavailable')
        return row

    def target_snapshot(self,target_key):
        self.target_row(target_key)
        # A small read-through cache bounds public read traffic. It never refreshes
        # evidence timestamps or switches the actual workload's selected target.
        with self.target_cache_lock:
            cached=self.target_caches.get(target_key)
            if cached and time.monotonic()-cached['cached_at']<3:
                return deepcopy(cached['value'])
            b=self.config['platform'];token=Path(b['token_path']).read_text().strip()
            try:
                value=self.request('GET',b['url'].rstrip('/')+'/api/v1/showcase/targets/'+target_key+'/snapshot',headers={'Authorization':'Bearer '+token})
                if value.get('target',{}).get('key')!=target_key:raise ValueError('unexpected_target_projection')
            except Exception:
                self.target_caches.pop(target_key,None);raise
            projected={'target_key':target_key,'platform':value,'platform_received_at':utc()}
            self.target_caches[target_key]={'cached_at':time.monotonic(),'value':projected}
            return deepcopy(projected)

    def set_desired(self,on,key=None,expected_revision=None,expected_generation=None,lease_seconds=None,
                    validation_id=None,expected_validation_id=None):
        if type(on) is not bool:raise ValueError('invalid_switch')
        if lease_seconds is not None and (not on or type(lease_seconds) is not int or not 1<=lease_seconds<=3600):
            raise ValueError('invalid_control_lease')
        if validation_id is not None and (not on or lease_seconds is None or not isinstance(validation_id,str) or len(validation_id)!=32):
            raise ValueError('invalid_validation_id')
        key=key or self.desired_key;self.row(key)
        with self.lock:
            if ((expected_revision is not None and expected_revision!=self.revision) or
                    (expected_generation is not None and expected_generation!=self.generation_id) or
                    (expected_validation_id is not None and expected_validation_id!=self.validation_id)):
                raise RevisionConflict('revision_conflict')
            if (on,key)!=(self.desired_on,self.desired_key) or lease_seconds is not None:
                if key!=self.desired_key:
                    self.platform=None;self.platform_at=None;self.last_snapshot=0
                self.desired_on,self.desired_key=on,key;self.revision+=1;self.updated_at=utc()
                self.lease_expired_revision=None
                if on:self.validation_id=validation_id
                elif expected_validation_id is None:self.validation_id=None
                self.control_deadline=time.monotonic()+lease_seconds if on and lease_seconds else None
                self.control_deadline_at=(datetime.now(timezone.utc)+timedelta(seconds=lease_seconds)).isoformat().replace('+00:00','Z') if on and lease_seconds else None
        return self.state()

    def request(self,method,url,**kwargs):
        response=self.client.request(method,url,**kwargs)
        response.raise_for_status();return response.json()

    def app_request(self,row,method,payload=None):
        service=self.config['services'][row['service']]
        return self.request(method,service['url'].rstrip('/')+'/control',
            headers={'Authorization':'Bearer '+service['token']},**({'json':payload} if payload is not None else {}))

    def pacing(self,row,on):
        b=self.config['platform'];token=Path(b['token_path']).read_text().strip()
        return self.request('PUT',b['url'].rstrip('/')+'/api/v1/showcase/targets/'+row['target_key']+'/pacing',
            headers={'Authorization':'Bearer '+token},json={'enabled':on})

    def clear(self,row):
        self.app_request(row,'PUT',{'mode':'normal','owner':self.owner,'ttl':1})
        result=self.app_request(row,'GET')
        if not result['normal'] or result['held_connections'] or result['held_read_view']:
            return False
        # Pacing restore failure cannot keep a workload alive; B's lease expires.
        try:self.pacing(row,False)
        except (httpx.HTTPError,OSError):pass
        return True

    def tick(self):
        with self.lock:
            if self.desired_on and self.control_deadline is not None and time.monotonic()>=self.control_deadline:
                self.desired_on=False;self.revision+=1;self.updated_at=utc()
                self.lease_expired_revision=self.revision
                self.control_deadline=None;self.control_deadline_at=None
            on,key,revision=self.desired_on,self.desired_key,self.revision
        row=self.row(key)
        try:
            if self.active and (not on or self.active['key']!=key):
                self.actual_state='stopping'
                if not self.clear(self.active):return
                self.active=None
            if on:
                # Request a real ordinary-pipeline cadence lease before load.
                if not self.current(revision,key):return
                self.pacing(row,True)
                if not self.current(revision,key):return
                self.app_request(row,'PUT',{'mode':row['mode'],'owner':self.owner,'ttl':12})
                self.active=row;self.actual_state='on'
                if not self.current(revision,key):
                    self.actual_state='stopping'
                    if self.clear(row):self.active=None;self.actual_state='off'
                    return
            else:self.actual_state='off'
            self.error=None
        except (httpx.HTTPError,OSError,ValueError,KeyError):
            self.error='workload_or_platform_unavailable';self.actual_state='stopping' if self.active else 'unavailable'
            # No lease renewal on failure; app fails safe within twelve seconds.
        now=time.monotonic()
        if now-self.last_snapshot>=60:
            self.last_snapshot=now
            try:
                b=self.config['platform'];token=Path(b['token_path']).read_text().strip()
                value=self.request('GET',b['url'].rstrip('/')+'/api/v1/showcase/targets/'+row['target_key']+'/snapshot',headers={'Authorization':'Bearer '+token})
                with self.lock:
                    if self.desired_key==key and value.get('target',{}).get('key')==row['target_key']:
                        self.platform=value;self.platform_at=utc()
            except (httpx.HTTPError,OSError):
                with self.lock:self.platform=None;self.platform_at=None

    def current(self,revision,key):
        with self.lock:
            return not self.stop.is_set() and self.desired_on and self.revision==revision and self.desired_key==key

    def run(self):
        while not self.stop.is_set():self.tick();self.stop.wait(1)

    def start(self):
        self.guard.acquire()
        self.thread=threading.Thread(target=self.run,daemon=True);self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=30)
            if self.thread.is_alive():
                # Never let a second owner start while an old request can renew.
                raise RuntimeError('controller_shutdown_incomplete_owner_lock_retained')
        try:
            if self.active:
                try:self.clear(self.active)
                except (httpx.HTTPError,OSError):pass
        finally:
            if self.owns_client:self.client.close()
            self.guard.release()

    def state(self):
        with self.lock:return {'desired_on':self.desired_on,'scenario':self.desired_key,'revision':self.revision,
            'generation_id':self.generation_id,'control_lease_expires_at':self.control_deadline_at,
            'lease_expired_revision':self.lease_expired_revision,'validation_id':self.validation_id,
            'actual_state':self.actual_state,'error_code':self.error,'updated_at':self.updated_at,
            'platform':deepcopy(self.platform),'platform_received_at':self.platform_at,
            'platform_url':self.config.get('platform_public_url'),'scenarios':public_catalog(self.config)}

    def direct_state(self,target_key=None):
        with self.lock:
            key,revision,generation=self.desired_key,self.revision,self.generation_id
        row=self.target_row(target_key) if target_key else self.row(key)
        value=self.app_request(row,'GET')
        control={k:value[k] for k in ('normal','held_connections','held_read_view')}
        if (type(control['normal']) is not bool or type(control['held_read_view']) is not bool or
                type(control['held_connections']) is not int or control['held_connections']<0):
            raise ValueError('invalid_direct_measurement')
        with self.lock:
            return {'revision':revision,'generation_id':generation,'scenario':key,'target_key':row['target_key'],
                    'observed_at':utc(),'control':control,
                    'current':revision==self.revision and key==self.desired_key}


def load_config():
    p=Path(os.environ['SCENARIO_SITE_CONFIG'])
    if not p.is_absolute():raise ValueError('absolute_private_config_required')
    return json.loads(p.read_text(encoding='utf-8-sig'))
