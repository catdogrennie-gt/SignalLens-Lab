from datetime import datetime,timedelta,timezone
import json
import httpx
import pytest
from lab.scenario_validation import ScenarioValidator,ValidationOptions,save_private_result


class Clock:
    def __init__(self):self.value=0
    def monotonic(self):return self.value
    def sleep(self,seconds):self.value+=seconds
    def utc(self):return (datetime(2026,11,12,tzinfo=timezone.utc)+timedelta(seconds=self.value)).isoformat().replace('+00:00','Z')


def fixture(*,old_report_only=False,interrupted=False,fail_once=False,stale_recovery=False,on_conflict=False,lost_on_ack=False,restarted=False,expired=False,stale_direct=False,unrelated_match=False):
    clock=Clock();calls=[];data={'on':False,'revision':0,'generation':'g'*32,'validation_id':None,'scenario':'query-efficiency','on_at':None,'off_at':None,'reads':0,'failed':False,'expired_revision':None}
    def state():
        on=data['on'];stamp=clock.utc();during=data['on_at'] is not None
        new=on and data['reads']>=2
        report_new=new and not old_report_only
        return {'generation_id':data['generation'],'revision':data['revision'],'scenario':data['scenario'],'desired_on':on,'actual_state':'on' if on else 'off',
                'validation_id':data['validation_id'],
                'lease_expired_revision':data['expired_revision'],'control_lease_expires_at':stamp if on else None,
                'scenarios':[{'key':'query-efficiency','target_key':'svc-a','available':True}],
                'token':'DO_NOT_PUBLISH_SECRET','private_path':'D:/private/config.json',
                'platform':{'target':{'key':'svc-a'},'inspection':{'status':'running' if on else 'idle'},
                    'observation':{'observed_at':stamp,'status':'signals_present' if new else 'data_available',
                        'metrics':[{'hostid':'1','itemid':'2','name':'Request duration','unit':'ms','value':100 if on else 10,'status':'stale' if stale_recovery and during and not on else 'fresh','observed_at':stamp,'value_semantics':'gauge'}],
                        'signals':[{'id':'new-signal','event_id':'event-new','observed_at':stamp,'hostid':'1','itemid':'2','episode_started_at':data['on_at']}] if new else []},
                    'report_matches_current_observation':True,
                    'report':{'id':'new-report' if report_new else 'old-report','status':'completed','mode':'live_redacted',
                        'created_at':stamp if report_new else '2026-11-11T23:59:00Z','from':'2026-11-11T23:00:00Z','to':stamp,
                        'matched_signals':[{'id':'other-signal' if unrelated_match else 'new-signal','event_id':'event-new','observed_at':stamp,'hostid':'1','itemid':'2','episode_started_at':data['on_at']}] if new else [],
                        'executive_summary':{'headline':'PRIVATE_TEXT_MUST_NOT_COPY'}}}}
    def handle(request):
        body=json.loads(request.content) if request.content else None
        calls.append((request.method,request.url.path,body))
        assert request.url.host=='a.test'
        if request.url.path=='/api/switch':
            if on_conflict and body['on']:
                data['revision']+=1;data['on']=True;data['validation_id']='other-validation';data['on_at']=clock.utc()
                return httpx.Response(409,json={'error':'revision_conflict'})
            if (body['expected_revision']!=data['revision'] or body['expected_generation']!=data['generation']
                    or body.get('expected_validation_id',data['validation_id'])!=data['validation_id']):
                return httpx.Response(409,json={'error':'revision_conflict'})
            if (body['on'],body['scenario'])!=(data['on'],data['scenario']):data['revision']+=1
            data['on'],data['scenario']=body['on'],body['scenario']
            if body['on']:data['on_at']=clock.utc();data['validation_id']=body['validation_id']
            elif data['on_at'] is not None:data['off_at']=clock.utc()
            if lost_on_ack and body['on']:raise httpx.ReadTimeout('ack_lost',request=request)
            return httpx.Response(200,json=state())
        if request.url.path=='/api/targets/svc-a/snapshot':
            return httpx.Response(200,json={'target_key':'svc-a','platform':state()['platform'],'platform_received_at':clock.utc()})
        if request.url.path=='/api/targets/svc-a/control-state':
            return httpx.Response(200,json={'revision':data['revision'],'generation_id':data['generation'],'scenario':data['scenario'],'target_key':'svc-a','current':True,'observed_at':'2026-11-11T00:00:00Z' if stale_direct and data['off_at'] else clock.utc(),'control':{'normal':not data['on'],'held_connections':6 if data['on'] else 0,'held_read_view':False}})
        assert request.url.path=='/api/state'
        if data['on']:
            data['reads']+=1
            if fail_once and not data['failed']:
                data['failed']=True;raise httpx.ConnectError('PRIVATE_URL_MUST_NOT_COPY',request=request)
            if interrupted and data['reads']==2:
                data['revision']+=1;data['scenario']='temporary-work';data['validation_id']='other-visitor'
            if restarted and data['reads']==2:data['generation']='h'*32;data['validation_id']='other-controller'
            if expired and data['reads']==2:
                data['on']=False;data['revision']+=1;data['expired_revision']=data['revision'];data['off_at']=clock.utc()
        return httpx.Response(200,json=state())
    client=httpx.Client(transport=httpx.MockTransport(handle))
    validator=ScenarioValidator(ValidationOptions('http://a.test','query-efficiency',8,8,poll_seconds=1),client=client,clock=clock)
    return validator,calls,data


def test_only_a_switch_and_reads_are_used_and_all_observation_stages_are_distinct():
    validator,calls,data=fixture();r=validator.run()
    assert not data['on']
    assert {path for _,path,_ in calls}<={'/api/state','/api/switch','/api/targets/svc-a/control-state','/api/targets/svc-a/snapshot'}
    assert all(method in {'GET','PUT'} for method,_,_ in calls)
    assert r['phases']['autonomous_discovery']['new_signals']
    assert r['phases']['report']['new_report']['id']=='new-report'
    assert r['phases']['direct_cleanup']['confirmed'] is True
    assert r['phases']['monitor_recovery']['status']=='observed_return_near_pre_switch_values'
    assert r['diagnosis_quality']=='pending_human_review'
    assert r['status']=='observations_recorded'
    writes=[body for method,path,body in calls if method=='PUT']
    assert len(writes)==2 and [body['on'] for body in writes]==[True,False]


def test_old_current_report_is_never_counted_as_new_case_success():
    validator,_,data=fixture(old_report_only=True);r=validator.run()
    assert not data['on']
    assert r['phases']['report']['new_report'] is None
    assert r['status']=='incomplete'


def test_current_report_for_unrelated_signal_does_not_pass_this_run():
    validator,_,data=fixture(unrelated_match=True);r=validator.run()
    assert not data['on'] and r['phases']['autonomous_discovery']['new_signals']
    assert r['phases']['report']['new_report']['id']=='new-report'
    assert not r['phases']['report']['current_episode_claimed_by_bridge']
    assert not r['phases']['report']['matched_this_run_signals']
    assert r['status']=='incomplete'


def test_exception_after_on_always_attempts_conditional_off_and_redacts_errors():
    validator,calls,data=fixture(fail_once=True);r=validator.run()
    assert not data['on']
    assert any(body and body['on'] is False and body['expected_revision']==1 for _,path,body in calls if path=='/api/switch')
    assert r['phases']['direct_cleanup']['confirmed'] is True
    raw=json.dumps(r)
    assert not any(secret in raw for secret in ['DO_NOT_PUBLISH_SECRET','PRIVATE_TEXT_MUST_NOT_COPY','PRIVATE_URL_MUST_NOT_COPY','D:/private/config.json'])


def test_other_visitor_revision_change_interrupts_and_does_not_switch_off_their_case():
    validator,calls,data=fixture(interrupted=True);r=validator.run()
    assert r['status']=='interrupted' and data['on'] and data['scenario']=='temporary-work'
    assert not any(body and body['on'] is False for _,path,body in calls if path=='/api/switch' and body['expected_revision']>0)


def test_cleanup_does_not_imply_monitor_recovery_when_samples_are_stale():
    validator,_,_=fixture(stale_recovery=True);r=validator.run()
    assert r['phases']['direct_cleanup']['confirmed'] is True
    assert r['phases']['monitor_recovery']['status']!='observed_return_near_pre_switch_values'
    assert r['status']=='incomplete'


def test_on_compare_and_swap_conflict_does_not_claim_or_cancel_other_visitor():
    validator,calls,data=fixture(on_conflict=True);r=validator.run()
    assert data['on'] and r['status']=='interrupted'
    assert not any(body and body['on'] is False and body['expected_revision']>0 for _,path,body in calls if path=='/api/switch')


def test_ack_lost_after_real_on_is_cleaned_by_matching_validation_id():
    validator,calls,data=fixture(lost_on_ack=True);r=validator.run()
    assert not data['on'] and r['phases']['direct_cleanup']['confirmed']
    assert any(body and body.get('expected_validation_id')==validator.validation_id for _,path,body in calls if path=='/api/switch')


def test_controller_restart_with_same_revision_never_lets_old_harness_cancel_it():
    validator,calls,data=fixture(restarted=True);r=validator.run()
    assert data['on'] and r['status']=='interrupted'
    assert not any(body and body['on'] is False and body['expected_revision']>0 for _,path,body in calls if path=='/api/switch')


def test_own_deadman_expiration_is_not_misattributed_to_another_visitor():
    validator,_,data=fixture(expired=True);r=validator.run()
    assert not data['on'] and r['status']!='interrupted'
    assert r['phases']['direct_cleanup']['confirmed']
    assert any(e['event']=='bounded_lease_expired' for e in r['events'])


def test_cached_control_cleanup_from_before_off_is_not_accepted():
    validator,_,_=fixture(stale_direct=True);r=validator.run()
    assert r['phases']['direct_cleanup']['confirmed'] is False
    assert r['status']=='incomplete'


def test_output_must_be_absolute_outside_source_repository(tmp_path):
    with pytest.raises(ValueError):save_private_result('local.json',{})
    p=tmp_path/'proof.private.json';save_private_result(p,{'status':'incomplete'})
    assert json.loads(p.read_text())['status']=='incomplete'


@pytest.mark.parametrize('origin',['http://user:password@a.test','https://a.test/?token=secret','http://a.test/other'])
def test_origin_never_accepts_credentials_or_query_secrets(origin):
    with pytest.raises(ValueError):ValidationOptions(origin,'query-efficiency',8,8)
