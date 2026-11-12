"""Generate private, reproducible credentials and bounded fixture SQL locally."""
import argparse
import json
from pathlib import Path
import secrets


def render_init(c):
    return f"""CREATE DATABASE IF NOT EXISTS workload;
CREATE USER IF NOT EXISTS 'app'@'%' IDENTIFIED BY '{c['app']}' WITH MAX_USER_CONNECTIONS 8;
CREATE USER IF NOT EXISTS 'monitor'@'%' IDENTIFIED BY '{c['monitor']}' WITH MAX_USER_CONNECTIONS 12;
GRANT SELECT ON workload.* TO 'app'@'%';
GRANT REPLICATION CLIENT,PROCESS,SHOW DATABASES,SHOW VIEW ON *.* TO 'monitor'@'%';
GRANT SELECT ON performance_schema.* TO 'monitor'@'%';
CREATE TABLE IF NOT EXISTS workload.orders(id INT PRIMARY KEY,customer_id INT,amount INT,status VARCHAR(20),payload VARCHAR(1100));
SET SESSION cte_max_recursion_depth=5000;
INSERT IGNORE INTO workload.orders WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<4000) SELECT n,MOD(n,173),MOD(n*137,100000),'pending',REPEAT(CHAR(65+MOD(n,26)),1024) FROM seq;
CREATE TABLE IF NOT EXISTS workload.ledger(id INT PRIMARY KEY,version BIGINT NOT NULL,updated_at DATETIME(6));
INSERT IGNORE INTO workload.ledger VALUES(1,0,UTC_TIMESTAMP(6));
GRANT UPDATE ON workload.ledger TO 'app'@'%';
"""


def prepare(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    path=folder/'credentials.private.json'
    if path.exists():credentials=json.loads(path.read_text())
    else:
        credentials={k:{field:secrets.token_urlsafe(32) for field in ('root','app','monitor','control')} for k in 'abc'}
        path.write_text(json.dumps(credentials,indent=2),encoding='utf-8')
    for k,c in credentials.items():
        if k not in 'abcd' or not isinstance(c,dict):continue
        (folder/(k+'-root')).write_text(c['root'],encoding='utf-8')
        (folder/(k+'-init.sql')).write_text(render_init(c),encoding='utf-8')
        if k in 'ab':
            app={'database_host':'db-'+k,'database_user':'app','database_password':c['app'],
                 'monitor_user':'monitor','monitor_password':c['monitor'],'control_token':c['control']}
            (folder/(k+'-app.json')).write_text(json.dumps(app),encoding='utf-8')
    return {'business_databases':3,'applications':2,'fixture_rows_per_database':4000}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--private-root',default='.runtime/private')
    print(json.dumps(prepare(parser.parse_args().private_root)))
