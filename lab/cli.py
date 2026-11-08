import argparse
import json
from lab.db import initialize
from lab.collector import collect_forever
from lab.scenarios import execute
from lab.bootstrap import bootstrap

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['init','collect','scenario','seed'])
    parser.add_argument('--scenario',choices=['lock_wait','sql_load'])
    parser.add_argument('--duration',type=int,default=25)
    parser.add_argument('--url',default='http://zabbix-web:8080/api_jsonrpc.php')
    parser.add_argument('--password-file')
    args=parser.parse_args()
    if args.action=='init': result=initialize()
    elif args.action=='collect': return collect_forever()
    elif args.action=='scenario': result=execute(args.scenario,args.duration)
    else: result=bootstrap(args.url,args.password_file)
    print(json.dumps(result,default=str))
if __name__=='__main__': main()
