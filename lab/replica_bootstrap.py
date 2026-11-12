"""Prepare private replica files; never connects or changes a server by itself."""
import argparse
import json
from pathlib import Path
import re
import secrets
from .scenario_bootstrap import prepare,render_init


def prepare_replica(folder,source_log_file=None,source_log_position=None):
    prepare(folder)
    folder=Path(folder);path=folder/'credentials.private.json'
    credentials=json.loads(path.read_text(encoding='utf-8'))
    if 'd' not in credentials:
        credentials['d']={k:secrets.token_urlsafe(32) for k in ['root','app','monitor','control']}
    # MySQL replication passwords are limited to 32 characters.
    if 'replication' not in credentials:credentials['replication']=secrets.token_urlsafe(24)
    if len(credentials['replication'])>32:raise ValueError('Replication password exceeds MySQL limit')
    path.write_text(json.dumps(credentials,indent=2),encoding='utf-8')
    c,d=credentials['c'],credentials['d']
    (folder/'d-root').write_text(d['root'],encoding='utf-8')
    (folder/'d-init.sql').write_text(render_init(d),encoding='utf-8')
    (folder/'c-app.json').write_text(json.dumps({'database_host':'db-d','database_user':'app',
        'database_password':d['app'],'monitor_user':'monitor','monitor_password':d['monitor'],
        'control_token':c['control'],'source_host':'db-c','source_user':'app','source_password':c['app']}),encoding='utf-8')
    (folder/'c-replication.sql').write_text(
        "CREATE USER IF NOT EXISTS 'replica'@'%' IDENTIFIED BY '"+credentials['replication']+"' WITH MAX_USER_CONNECTIONS 2;\n"
        "GRANT REPLICATION SLAVE ON *.* TO 'replica'@'%';\n"
        "GRANT SELECT ON workload.ledger TO 'monitor'@'%';\n"
        "SET GLOBAL binlog_expire_logs_seconds=3600;\nSET GLOBAL max_binlog_size=8388608;\n",encoding='utf-8')
    if (source_log_file is None)!=(source_log_position is None):raise ValueError('Both actual source coordinates are required')
    if source_log_file is not None:
        if not re.fullmatch(r'[A-Za-z0-9_.-]+',source_log_file) or source_log_position<4:raise ValueError('Invalid actual source coordinates')
        (folder/'d-replication.sql').write_text("GRANT SELECT ON workload.ledger TO 'monitor'@'%';\n"
            "CHANGE REPLICATION SOURCE TO SOURCE_HOST='db-c',SOURCE_USER='replica',SOURCE_PASSWORD='"+
            credentials['replication']+"',SOURCE_LOG_FILE='"+source_log_file+"',SOURCE_LOG_POS="+str(source_log_position)+
            ",GET_SOURCE_PUBLIC_KEY=1;\nSTART REPLICA;\n",encoding='utf-8')
    return {'prepared':True,'coordinates_configured':source_log_file is not None}


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--private-root',default='.runtime/private')
    p.add_argument('--source-log-file');p.add_argument('--source-log-position',type=int)
    a=p.parse_args();print(json.dumps(prepare_replica(a.private_root,a.source_log_file,a.source_log_position)))
