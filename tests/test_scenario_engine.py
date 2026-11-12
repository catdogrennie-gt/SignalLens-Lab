import json
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from lab.business_app import Workload
from lab.scenario_engine import Controller, SingleOwner
from lab.scenario_site import create_app
from lab.scenario_catalog import public_catalog


def configured(tmp_path,fail=False):
    token=tmp_path/'token';token.write_text('x'*40)
    calls=[]; active={}
    def handle(r):
        calls.append((r.method,str(r.url),json.loads(r.content) if r.content else None))
        if fail and '/pacing' in r.url.path:return httpx.Response(503)
        if r.url.path=='/control':
            if r.method=='PUT':active[r.url.host]=json.loads(r.content)['mode'];return httpx.Response(200,json={'accepted':True})
            return httpx.Response(200,json={'normal':active.get(r.url.host,'normal')=='normal','held_connections':0,'held_read_view':False})
        return httpx.Response(200,json={'target':{'name':'svc-a','key':r.url.path.split('/')[-2]},'charts':[]})
    config={'owner_lock':str(tmp_path/'owner.lock'),'public_origin':'http://testserver',
        'services':{'svc-a':{'url':'http://app-a','token':'a'},'svc-b':{'url':'http://app-b','token':'b'}},
        'platform':{'url':'http://platform','token_path':str(token)}}
    return Controller(config,httpx.Client(transport=httpx.MockTransport(handle))),calls,active


def test_repeated_on_does_not_allocate_another_owner_or_stack_work(tmp_path):
    c,calls,active=configured(tmp_path)
    c.set_desired(True);c.tick();owner=c.owner
    c.set_desired(True);c.tick()
    assert c.revision==1 and c.owner==owner and active=={'app-a':'query'}
    assert all('/investigation' not in url and '/run' not in url for _,url,_ in calls)


def test_switch_clears_old_before_new_service_lease(tmp_path):
    c,calls,active=configured(tmp_path)
    c.set_desired(True);c.tick();calls.clear()
    c.set_desired(True,'temporary-work');c.tick()
    controls=[(url,body['mode']) for method,url,body in calls if method=='PUT' and url.endswith('/control')]
    assert controls==[('http://app-a/control','normal'),('http://app-b/control','temporary')]
    assert active=={'app-a':'normal','app-b':'temporary'}


def test_rapid_off_on_reconciles_latest_without_more_owners(tmp_path):
    c,calls,active=configured(tmp_path)
    c.set_desired(True);c.tick();c.set_desired(False);c.set_desired(True);c.tick()
    assert c.revision==3 and active=={'app-a':'query'}
    c.set_desired(False);c.tick()
    assert c.actual_state=='off' and c.active is None and active['app-a']=='normal'


def test_platform_failure_does_not_start_fault(tmp_path):
    c,calls,active=configured(tmp_path,fail=True)
    c.set_desired(True);c.tick()
    assert not active and c.error and c.actual_state=='unavailable'


def test_unimplemented_or_unconfigured_target_rejected(tmp_path):
    c,_,_=configured(tmp_path)
    with pytest.raises(ValueError):c.set_desired(True,'stale-replica')
    with pytest.raises(ValueError):c.set_desired(True,'arbitrary')
    assert c.revision==0


def test_single_owner_lock_is_real_process_exclusive(tmp_path):
    a,b=SingleOwner(tmp_path/'same'),SingleOwner(tmp_path/'same')
    a.acquire()
    try:
        with pytest.raises(RuntimeError):b.acquire()
    finally:a.release()
    b.acquire();b.release()


def test_workload_lease_expires_and_does_not_export_answer(monkeypatch):
    now=[100.]
    monkeypatch.setattr(time,'monotonic',lambda:now[0])
    w=Workload({});w.control('query','owner-1234567890123',ttl=12)
    assert w.current_mode()=='query'
    metrics=w.metrics()
    assert not {'mode','owner','scenario','expected_root_cause'}.intersection(metrics)
    now[0]=113
    assert w.current_mode()=='normal'


def test_other_owner_cannot_cancel_active_workload():
    w=Workload({});w.control('query','owner-1234567890123')
    with pytest.raises(ValueError):w.control('normal','other-1234567890123')


def test_site_requires_origin_and_exact_switch_fields(tmp_path):
    c,_,_=configured(tmp_path)
    with TestClient(create_app(c.config,c,start_controller=False)) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/state').json()['desired_on'] is False
        assert client.put('/api/switch',json={'on':True}).status_code==403
        assert client.put('/api/switch',headers={'Origin':'http://testserver'},json={'on':'true'}).status_code==422
        assert client.put('/api/switch',headers={'Origin':'http://testserver'},json={'on':True,'sql':'SELECT 1'}).status_code==422
        assert client.put('/api/switch',headers={'Origin':'http://testserver'},json={'on':True}).status_code==200


def test_unavailable_snapshot_is_not_stale_success(tmp_path):
    c,_,_=configured(tmp_path);c.tick();assert c.platform
    c.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(503)))
    c.last_snapshot=0;c.tick()
    assert c.platform is None and c.platform_at is None


def test_acceptance_status_does_not_claim_platform_success_from_a_switch(tmp_path):
    c,_,_=configured(tmp_path)
    proof=tmp_path/'verification.json';c.config['verification_path']=str(proof)
    proof.write_text(json.dumps({'query-efficiency':{'status':'direct_verified'}}))
    c.set_desired(True);c.tick()
    assert public_catalog(c.config)[0]['status']=='direct_verified'
    proof.write_text(json.dumps({'query-efficiency':{'status':'platform_verified'}}))
    assert public_catalog(c.config)[0]['status']=='platform_verified'
    proof.write_text('invalid')
    assert public_catalog(c.config)[0]['status']=='implemented_awaiting_verification'


def test_switch_clears_previous_target_report_immediately_and_fences_inflight_snapshot(tmp_path):
    c,_,_=configured(tmp_path);c.tick();assert c.platform
    c.set_desired(False,'temporary-work')
    assert c.state()['platform'] is None and c.platform_at is None
    def old_response(request):
        c.set_desired(False,'query-efficiency')
        return httpx.Response(200,json={'target':{'key':'svc-b'},'report_matches_current_observation':True})
    c.client=httpx.Client(transport=httpx.MockTransport(old_response));c.tick()
    assert c.platform is None


def test_off_during_pacing_does_not_send_a_later_on_lease(tmp_path):
    c,_,_=configured(tmp_path);calls=[]
    def handler(request):
        calls.append(str(request.url))
        if request.url.path.endswith('/pacing'):c.set_desired(False)
        return httpx.Response(200,json={})
    c.client=httpx.Client(transport=httpx.MockTransport(handler));c.set_desired(True);c.tick()
    assert not any(url.endswith('/control') for url in calls)
    assert c.active is None


def test_shutdown_retains_owner_lock_until_network_thread_has_ended(tmp_path):
    c,_,_=configured(tmp_path);c.guard.acquire()
    class StillRunning:
        def join(self,timeout):pass
        def is_alive(self):return True
    c.thread=StillRunning();other=SingleOwner(c.config['owner_lock'])
    try:
        with pytest.raises(RuntimeError,match='lock_retained'):c.close()
        with pytest.raises(RuntimeError):other.acquire()
    finally:c.thread=None;c.guard.release()
    other.acquire();other.release()


@pytest.mark.parametrize('query_fails',[False,True])
def test_temporary_query_restores_session_settings_even_after_query_failure(query_fails):
    class Cursor:
        def __init__(self):self.statements=[]
        def execute(self,sql):
            self.statements.append(sql)
            if query_fails and sql.startswith('SELECT'):raise ValueError('query_failed')
        def fetchall(self):return []
    cur=Cursor()
    if query_fails:
        with pytest.raises(ValueError):Workload.execute_query(cur,'temporary')
    else:Workload.execute_query(cur,'temporary')
    assert cur.statements[-2:]==['SET SESSION tmp_table_size=DEFAULT','SET SESSION max_heap_table_size=DEFAULT']


def test_long_view_write_budget_does_not_end_owned_snapshot(monkeypatch):
    statements=[]
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql):statements.append(sql)
        def fetchone(self):return {'version':0}
    class Connection:
        def cursor(self):return Cursor()
        def rollback(self):pass
        def close(self):pass
    class Stop:
        n=0
        def wait(self,seconds):self.n+=1;return self.n>650
    work=Workload({});work.stop=Stop()
    monkeypatch.setattr(work,'connect',lambda:Connection())
    monkeypatch.setattr(work,'current_mode',lambda:'view')
    work.maintain()
    assert sum(s.startswith('UPDATE ledger') for s in statements)==600
    assert sum(s.startswith('SELECT version') for s in statements)==651
    assert work.view is None and work.writer is None


def test_completed_request_rate_uses_observed_uptime_before_full_window(monkeypatch):
    now=[100.];monkeypatch.setattr(time,'monotonic',lambda:now[0])
    work=Workload({});now[0]=105.
    work.samples.append({'at':104.,'request_ms':2.,'db_ms':1.,'pool_wait_ms':0.,'failed':False})
    assert work.metrics()['request_rate']==.2


def test_conditional_validation_switch_cannot_overwrite_another_revision(tmp_path):
    c,_,_=configured(tmp_path)
    initial=c.state()
    c.set_desired(True,expected_revision=0,expected_generation=initial['generation_id'],lease_seconds=20)
    c.set_desired(True,'temporary-work')
    with pytest.raises(ValueError,match='revision_conflict'):
        c.set_desired(False,expected_revision=1,expected_generation=initial['generation_id'])
    assert c.desired_on and c.desired_key=='temporary-work'


def test_validation_lease_expires_without_imposing_limits_on_manual_switch(tmp_path,monkeypatch):
    now=[100.];monkeypatch.setattr(time,'monotonic',lambda:now[0])
    c,_,_=configured(tmp_path);c.set_desired(True,lease_seconds=5);c.tick()
    now[0]=106.;c.tick()
    assert c.desired_on is False and c.actual_state=='off'
    assert c.state()['lease_expired_revision']==2
    c.set_desired(True);now[0]=100000.;c.tick()
    assert c.desired_on and c.actual_state=='on'


def test_readonly_control_projection_contains_actual_cleanup_and_no_secrets(tmp_path):
    c,_,_=configured(tmp_path)
    with TestClient(create_app(c.config,c,start_controller=False)) as client:
        r=client.get('/api/control-state');assert r.status_code==200
        value=r.json();assert value['control']=={'normal':True,'held_connections':0,'held_read_view':False}
        assert value['current'] is True
        assert not {'token','owner','mode','url','password'}.intersection(value)


def test_lost_ack_conditional_off_requires_matching_validation_id(tmp_path):
    c,_,_=configured(tmp_path);g=c.state()['generation_id']
    c.set_desired(True,expected_revision=0,expected_generation=g,lease_seconds=30,validation_id='a'*32)
    with pytest.raises(ValueError,match='revision_conflict'):
        c.set_desired(False,expected_revision=1,expected_generation=g,expected_validation_id='b'*32)
    assert c.desired_on
    c.set_desired(False,expected_revision=1,expected_generation=g,expected_validation_id='a'*32)
    assert not c.desired_on and c.state()['validation_id']=='a'*32


def test_site_compare_and_swap_conflict_is_409_without_mutating_switch(tmp_path):
    c,_,_=configured(tmp_path)
    with TestClient(create_app(c.config,c,start_controller=False)) as client:
        r=client.put('/api/switch',headers={'Origin':'http://testserver'},json={'on':True,'expected_revision':99,'expected_generation':c.generation_id})
        assert r.status_code==409 and c.desired_on is False
        assert client.put('/api/switch',headers={'Origin':'http://testserver'},json={'on':True,'lease_seconds':True}).status_code==422


def test_direct_control_unavailable_returns_error_not_clean_zero_values(tmp_path):
    c,_,_=configured(tmp_path);c.client=httpx.Client(transport=httpx.MockTransport(lambda r:httpx.Response(503)))
    with TestClient(create_app(c.config,c,start_controller=False)) as client:
        r=client.get('/api/control-state')
        assert r.status_code==503 and r.json()=={'error':'direct_state_unavailable'}


def test_browsing_fixed_other_target_never_changes_global_switch_or_selection(tmp_path):
    c,calls,_=configured(tmp_path)
    with TestClient(create_app(c.config,c,start_controller=False)) as client:
        before=c.state()
        r=client.get('/api/targets/svc-b/snapshot');assert r.status_code==200
        assert r.json()['platform']['target']['key']=='svc-b'
        assert client.get('/api/targets/svc-b/control-state').json()['control']['normal']
        assert client.get('/api/targets/arbitrary/snapshot').status_code==422
        after=c.state()
        assert (before['revision'],before['scenario'],before['desired_on'])==(after['revision'],after['scenario'],after['desired_on'])
        assert all(method=='GET' for method,_,_ in calls)


def test_verified_value_explanation_is_separate_from_model_data_and_links_are_scoped(tmp_path):
    c,_,_=configured(tmp_path);p=tmp_path/'acceptance.json';c.config.update(verification_path=str(p),platform_public_url='https://b.test')
    proof={'query-efficiency':{'status':'platform_verified','business_symptom':'Observed symptom',
        'zabbix_comparison':'Actual alert comparison','platform_clues':'Actual B clues',
        'validation_evidence':{'report_id':'report-1','report_url':'https://b.test/reports/report-1',
            'comparison_kind':'not_triggered_in_this_window','measurements':[{'label':'Rows','before':20,'during':4799,'after':20,'unit':'rows'}]}}}
    p.write_text(json.dumps(proof));row=public_catalog(c.config)[0]
    assert row['business_symptom']=='Observed symptom' and row['validation_evidence']['report_url']=='https://b.test/reports/report-1'
    assert row['validation_evidence']['measurements'][0]['during']==4799
    proof['query-efficiency']['validation_evidence']['report_url']='javascript:alert(1)';p.write_text(json.dumps(proof))
    assert 'report_url' not in public_catalog(c.config)[0]['validation_evidence']
    p.write_text('[]');assert public_catalog(c.config)[0]['status']=='implemented_awaiting_verification'
