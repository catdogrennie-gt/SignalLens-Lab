import json
import signal
import threading
import time
from pathlib import Path
from lab.db import config, identity, connect, dataset_digest
from lab.collector import Collector, utc
from lab.core import managed_connections

def execute(kind,duration=25):
    if kind not in ('lock_wait','sql_load') or not 10<=duration<=45:
        raise ValueError('Scenario/duration outside allowlist (10..45 seconds)')
    cfg=config(); target=identity(cfg)
    manifest={'source_mode':'live_lab','scenario':kind,'started_at':utc(),'target':target,
              'injected_action': 'UPDATE row 1 in held transaction; blocked UPDATE with innodb_lock_wait_timeout=6' if kind=='lock_wait' else 'two bounded SELECT BENCHMARK SQL workload connections',
              'observations':[],'worker_outcomes':[],'errors':[],'cleanup':{},'dataset_before':dataset_digest(cfg)}
    collector=Collector(cfg); workers=[]; stop=threading.Event()
    def observe(phase):
        sample=collector.sample(); sample['phase']=phase
        try: sample['sender']=collector.publish(sample)
        except Exception as exc: sample['sender_error']=str(exc); manifest['errors'].append('sender:'+type(exc).__name__)
        manifest['observations'].append(sample)
        return sample
    def interrupt(signum,frame): raise InterruptedError('Scenario terminated; cleanup follows')
    previous_handlers={sig:signal.signal(sig,interrupt) for sig in (signal.SIGTERM,signal.SIGINT)}
    try:
        for _ in range(3): observe('baseline'); time.sleep(3)
        with managed_connections(lambda:connect(cfg,'scenario',autocommit=False)) as acquire:
            if kind=='lock_wait':
                holder=acquire()
                with holder.cursor() as cur: cur.execute('UPDATE orders SET amount_cents=amount_cents+1 WHERE id=1')
                def blocked():
                    while not stop.is_set():
                        try:
                            with connect(cfg,'scenario',False) as conn, conn.cursor() as cur:
                                cur.execute('SET SESSION innodb_lock_wait_timeout=6')
                                try: cur.execute('UPDATE orders SET amount_cents=amount_cents+1 WHERE id=1')
                                finally: conn.rollback()
                        except Exception as exc: manifest['worker_outcomes'].append({'at':utc(),'type':type(exc).__name__,'mysql_code':exc.args[0] if exc.args else None})
                        stop.wait(.1)
                workers=[threading.Thread(target=blocked)]
            else:
                def sql_worker():
                    try:
                        with connect(cfg,'scenario',True) as conn,conn.cursor() as cur:
                            # Each SQL statement has a server-side 2s bound; no data mutation.
                            while not stop.is_set(): cur.execute("SELECT /*+ MAX_EXECUTION_TIME(2000) */ BENCHMARK(200000,SHA2('signallens-lab',256))"); cur.fetchone()
                    except Exception as exc: manifest['worker_outcomes'].append({'at':utc(),'type':type(exc).__name__,'mysql_code':exc.args[0] if exc.args else None})
                workers=[threading.Thread(target=sql_worker) for _ in range(2)]
            for worker in workers: worker.start()
            deadline=time.monotonic()+duration
            try:
                while time.monotonic()<deadline: observe('fault'); time.sleep(2)
                # A separate successful SELECT 1 is deliberately an insufficient repair control.
                manifest['negative_control']={'action':'successful independent SELECT 1 (network probe)','at':utc(),
                    'observation':observe('negative_control'),'interpretation':'Does not resolve the held transaction or SQL workload; inspect actual lock/load evidence.'}
            finally:
                stop.set()
                for worker in workers: worker.join(timeout=9)
                manifest['cleanup']['workers_stopped']=all(not worker.is_alive() for worker in workers)
        manifest['cleanup']['transactions_rolled_back_and_connections_closed']=True
    except BaseException as exc:
        manifest['errors'].append(type(exc).__name__+':'+str(exc))
    finally:
        stop.set()
        for worker in workers: worker.join(timeout=9)
        manifest['cleanup']['workers_stopped']=all(not worker.is_alive() for worker in workers)
        try:
            time.sleep(3)  # Avoid a near-zero interval after the negative-control sample.
            for _ in range(5): observe('recovery'); time.sleep(3)
            with connect(cfg) as conn,conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n FROM performance_schema.threads WHERE PROCESSLIST_USER='scenario'")
                manifest['cleanup']['remaining_scenario_sessions']=cur.fetchone()['n']
            manifest['cleanup']['remaining_lock_waits']=manifest['observations'][-1]['values'].get('sl.mysql.lock_waits')
            manifest['dataset_after']=dataset_digest(cfg)
            manifest['cleanup']['dataset_unchanged']=manifest['dataset_before']==manifest['dataset_after']
        except Exception as exc: manifest['errors'].append('cleanup verification:'+type(exc).__name__)
        manifest['ended_at']=utc()
        manifest['status']='verified' if not manifest['errors'] and manifest['cleanup'].get('workers_stopped') and manifest['cleanup'].get('dataset_unchanged') and manifest['cleanup'].get('remaining_lock_waits')==0 and manifest['cleanup'].get('remaining_scenario_sessions')==0 else 'failed_or_insufficient'
        if any(s['provenance'].get('errors') for s in manifest['observations']):
            manifest['status']='failed_or_insufficient'
        if any(kind=='sql_load' or outcome.get('mysql_code')!=1205 for outcome in manifest['worker_outcomes']):
            manifest['status']='failed_or_insufficient'
        if kind=='lock_wait' and not any(s['values'].get('sl.mysql.lock_waits',0)>0 for s in manifest['observations'] if s['phase']=='fault'):
            manifest['status']='failed_or_insufficient'
        if kind=='sql_load' and not any(s['values'].get('sl.mysql.threads_running',0)>2 for s in manifest['observations'] if s['phase']=='fault'):
            manifest['status']='failed_or_insufficient'
        Path('.runtime/runs').mkdir(parents=True,exist_ok=True)
        output=Path('.runtime/runs',kind+'-'+str(int(time.time()))+'.json')
        output.write_text(json.dumps(manifest,indent=2,default=str))
        for sig,handler in previous_handlers.items(): signal.signal(sig,handler)
    return {'manifest':str(output),'status':manifest['status'],'cleanup':manifest['cleanup']}
