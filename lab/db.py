import json
import hashlib
from pathlib import Path
import pymysql
from lab.core import guard

CONFIG = Path('.runtime/private/target.json')

def config():
    return json.loads(CONFIG.read_text())

def connect(cfg, identity='monitor', autocommit=True):
    return pymysql.connect(host='target',port=3306,user=identity,
        password=cfg['passwords'][identity],database='lab_orders',
        autocommit=autocommit,connect_timeout=5,read_timeout=8,write_timeout=5,
        cursorclass=pymysql.cursors.DictCursor)

def identity(cfg):
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute('SELECT @@server_uuid AS server_uuid')
        observed=cur.fetchone()
        cur.execute('SELECT * FROM lab_orders.identity_marker WHERE id=1')
        observed.update(cur.fetchone() or {})
    guard(cfg,observed)
    return observed

def initialize():
    cfg=config()
    root_password=Path('/run/secrets/lab-root-password').read_text().strip()
    with pymysql.connect(host='target',user='root',password=root_password,
                         autocommit=True,cursorclass=pymysql.cursors.DictCursor) as conn, conn.cursor() as cur:
        cur.execute('CREATE DATABASE IF NOT EXISTS lab_orders')
        cur.execute('CREATE TABLE IF NOT EXISTS lab_orders.identity_marker (id INT PRIMARY KEY, marker VARCHAR(64), container VARCHAR(64), owner VARCHAR(64), role VARCHAR(64), `database` VARCHAR(64))')
        cur.execute("INSERT IGNORE INTO lab_orders.identity_marker VALUES (1,'signallens-lab-v1','signallens-lab-target','signallens-lab','target','lab_orders')")
        cur.execute('CREATE TABLE IF NOT EXISTS lab_orders.orders (id INT PRIMARY KEY, customer_id INT NOT NULL, amount_cents INT NOT NULL, status VARCHAR(16) NOT NULL)')
        cur.executemany('INSERT IGNORE INTO lab_orders.orders VALUES (%s,%s,%s,%s)',[(i,i%100,(i*137)%100000,'pending') for i in range(1,10001)])
        for user in ('monitor','scenario'):
            cur.execute('CREATE USER IF NOT EXISTS %s@\'%%\' IDENTIFIED WITH caching_sha2_password BY %s',(user,cfg['passwords'][user]))
        cur.execute('GRANT SELECT ON lab_orders.* TO monitor@\'%\'')
        cur.execute('GRANT SELECT ON performance_schema.* TO monitor@\'%\'')
        cur.execute('GRANT PROCESS ON *.* TO monitor@\'%\'')
        cur.execute('GRANT SELECT, UPDATE ON lab_orders.orders TO scenario@\'%\'')
        cur.execute('SELECT @@server_uuid AS uuid')
        cfg['server_uuid']=cur.fetchone()['uuid']
    CONFIG.write_text(json.dumps(cfg,indent=2))
    return identity(cfg)

def dataset_digest(cfg):
    with connect(cfg) as conn, conn.cursor() as cur:
        cur.execute('SELECT COUNT(*) AS row_count, SUM(amount_cents) AS amount_sum, BIT_XOR(CRC32(CONCAT_WS(\':\',id,customer_id,amount_cents,status))) AS checksum FROM orders')
        result={k:str(v) for k,v in cur.fetchone().items()}
        cur.execute('SELECT id,customer_id,amount_cents,status FROM orders ORDER BY id')
        digest=hashlib.sha256()
        for row in cur.fetchall(): digest.update((json.dumps(row,sort_keys=True,separators=(',',':'))+'\n').encode())
        result['ordered_sha256']=digest.hexdigest()
        return result
