import json
import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def site(tmp_path):
    from lab.curated_site import create_app
    proof=tmp_path/'accepted.json'
    proof.write_text(json.dumps({'connection-headroom':{'status':'platform_verified','analysis_verified':True,
        'verified_stages':{k:True for k in ('reproduction','discovery','interpretation','display','cleanup','recovery')},
        'platform_clues':'Configured capacity-risk scope only; root cause unknown.'}}))
    state={'scenario':'connection-headroom','desired_on':False,'actual_state':'off','revision':21,
        'generation_id':'a'*32,'platform':{'target':{'key':'db-a-headroom'}},
        'scenarios':[{'key':'connection-headroom','target_key':'db-a-headroom','available':True},
                     {'key':'query-efficiency','target_key':'svc-a','available':True}]}
    calls=[]
    def handle(r):
        calls.append((r.method,r.url.path,json.loads(r.content) if r.content else None))
        if r.url.path=='/api/state':return httpx.Response(200,json=state)
        if r.url.path=='/api/switch':
            body=json.loads(r.content)
            if body['expected_revision']!=state['revision']:return httpx.Response(409,json={'error':'revision_conflict'})
            return httpx.Response(200,json={**state,'desired_on':body['on']})
        return httpx.Response(200,json={'target_key':'db-a-headroom','platform':{'target':{'key':'db-a-headroom'},'report':None}})
    cfg={'controller_origin':'http://127.0.0.1:18411','public_origin':'http://testserver',
        'verification_path':str(proof),'public_scenarios':{'connection-headroom':'db-a-headroom'},
        'platform_public_url':'https://platform.example'}
    client=TestClient(create_app(cfg,httpx.Client(transport=httpx.MockTransport(handle))))
    yield client,calls,proof,state
    client.close()


def payload(**extra):
    return {'on':True,'scenario':'connection-headroom','expected_revision':21,'expected_generation':'a'*32,**extra}


def test_only_explicitly_verified_scene_and_target_are_public(site):
    client,calls,_,_=site
    state=client.get('/api/state').json()
    assert [s['key'] for s in state['scenarios']]==['connection-headroom']
    assert state['scenarios'][0]['status']=='analysis_verified'
    assert client.get('/api/targets/svc-a/snapshot').status_code==404
    assert not any('/svc-a/' in p for _,p,_ in calls)
    assert client.get('/api/targets/db-a-headroom/snapshot').status_code==200


@pytest.mark.parametrize('record',[{}, {'connection-headroom':{'status':'platform_verified'}},'corrupt'])
def test_legacy_missing_or_corrupt_acceptance_fails_closed(site,record):
    client,calls,proof,_=site
    proof.write_text('not-json' if record=='corrupt' else json.dumps(record))
    assert client.get('/api/state').status_code==503
    assert client.put('/api/switch',headers={'Origin':'http://testserver'},json=payload()).status_code==422
    assert not any(method=='PUT' for method,_,_ in calls)


def test_other_scene_is_never_forwarded_even_with_valid_concurrency(site):
    client,calls,_,_=site
    assert client.put('/api/switch',headers={'Origin':'http://testserver'},json=payload(scenario='query-efficiency')).status_code==422
    assert not any(method=='PUT' for method,_,_ in calls)


def test_origin_exact_fields_and_concurrency_are_required(site):
    client,calls,_,_=site
    assert client.put('/api/switch',json=payload()).status_code==403
    for body in (payload(owner='attacker'),payload(on='true'),{'on':True,'scenario':'connection-headroom'}):
        assert client.put('/api/switch',headers={'Origin':'http://testserver'},json=body).status_code==422
    assert not any(method=='PUT' for method,_,_ in calls)
    assert client.put('/api/switch',headers={'Origin':'http://testserver'},json=payload(expected_revision=20)).status_code==409
    assert client.put('/api/switch',headers={'Origin':'http://testserver'},json=payload()).status_code==200
    assert all(path in ('/api/state','/api/switch') for _,path,_ in calls)


def test_revoked_acceptance_still_allows_current_scene_off(site):
    client,calls,proof,state=site
    state['desired_on']=True;proof.write_text('{}')
    fresh=client.get('/api/state')
    assert fresh.status_code==200
    snapshot=fresh.json()
    assert snapshot['scenarios'][0]['available'] is False
    assert snapshot['scenarios'][0]['status']=='implemented_awaiting_verification'
    response=client.put('/api/switch',headers={'Origin':'http://testserver'},json=payload(on=False,
        expected_revision=snapshot['revision'],expected_generation=snapshot['generation_id']))
    assert response.status_code==200 and response.json()['desired_on'] is False
    assert len([r for r in calls if r[0]=='PUT'])==1


def test_does_not_take_over_other_internal_work(site):
    client,calls,_,state=site
    state.update(scenario='query-efficiency',desired_on=True)
    assert client.get('/api/state').status_code==503
    assert client.put('/api/switch',headers={'Origin':'http://testserver'},json=payload()).status_code==409
    assert not any(method=='PUT' for method,_,_ in calls)


@pytest.mark.parametrize('change',[{'target_key':'svc-a'},{'available':False}])
def test_on_rechecks_pinned_live_catalog_before_forwarding(site,change):
    client,calls,_,state=site
    state['scenarios'][0].update(change)
    assert client.put('/api/switch',headers={'Origin':'http://testserver'},json=payload()).status_code==422
    assert not any(method=='PUT' for method,_,_ in calls)


def test_upstream_failure_is_unavailable_without_private_details(site):
    from lab.curated_site import create_app
    _,_,proof,_=site
    def broken(r):raise httpx.ConnectError('private-secret-and-host',request=r)
    with TestClient(create_app({'controller_origin':'http://127.0.0.1:18411','public_origin':'http://testserver',
        'verification_path':str(proof),'public_scenarios':{'connection-headroom':'db-a-headroom'}},
        httpx.Client(transport=httpx.MockTransport(broken)))) as c:
        response=c.get('/api/state')
        assert response.status_code==503 and 'private-secret' not in response.text
