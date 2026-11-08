"""Export actual scoped Zabbix events/history for Lab acceptance (no tokens)."""
import json
import time
import urllib.request
from pathlib import Path

def audit(url,password_file):
    token=None
    def api(method,params):
        headers={'Content-Type':'application/json-rpc'}
        if token: headers['Authorization']='Bearer '+token
        request=urllib.request.Request(url,json.dumps(dict(jsonrpc='2.0',method=method,params=params,id=1)).encode(),headers)
        with urllib.request.urlopen(request,timeout=15) as response: value=json.load(response)
        if 'error' in value: raise RuntimeError(str(value['error']))
        return value['result']
    seed=json.loads(Path('.runtime/seed.json').read_text())
    token=api('user.login',{'username':'Admin','password':Path(password_file).read_text().strip()})
    try:
        events=api('event.get',{'hostids':list(seed['hosts'].values()),'source':0,'object':0,'time_from':int(time.time())-7200,'output':['eventid','clock','value','objectid','name','severity','r_eventid'],'selectTags':'extend','sortfield':['clock'],'sortorder':'ASC','limit':1000})
        histories={}
        for name,itemid in seed['items'].items():
            if name=='sl.provenance': continue
            history=4 if name.endswith(('lock_snapshot','sql_summary')) else 3 if name.endswith(('lock_waits','threads_running','mysql_available')) else 0
            histories[name]=api('history.get',{'history':history,'itemids':[itemid],'time_from':int(time.time())-7200,'output':'extend','sortfield':['clock'],'sortorder':'ASC','limit':2000})
        result={'source_mode':'live_lab','exported_at_ms':int(time.time()*1000),'events':events,'histories':histories,'limits':{'events':1000,'per_item_history':2000},'scope':seed['hosts']}
        Path('.runtime/events.json').write_text(json.dumps(result,indent=2))
    finally: api('user.logout',[])
    print(json.dumps({'event_count':len(events),'problem_events':[event for event in events if event['value']=='1'],'history_counts':{key:len(value) for key,value in histories.items()}}))

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(); parser.add_argument('--url',required=True); parser.add_argument('--password-file',required=True)
    args=parser.parse_args(); audit(args.url,args.password_file)
