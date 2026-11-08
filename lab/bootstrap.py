"""Admin-only seed, executed separately from formal read-only runtime."""
import json
import urllib.request
from pathlib import Path
from lab.metrics import HOSTS, ITEMS, ITEM_METADATA_TAGS

def bootstrap(url,password_file,groupid='22'):
    token=None
    def api(method,params):
        headers={'Content-Type':'application/json-rpc'}
        if token: headers['Authorization']='Bearer '+token
        request=urllib.request.Request(url,json.dumps(dict(jsonrpc='2.0',method=method,params=params,id=1)).encode(),headers)
        with urllib.request.urlopen(request,timeout=15) as response: result=json.load(response)
        if 'error' in result: raise RuntimeError(str(result['error']))
        return result['result']
    token=api('user.login',{'username':'Admin','password':Path(password_file).read_text().strip()})
    result={'source_id':'lab','datasource_uid':'signallens-zabbix','hostgroupid':groupid,'hosts':{},'items':{},'triggers':{}}
    try:
        for layer in ('host','db','net'):
            hostname=HOSTS[layer]
            tags=[{'tag':'sl.layer','value':layer},{'tag':'sl.system','value':'lab-orders'},{'tag':'sl.source_mode','value':'live_lab'}]
            if layer!='host': tags.append({'tag':'sl.parent','value':result['hosts']['host']})
            existing=api('host.get',{'filter':{'host':[hostname]},'output':['hostid'],'selectGroups':['groupid']})
            if existing:
                if groupid not in [g['groupid'] for g in existing[0]['groups']]: raise ValueError('Existing host outside allowlisted Lab group')
                hostid=existing[0]['hostid']
                api('host.update',{'hostid':hostid,'tags':tags})
            else: hostid=api('host.create',{'host':hostname,'name':hostname,'groups':[{'groupid':groupid}],'tags':tags})['hostids'][0]
            result['hosts'][layer]=hostid
            for key,semantic,kind,vtype,units in ITEMS[layer]+[('sl.provenance','provenance','log',4,'')]:
                existing_items=api('item.get',{'hostids':[hostid],'filter':{'key_':key},'output':['itemid']})
                data={'hostid':hostid,'name':semantic+' (live Lab measurement)','key_':key,'type':2,'value_type':vtype,'units':units,'history':'7d','trends':'0','tags':[{'tag':'sl.metric','value':semantic},{'tag':'sl.kind','value':kind},{'tag':'sl.source_mode','value':'live_lab'},{'tag':'sl.sample_period','value':'3s'}]}
                data['tags'].extend(ITEM_METADATA_TAGS.get(key, []))
                if existing_items:
                    itemid=existing_items[0]['itemid']
                    api('item.update',{'itemid':itemid,'tags':data['tags']})
                else: itemid=api('item.create',data)['itemids'][0]
                result['items'][key]=itemid
        for name,expression,priority,scenario in [
            ('Lab MySQL actual lock waits','last(/sl-lab-db/sl.mysql.lock_waits)>0',3,'lock_wait'),
            ('Lab MySQL active SQL workload','last(/sl-lab-db/sl.mysql.threads_running)>2',2,'sql_load'),
            ('Lab MySQL probe unavailable','last(/sl-lab-net/sl.net.mysql_available)=0',3,'probe')]:
            existing=api('trigger.get',{'hostids':list(result['hosts'].values()),'filter':{'description':name},'output':['triggerid']})
            data={'description':name,'expression':expression,'priority':priority,'tags':[{'tag':'sl.system','value':'lab-orders'},{'tag':'sl.scenario','value':scenario},{'tag':'sl.source_mode','value':'live_lab'}]}
            triggerid=existing[0]['triggerid'] if existing else api('trigger.create',data)['triggerids'][0]
            result['triggers'][scenario]=triggerid
    finally: api('user.logout',[])
    Path('.runtime/seed.json').write_text(json.dumps(result,indent=2))
    return result

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument('--url',required=True)
    parser.add_argument('--password-file',required=True)
    parser.add_argument('--group-id',default='22')
    args=parser.parse_args()
    print(json.dumps(bootstrap(args.url,args.password_file,args.group_id)))
