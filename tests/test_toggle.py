import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient

from lab.toggle_controller import Controller, StateStore, PlatformClient, load_config
from lab.toggle_web import create_app
from lab.toggle_target import TargetError, FixedToggleFault, validate_container, load_target, HOLDER_LEASE_SECONDS, SQL


class FakeFault:
    def __init__(self): self.starts=0; self.stops=0; self.beats=0
    def start(self): self.starts+=1; return {'active':True}
    def heartbeat(self): self.beats+=1; return {'active':True}
    def stop(self):
        self.stops+=1
        return {'sessions_closed':True,'locks_cleared':True,'dataset_unchanged':True}
    def verify_idle(self):return {'sessions_closed':True,'locks_cleared':True,'dataset_unchanged':True}


class FakePlatform:
    def __init__(self): self.enabled=[]
    def pacing(self, enabled): self.enabled.append(enabled); return {'pacing':{'active':enabled}}
    def snapshot(self): return {'observation':{'status':'normal'},'report_matches_current_observation':False}
    def close(self): pass


class ToggleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.cfg={'public_origin':'http://testserver','allowed_origins':[],
                  'state_path':str(self.root/'state.sqlite3'), 'target_config_path':str(self.root/'target.json'),
                  'platform':{'api_url':'http://127.0.0.1:18180/api/v1/showcase','token_path':str(self.root/'token'),
                              'public_url':'https://platform.example'}}
        self.fault=FakeFault()
        self.platform=FakePlatform()
        self.controller=Controller(self.cfg, fault_factory=lambda:self.fault, platform_client=self.platform)

    def test_concurrent_repeated_on_is_one_intent_and_one_fault(self):
        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:self.controller.set_desired(True),range(30)))
        first=self.controller.state()
        self.assertTrue(first['desired_on'])
        self.assertEqual(first['revision'],1)
        self.controller.step()
        for _ in range(10): self.controller.set_desired(True); self.controller.step()
        self.assertEqual(self.fault.starts,1)
        self.assertEqual(self.controller.state()['actual_state'],'on')

    def test_off_on_is_accepted_without_platform_or_recovery_gate(self):
        self.controller.set_desired(True); self.controller.step()
        self.controller.set_desired(False)
        self.assertFalse(self.controller.state()['desired_on'])
        self.controller.step()
        self.controller.set_desired(True); self.controller.step()
        self.assertEqual((self.fault.starts,self.fault.stops),(2,1))
        self.assertTrue(self.controller.state()['desired_on'])

    def test_fast_off_on_releases_existing_fault_before_one_new_fault(self):
        self.controller.set_desired(True); self.controller.step()
        self.controller.set_desired(False); self.controller.set_desired(True)
        self.controller.step()
        self.assertEqual((self.fault.starts,self.fault.stops),(2,1))

    def test_off_barrier_survives_another_api_store_and_later_on(self):
        self.controller.set_desired(True);self.controller.step()
        other_api=StateStore(self.cfg['state_path'])
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(other_api.desired,False).result()
            pool.submit(other_api.desired,True).result()
        persisted=StateStore(self.cfg['state_path']).read()
        self.assertGreater(persisted['stop_revision'],persisted['applied_stop_revision'])
        self.controller.step()
        state=self.controller.state()
        self.assertEqual((self.fault.starts,self.fault.stops),(2,1))
        self.assertEqual(state['stop_revision'],state['applied_stop_revision'])

    def test_new_off_during_rollback_is_not_acknowledged_by_old_barrier(self):
        self.controller.set_desired(True);self.controller.step()
        entered=threading.Event();release=threading.Event()
        original_stop=self.fault.stop
        def paused_stop():
            entered.set()
            self.assertTrue(release.wait(3))
            return original_stop()
        self.fault.stop=paused_stop
        self.controller.set_desired(False)
        with ThreadPoolExecutor(max_workers=1) as pool:
            step=pool.submit(self.controller.step)
            self.assertTrue(entered.wait(3))
            self.controller.set_desired(True)
            self.controller.set_desired(False)
            self.controller.set_desired(True)
            release.set();step.result(timeout=3)
        pending=self.controller.state()
        self.assertGreater(pending['stop_revision'],pending['applied_stop_revision'])
        self.assertEqual(self.fault.starts,1)
        self.controller.step()
        self.assertEqual((self.fault.starts,self.fault.stops),(2,1))

    def test_heartbeat_failure_cleanup_success_allows_off_without_stale_unknown(self):
        self.controller.set_desired(True);self.controller.step()
        self.fault.heartbeat=lambda:(_ for _ in ()).throw(OSError('connection lost'))
        self.controller._next_heartbeat=0
        self.controller.step()
        self.controller.set_desired(False);self.controller.step()
        self.assertEqual(self.controller.state()['actual_state'],'off')

    def test_heartbeat_failure_unknown_cleanup_is_rechecked_after_off(self):
        self.controller.set_desired(True);self.controller.step()
        self.fault.heartbeat=lambda:(_ for _ in ()).throw(OSError('connection lost'))
        self.fault.stop=lambda:{'sessions_closed':False,'locks_cleared':False,'dataset_unchanged':False}
        self.fault.verify_idle=lambda:{'sessions_closed':False,'locks_cleared':False,'dataset_unchanged':False}
        self.controller._next_heartbeat=0
        self.controller.step()
        self.controller.set_desired(False);self.controller.step()
        self.assertEqual(self.controller.state()['actual_state'],'unknown')
        self.fault.verify_idle=lambda:{'sessions_closed':True,'locks_cleared':True,'dataset_unchanged':True}
        self.controller._retry_after=0
        self.controller.step()
        self.assertEqual(self.controller.state()['actual_state'],'off')

    def test_platform_failure_never_blocks_off(self):
        self.controller.set_desired(True); self.controller.step()
        self.platform.snapshot=lambda:(_ for _ in ()).throw(OSError('private secret'))
        self.controller.platform_step()
        self.controller.set_desired(False); self.controller.step()
        state=self.controller.state()
        self.assertEqual(state['actual_state'],'off')
        self.assertEqual(self.fault.stops,1)
        self.assertNotIn('private secret',json.dumps(state))

    def test_platform_failure_retains_last_real_snapshot_and_marks_stale(self):
        self.controller.platform_step()
        before=self.controller.state()
        self.platform.snapshot=lambda:(_ for _ in ()).throw(OSError('secret'))
        self.controller.platform_step()
        after=self.controller.state()
        self.assertEqual(after['platform'],before['platform'])
        self.assertEqual(after['last_received_at'],before['last_received_at'])
        self.assertTrue(after['platform_stale'])
        self.assertFalse(before['platform_stale'])

    def test_platform_calls_only_fixed_snapshot_and_pacing(self):
        self.controller.set_desired(True)
        self.controller.platform_step()
        self.assertEqual(self.platform.enabled,[True])
        self.assertIn('observation',self.controller.state()['platform'])
        self.controller.set_desired(False); self.controller.platform_step()
        self.assertEqual(self.platform.enabled,[True,False])

    def test_failed_direct_cleanup_remains_truthful_but_switch_can_be_set(self):
        self.controller.set_desired(True); self.controller.step()
        self.fault.stop=lambda:{'sessions_closed':False,'locks_cleared':False,'dataset_unchanged':False}
        self.controller.set_desired(False); self.controller.step()
        self.assertEqual(self.controller.state()['actual_state'],'unknown')
        self.controller.set_desired(True)
        self.assertTrue(self.controller.state()['desired_on'])

    def test_restart_intent_is_off_and_only_one_controller(self):
        self.controller.set_desired(True)
        self.controller.start()
        self.addCleanup(self.controller.close)
        self.assertFalse(self.controller.state()['desired_on'])
        second=Controller(self.cfg,fault_factory=lambda:FakeFault(),platform_client=FakePlatform())
        with self.assertRaisesRegex(RuntimeError,'controller_already_running'): second.start()

    def test_restart_does_not_claim_old_lease_recovered_before_direct_check(self):
        self.controller.store.reset()
        self.assertEqual(self.controller.state()['actual_state'],'unknown')
        self.assertFalse(self.controller.state()['desired_on'])

    def test_web_is_anonymous_and_strictly_fixed_boolean(self):
        app=create_app(config=self.cfg,controller=self.controller,start_controller=False)
        with TestClient(app,base_url='http://testserver') as client:
            self.assertEqual(client.get('/api/state').status_code,200)
            for body in ({'on':1},{'on':'true'},{'on':True,'sql':'bad'},{'on':None},{}):
                self.assertEqual(client.put('/api/switch',json=body,headers={'Origin':'http://testserver'}).status_code,422)
            for headers in ({},{'Origin':'https://evil.example'},{'Origin':'null'}):
                self.assertEqual(client.put('/api/switch',json={'on':True},headers=headers).status_code,403)
            self.assertEqual(client.put('/api/switch?target=bad',json={'on':True},headers={'Origin':'http://testserver'}).status_code,422)
            response=client.put('/api/switch',json={'on':True},headers={'Origin':'http://testserver'})
            self.assertEqual(response.status_code,200)
            self.assertTrue(response.json()['desired_on'])
            self.assertEqual(client.get('/api/state?target=bad').status_code,422)

    def test_proxy_headers_never_change_allowed_origin_or_host(self):
        app=create_app(config=self.cfg,controller=self.controller,start_controller=False)
        with TestClient(app,base_url='http://testserver') as client:
            self.assertEqual(client.put('/api/switch',json={'on':True},headers={'Origin':'https://evil.example','X-Forwarded-Host':'evil.example'}).status_code,403)
            self.assertEqual(client.get('/api/state',headers={'Host':'evil.example'}).status_code,400)

    def test_platform_http_client_never_follows_redirect_or_exposes_token(self):
        import httpx
        (self.root/'token').write_text('private-token-not-public',encoding='utf-8')
        requests=[]
        def respond(request):
            requests.append(request)
            return httpx.Response(200,json={'pacing':{'active':True}})
        client=PlatformClient(self.cfg['platform'],transport=httpx.MockTransport(respond))
        self.addCleanup(client.close)
        client.snapshot();client.pacing(True)
        self.assertEqual([(r.method,r.url.path) for r in requests],[('GET','/api/v1/showcase/snapshot'),('PUT','/api/v1/showcase/pacing')])
        self.assertTrue(all(r.headers['authorization']=='Bearer private-token-not-public' for r in requests))
        self.assertEqual(json.loads(requests[1].content),{'enabled':True})

    def test_config_rejects_wildcard_origin_and_repository_private_state(self):
        self.cfg['allowed_origins']=['*']
        path=self.root/'config.json';path.write_text(json.dumps(self.cfg),encoding='utf-8')
        with self.assertRaises(ValueError):load_config(path)
        self.cfg['allowed_origins']=[]
        self.cfg['state_path']=str(Path(__file__).resolve().parents[1]/'.runtime/state.sqlite3')
        path.write_text(json.dumps(self.cfg),encoding='utf-8')
        with self.assertRaises(ValueError):load_config(path)


class TargetGuardTests(unittest.TestCase):
    def test_container_is_exact_isolated_target(self):
        cfg={'profile':{'db_host':'172.20.0.2'}}
        data={'Name':'/signallens-lab-target','State':{'Running':True},
              'Config':{'Labels':{'sl.owner':'signallens-lab','sl.role':'target','com.docker.compose.project':'signallens-lab'}},
              'HostConfig':{'NetworkMode':'signallens-lab_lab'},
              'NetworkSettings':{'Networks':{'signallens-lab_lab':{'IPAddress':'172.20.0.2'},'signallens-internal':{}},'Ports':{'3306/tcp':None}}}
        validate_container(data,cfg)
        data['NetworkSettings']['Ports']['3306/tcp']=[{'HostPort':'3306'}]
        with self.assertRaises(TargetError):validate_container(data,cfg)

    def test_target_configuration_errors_never_include_private_contents(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'target.json';path.write_text('{"password":"private secret"',encoding='utf-8')
            with self.assertRaises(TargetError) as caught:load_target(path)
            self.assertNotIn('private secret',str(caught.exception))


class FakeConnection:
    def __init__(self,name,events):self.name=name;self.events=events;self.last=None;self.closed=False
    def cursor(self):return self
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def execute(self,sql,args=None):
        self.last=sql;self.events.append((self.name,sql,args))
        if self.name=='waiter' and sql==SQL:
            time.sleep(.005)
            raise Exception(1205,'expected timeout')
    def fetchone(self):return {'id':1 if self.name=='holder' else 2,'n':1}
    def autocommit(self,value):self.events.append((self.name,'autocommit',value))
    def rollback(self):self.events.append((self.name,'rollback',None))
    def close(self):self.closed=True;self.events.append((self.name,'close',None))


class TargetLeaseTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.connections=[]
        @contextmanager
        def connect(cfg,identity):
            name='monitor' if identity=='monitor' else 'holder' if not any(c.name=='holder' for c in self.connections) else 'waiter'
            conn=FakeConnection(name,self.events);self.connections.append(conn)
            try:yield conn
            finally:conn.close()
        self.patches=[patch('lab.toggle_target.load_target',return_value={}),
                      patch('lab.toggle_target._container_guard'),patch('lab.toggle_target._marker'),
                      patch('lab.toggle_target._idle',return_value={'sessions_closed':True,'locks_cleared':True,'dataset_unchanged':True}),
                      patch('lab.toggle_target._connect',connect)]
        for p in self.patches:p.start();self.addCleanup(p.stop)

    def test_short_holder_lease_renews_exact_transaction_without_reconnect(self):
        fault=FixedToggleFault('unused')
        self.addCleanup(fault.stop)
        self.assertTrue(fault.start()['active'])
        self.assertEqual([c.name for c in self.connections],['monitor','holder','waiter'])
        self.assertIn(('holder','SET SESSION wait_timeout=%s',(HOLDER_LEASE_SECONDS,)),self.events)
        self.assertLessEqual(HOLDER_LEASE_SECONDS,15)
        fault.heartbeat()
        self.assertEqual([c.name for c in self.connections],['monitor','holder','waiter'])
        self.assertIn(('holder','SELECT 1',None),self.events)
        self.assertEqual(sum(name=='holder' and sql==SQL for name,sql,_ in self.events),1)
        self.assertFalse(any('COMMIT' in str(sql).upper() for _,sql,_ in self.events if sql!='autocommit'))

    def test_stop_releases_holder_first_and_closes_all_even_on_rollback_failure(self):
        fault=FixedToggleFault('unused');fault.start()
        holder=next(c for c in self.connections if c.name=='holder')
        holder.rollback=lambda:(_ for _ in ()).throw(OSError('disconnect'))
        result=fault.stop()
        self.assertTrue(all(result[k] for k in ('sessions_closed','locks_cleared','dataset_unchanged')))
        self.assertTrue(all(c.closed for c in self.connections))
        holder_close=self.events.index(('holder','close',None))
        waiter_close=self.events.index(('waiter','close',None))
        self.assertLess(holder_close,waiter_close)

    def test_guard_failure_does_not_open_scenario_connections(self):
        with patch('lab.toggle_target._container_guard',side_effect=TargetError('target_identity_mismatch')):
            fault=FixedToggleFault('unused')
            with self.assertRaises(TargetError):fault.start()
        self.assertFalse(self.connections)
