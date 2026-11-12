"""Reproducible observation harness. Only A's switch and read projections are used.

No model prompts, investigation creation, direct DB connections or expected
diagnoses live here. A conditional bounded lease protects other visitors and
expires even if this process is killed before its finally block can execute.
"""
import argparse
from dataclasses import dataclass
from datetime import datetime,timezone
import json
import math
import re
from pathlib import Path
import time
import uuid
from urllib.parse import urlsplit

import httpx


class Clock:
    monotonic=staticmethod(time.monotonic)
    sleep=staticmethod(time.sleep)
    @staticmethod
    def utc():return datetime.now(timezone.utc).isoformat().replace('+00:00','Z')


def timestamp(value):
    try:
        parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        return parsed if parsed.tzinfo is not None else None
    except (ValueError,TypeError,AttributeError):return None


def after(value,boundary):
    a,b=timestamp(value),timestamp(boundary)
    return a is not None and b is not None and a>b


@dataclass
class ValidationOptions:
    a_origin:str
    scenario:str
    max_wait_seconds:float=240
    recovery_wait_seconds:float=120
    poll_seconds:float=3

    def __post_init__(self):
        u=urlsplit(self.a_origin)
        if (u.scheme not in {'http','https'} or not u.hostname or u.username or u.password
                or u.query or u.fragment or u.path not in {'','/'}):
            raise ValueError('a_origin_must_be_an_origin_without_credentials')
        if not 1<=self.max_wait_seconds<=3500 or not 0<=self.recovery_wait_seconds<=1800 or not 1<=self.poll_seconds<=30:
            raise ValueError('invalid_wait_bounds')
        self.a_origin=self.a_origin.rstrip('/')


def safe_metric(row):
    value=row.get('value')
    if type(value) not in (int,float) or not math.isfinite(value):value=None
    return {**{k:str(row.get(k) or '')[:200] for k in ('hostid','itemid','name','host_name','unit')},
            'value':value,'observed_at':row.get('observed_at'),'status':row.get('status','unknown'),
            'value_semantics':row.get('value_semantics','unknown')}


def safe_signal(row):
    return {k:row.get(k) for k in ('id','event_id','category','kind','classification','observed_at','episode_started_at','hostid','itemid')}


def signal_identity(row):return tuple(str(row.get(k) or '') for k in ('id','event_id','episode_started_at'))
def metric_identity(row):return str(row.get('hostid') or '')+':'+str(row.get('itemid') or '')
def comparable(row):return row['status']=='fresh' and row['value'] is not None and row['value_semantics'] in {'gauge','rate'}


def public_observation(state,received_at):
    p=state.get('platform') or {};o=p.get('observation') or {};r=p.get('report') or {};i=p.get('inspection') or {}
    report=None
    if r:
        report={k:r.get(k) for k in ('id','status','mode','created_at','from','to','error_code')}
        report['summary_present']=bool(r.get('executive_summary'))
        report['matches_current_observation']=p.get('report_matches_current_observation') is True
        report['matched_signals']=[safe_signal(s) for s in r.get('matched_signals',[])[:64]]
    return {'received_at':received_at,'revision':state.get('revision'),'generation_id':state.get('generation_id'),
            'desired_on':state.get('desired_on'),'actual_state':state.get('actual_state'),
            'target_key':(p.get('target') or {}).get('key'),'observed_at':o.get('observed_at'),
            'observation_status':o.get('status'),'metrics':[safe_metric(m) for m in o.get('metrics',[])[:64]],
            'signals':[safe_signal(s) for s in o.get('signals',[])[:64]],'report':report,
            'inspection':{k:i.get(k) for k in ('status','last_checked_at','last_completed_at','next_due_at')}}


class InterruptedValidation(RuntimeError):pass
class PreconditionFailed(RuntimeError):pass


class ScenarioValidator:
    def __init__(self,options,client=None,clock=None,on_update=None):
        self.options=options;self.client=client or httpx.Client(timeout=10,trust_env=False)
        self.owns_client=client is None;self.clock=clock or Clock()
        self.on_update=on_update
        self.generation=None;self.owned_revision=None;self.off_revision=None;self.target_key=None
        self.validation_id=uuid.uuid4().hex
        self.baseline_metrics={};self.baseline_signals=set();self.baseline_report=None
        self.changes={};self.new_signals={};self.last_fingerprint=None
        self.result={'validation_schema':1,'a_origin':options.a_origin,'scenario':options.scenario,
            'started_at':self.clock.utc(),'status':'not_started','diagnosis_quality':'pending_human_review',
            'meaning':'Observation workflow only. A successful run does not certify diagnosis accuracy or database health.',
            'phases':{'load_control':{'accepted':False,'active_seen':False},'data':{'fresh_after_on_seen':False},
                'autonomous_discovery':{'new_signals':[]},'report':{'new_report':None,'current_episode_claimed_by_bridge':False},
                'direct_cleanup':{'confirmed':False},'monitor_recovery':{'status':'not_checked'}},
            'events':[],'samples':[],'errors':[],'observed_numeric_changes':[]}

    def request(self,method,path,body=None):
        # Every path is a literal allowlisted A path; never follow a B URL from JSON.
        if ((method,path) not in {('GET','/api/state'),('PUT','/api/switch')} and
                not(method=='GET' and self.target_key and path in {
                    '/api/targets/'+self.target_key+'/snapshot','/api/targets/'+self.target_key+'/control-state'})):
            raise ValueError('unsupported_validation_request')
        r=self.client.request(method,self.options.a_origin+path,
            headers={'Origin':self.options.a_origin} if method=='PUT' else {},
            **({'json':body} if body is not None else {}))
        r.raise_for_status();return r.json()

    def event(self,name,**values):
        self.result['events'].append({'event':name,'at':self.clock.utc(),**values})
        if self.on_update:self.on_update(self.result)

    def conditional_switch(self,on,revision,*,bounded=False,owned=False):
        body={'on':on,'scenario':self.options.scenario,'expected_revision':revision,'expected_generation':self.generation}
        if bounded:body.update(lease_seconds=math.ceil(self.options.max_wait_seconds+15),validation_id=self.validation_id)
        if owned:body['expected_validation_id']=self.validation_id
        return self.request('PUT','/api/switch',body)

    def ensure_owned(self,state,revision,*,validation=False,check_scenario=True):
        if (state.get('generation_id')!=self.generation or state.get('revision')!=revision or
                check_scenario and state.get('scenario')!=self.options.scenario or
                validation and state.get('validation_id')!=self.validation_id):
            raise InterruptedValidation('generation_revision_or_scenario_changed')

    def record(self,state):
        obs=public_observation(state,self.clock.utc())
        # Keep distinct samples, not repeated browser polling of the same data.
        fingerprint=json.dumps({k:v for k,v in obs.items() if k!='received_at'},sort_keys=True)
        if fingerprint!=self.last_fingerprint:
            if len(self.result['samples'])<1024:self.result['samples'].append(obs)
            else:self.result['sample_budget_exceeded']=True
            self.last_fingerprint=fingerprint
            if self.on_update:self.on_update(self.result)
        return obs

    def preflight(self):
        state=self.request('GET','/api/state')
        self.generation=state.get('generation_id')
        if not isinstance(self.generation,str) or len(self.generation)!=32 or type(state.get('revision')) is not int:
            raise PreconditionFailed('conditional_validation_protocol_not_loaded')
        if state.get('desired_on') or state.get('actual_state')!='off':raise PreconditionFailed('another_workload_is_active')
        row=next((r for r in state.get('scenarios',[]) if r.get('key')==self.options.scenario),None)
        if not row or not row.get('available'):raise PreconditionFailed('scenario_unavailable')
        self.target_key=row['target_key']
        if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',self.target_key):raise PreconditionFailed('invalid_target_identity')
        revision=state['revision']
        self.ensure_owned(state,revision,check_scenario=False)
        deadline=self.clock.monotonic()+30
        while True:
            self.ensure_owned(state,revision,check_scenario=False)
            state=self.with_target_projection(state)
            p=state.get('platform') or {}
            if (p.get('target') or {}).get('key')==self.target_key:
                obs=self.record(state)
                self.baseline_metrics={metric_identity(m):m for m in obs['metrics'] if comparable(m)}
                if self.baseline_metrics:break
            if self.clock.monotonic()>=deadline:raise PreconditionFailed('fresh_target_baseline_unavailable')
            self.clock.sleep(self.options.poll_seconds);state=self.request('GET','/api/state')
        direct=self.request('GET','/api/targets/'+self.target_key+'/control-state')
        if not self.direct_clean(direct,revision,check_scenario=False):raise PreconditionFailed('target_direct_cleanup_not_confirmed_before_on')
        self.result['baseline']={'observed_at':obs['observed_at'],'metrics':list(self.baseline_metrics.values()),
            'report_id':(obs['report'] or {}).get('id'),'signals':obs['signals'],
            'meaning':'Fresh pre-switch values, not proof of a healthy normal baseline.'}
        self.baseline_signals={signal_identity(s) for s in obs['signals']};self.baseline_report=(obs['report'] or {}).get('id')
        self.event('preflight_completed',revision=revision)
        return revision

    def with_target_projection(self,state):
        view=self.request('GET','/api/targets/'+self.target_key+'/snapshot')
        if view.get('target_key')!=self.target_key:raise PreconditionFailed('unexpected_read_target')
        return dict(state,platform=view.get('platform'),platform_received_at=view.get('platform_received_at'))

    def direct_clean(self,direct,revision,*,check_scenario=True):
        c=direct.get('control') or {}
        return (direct.get('current') is True and direct.get('generation_id')==self.generation
            and direct.get('revision')==revision and (not check_scenario or direct.get('scenario')==self.options.scenario)
            and direct.get('target_key')==self.target_key and c.get('normal') is True
            and type(c.get('held_connections')) is int and c['held_connections']==0 and c.get('held_read_view') is False
            and (not self.result.get('off_at') or after(direct.get('observed_at'),self.result['off_at'])))

    def expired_ours(self,state):
        return (state.get('generation_id')==self.generation and state.get('scenario')==self.options.scenario
            and state.get('validation_id')==self.validation_id and not state.get('desired_on')
            and state.get('revision')==self.owned_revision+1 and state.get('lease_expired_revision')==state.get('revision'))

    def observe_load(self,state):
        obs=self.record(state);on_at=self.result['on_at'];phases=self.result['phases']
        phases['load_control']['active_seen']|=state.get('actual_state')=='on'
        for m in obs['metrics']:
            if not comparable(m) or not after(m['observed_at'],on_at):continue
            phases['data']['fresh_after_on_seen']=True
            ident=metric_identity(m);base=self.baseline_metrics.get(ident)
            if not base:continue
            difference=abs(m['value']-base['value']);threshold=max(abs(base['value'])*.5,1e-9)
            if difference>threshold:
                old=self.changes.get(ident)
                if old is None or difference>old['absolute_difference']:
                    self.changes[ident]={'hostid':m['hostid'],'itemid':m['itemid'],'name':m['name'],'unit':m['unit'],
                        'before':base['value'],'during':m['value'],'observed_at':m['observed_at'],'absolute_difference':difference,
                        'meaning':'Numeric difference only; causality is not asserted.'}
        for s in obs['signals']:
            if (signal_identity(s) not in self.baseline_signals and after(s.get('observed_at'),on_at)
                    and (not s.get('episode_started_at') or timestamp(s['episode_started_at']) is not None and
                         timestamp(s['episode_started_at'])>=timestamp(on_at))):
                self.new_signals[signal_identity(s)]=s
        phases['autonomous_discovery']['new_signals']=list(self.new_signals.values())
        report=obs['report']
        if (report and report['id']!=self.baseline_report and after(report['created_at'],on_at)
                and report['status'] in {'completed','partial'}):
            phases['report']['new_report']=report
            # A report can be current for another finding on the same target.
            # Require the bridge's explicit episode AND item association to one
            # of this run's new signals; a boolean alone is insufficient.
            matched=[s for s in report['matched_signals'] if s.get('id') and
                signal_identity(s) in self.new_signals and s.get('hostid') and s.get('itemid') and
                metric_identity(s)==metric_identity(self.new_signals[signal_identity(s)])]
            phases['report']['matched_this_run_signals']=matched
            phases['report']['current_episode_claimed_by_bridge']=bool(report['matches_current_observation'] and matched)
        return bool(phases['report']['current_episode_claimed_by_bridge'] and
            (phases['report']['new_report'] or {}).get('summary_present'))

    def cleanup(self):
        if self.owned_revision is None:
            self.result['phases']['direct_cleanup']['status']='no_load_started';return
        # Conditional OFF is safe even if the last GET failed: generation/revision
        # prevent overwriting another visitor or a restarted controller.
        try:
            state=self.request('GET','/api/state')
            if self.expired_ours(state):
                self.off_revision=state['revision'];self.result['off_at']=self.clock.utc()
                self.event('bounded_lease_expired');self.recover();return
            self.ensure_owned(state,self.owned_revision,validation=True)
        except InterruptedValidation:
            self.result['status']='interrupted';self.result['phases']['direct_cleanup']['status']='not_owned_after_interruption';return
        except (httpx.HTTPError,ValueError):state=None
        try:
            state=self.conditional_switch(False,self.owned_revision,owned=True)
            self.off_revision=self.owned_revision+1
            self.ensure_owned(state,self.off_revision,validation=True)
            self.result['off_at']=self.clock.utc();self.event('conditional_off_accepted',revision=self.off_revision)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code==409:
                self.result['status']='interrupted';self.result['phases']['direct_cleanup']['status']='conditional_off_conflict';return
            raise
        self.recover()

    def recover(self):
        deadline=self.clock.monotonic()+self.options.recovery_wait_seconds
        consecutive=0;last_fingerprint=None
        signaled={str(s.get('hostid'))+':'+str(s.get('itemid')) for s in self.new_signals.values() if s.get('hostid') and s.get('itemid')}
        selected={k:v for k,v in self.changes.items() if k in signaled}
        recovery=self.result['phases']['monitor_recovery']
        recovery['comparison_scope']='this_run_signal_dimensions' if selected else 'no_signal_dimensions_available'
        recovery['meaning']='Near pre-switch values is an observation, not a guarantee of database health.'
        while True:
            state=self.request('GET','/api/state');self.ensure_owned(state,self.off_revision,validation=True)
            obs=self.record(self.with_target_projection(state))
            if not self.result['phases']['direct_cleanup']['confirmed']:
                try:
                    direct=self.request('GET','/api/targets/'+self.target_key+'/control-state')
                    if self.direct_clean(direct,self.off_revision):
                        self.result['phases']['direct_cleanup']={'confirmed':True,'status':'actual_control_clean',
                            'observed_at':direct['observed_at'],'control':direct['control']}
                except httpx.HTTPError:pass
            metrics={metric_identity(m):m for m in obs['metrics']}
            fresh=[m for m in obs['metrics'] if comparable(m) and after(m['observed_at'],self.result['off_at'])]
            recovery['fresh_after_off_seen']=bool(fresh)
            matching=[]
            for ident in selected:
                m=metrics.get(ident);b=self.baseline_metrics[ident]
                matching.append(bool(m and comparable(m) and after(m['observed_at'],self.result['off_at'])
                    and abs(m['value']-b['value'])<=max(abs(b['value'])*.5,1e-9)))
            fingerprint=tuple((k,metrics[k]['observed_at']) for k in selected if k in metrics)
            if selected and all(matching):
                if fingerprint!=last_fingerprint:consecutive+=1;last_fingerprint=fingerprint
            else:consecutive=0;last_fingerprint=None
            recovery['consecutive_distinct_observations']=consecutive
            recovery['compared_itemids']=[v['itemid'] for v in selected.values()]
            if consecutive>=2:
                recovery['status']='observed_return_near_pre_switch_values'
                if self.result['phases']['direct_cleanup']['confirmed']:return
            if self.clock.monotonic()>=deadline:
                if recovery.get('status')!='observed_return_near_pre_switch_values':
                    recovery['status']='unconfirmed' if selected else 'no_signal_dimensions_to_compare'
                return
            self.clock.sleep(min(self.options.poll_seconds,max(0,deadline-self.clock.monotonic())))

    def run(self):
        try:
            revision=self.preflight()
            self.result['status']='running'
            self.result['on_at']=self.clock.utc();self.owned_revision=revision+1
            try:state=self.conditional_switch(True,revision,bounded=True)
            except httpx.HTTPStatusError as exc:
                self.owned_revision=None
                if exc.response.status_code==409:raise InterruptedValidation('on_revision_conflict') from None
                raise
            self.ensure_owned(state,self.owned_revision,validation=True)
            self.result['phases']['load_control']['accepted']=True
            self.event('conditional_on_accepted',revision=self.owned_revision,
                server_expires_at=state.get('control_lease_expires_at'))
            deadline=self.clock.monotonic()+self.options.max_wait_seconds
            while True:
                state=self.request('GET','/api/state')
                if self.expired_ours(state):self.event('bounded_lease_expired_during_observation');break
                self.ensure_owned(state,self.owned_revision,validation=True)
                if self.observe_load(self.with_target_projection(state)):break
                if self.clock.monotonic()>=deadline:break
                self.clock.sleep(min(self.options.poll_seconds,max(0,deadline-self.clock.monotonic())))
        except PreconditionFailed as exc:
            self.result['status']='precondition_failed';self.event('precondition_failed',code=str(exc))
        except InterruptedValidation:
            self.result['status']='interrupted';self.event('interrupted')
        except Exception as exc:
            self.result['status']='error';self.result['errors'].append({'phase':'observation','error_type':type(exc).__name__})
        finally:
            try:self.cleanup()
            except InterruptedValidation:self.result['status']='interrupted';self.event('interrupted_during_recovery')
            except Exception as exc:
                self.result['errors'].append({'phase':'cleanup','error_type':type(exc).__name__})
                self.result['phases']['direct_cleanup'].setdefault('status','unconfirmed_server_deadline_remains_active')
            self.result['observed_numeric_changes']=list(self.changes.values())
            self.result['ended_at']=self.clock.utc()
            if self.owns_client:self.client.close()
        if self.result['status'] not in {'error','precondition_failed','interrupted'}:
            p=self.result['phases']
            complete=(p['load_control']['active_seen'] and p['data']['fresh_after_on_seen'] and
                bool(p['autonomous_discovery']['new_signals']) and p['report']['current_episode_claimed_by_bridge']
                and p['direct_cleanup']['confirmed'] and p['monitor_recovery']['status']=='observed_return_near_pre_switch_values')
            self.result['status']='observations_recorded' if complete else 'incomplete'
        return self.result


def save_private_result(path,result):
    path=Path(path)
    if not path.is_absolute():raise ValueError('absolute_private_output_required')
    path=path.resolve();source=Path(__file__).resolve().parents[1]
    if path.is_relative_to(source):raise ValueError('output_must_be_outside_public_source')
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.tmp')
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    temporary.chmod(0o600)
    # Windows readers can briefly deny replacing the last complete checkpoint.
    # Retry only that operation, retaining the previous record until success;
    # persistent denial still reaches the validator's normal safety cleanup.
    for delay in (0,.025,.05,.1,.2):
        if delay:time.sleep(delay)
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if delay==.2:raise


def main(argv=None):
    parser=argparse.ArgumentParser(description='Observe a real A→Zabbix→B validation run. Does not create AI tasks.')
    parser.add_argument('--a-origin',required=True);parser.add_argument('--scenario',required=True)
    parser.add_argument('--max-wait-seconds',type=float,default=240)
    parser.add_argument('--recovery-wait-seconds',type=float,default=120)
    parser.add_argument('--poll-seconds',type=float,default=3)
    parser.add_argument('--output',required=True,help='Absolute private JSON path outside the source checkout')
    args=parser.parse_args(argv)
    # Validate destination before any switch is sent.
    output=Path(args.output)
    if not output.is_absolute() or output.resolve().is_relative_to(Path(__file__).resolve().parents[1]):
        parser.error('--output must be an absolute path outside this public checkout')
    if output.exists():parser.error('--output already exists; use a new filename to retain earlier trials')
    options=ValidationOptions(args.a_origin,args.scenario,args.max_wait_seconds,args.recovery_wait_seconds,args.poll_seconds)
    validator=ScenarioValidator(options,on_update=lambda result:save_private_result(output,result))
    save_private_result(output,validator.result)
    try:result=validator.run()
    except KeyboardInterrupt:
        result=validator.result;result['status']='interrupted';result['errors'].append({'phase':'operator','error_type':'KeyboardInterrupt'})
    save_private_result(output,result)
    print(json.dumps({'status':result['status'],'diagnosis_quality':result['diagnosis_quality'],'private_output_saved':True}))
    return 0 if result['status']=='observations_recorded' else 2 if result['status']=='interrupted' else 3


if __name__=='__main__':raise SystemExit(main())
