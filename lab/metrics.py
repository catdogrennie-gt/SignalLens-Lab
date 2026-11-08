"""Explicit Zabbix binding definitions. No display-name inference."""
HOSTS = {'db': 'sl-lab-db', 'host': 'sl-lab-host', 'net': 'sl-lab-net'}
# key, semantic, kind, Zabbix value_type, units
ITEMS = {
 'db': [('sl.mysql.lock_waits','lock_waits','metric',3,''),
        ('sl.mysql.qps','queries_per_second','metric',0,'qps'),
        ('sl.mysql.threads_running','threads_running','metric',3,''),
        ('sl.mysql.lock_snapshot','lock_snapshot','lock_snapshot',4,''),
        ('sl.mysql.sql_summary','sql_summary','sql_summary',4,'')],
 'host': [('sl.host.cpu_pct','cpu_percent','metric',0,'%'),
          ('sl.host.disk_read_bps','disk_read_bytes_per_second','metric',0,'Bps'),
          ('sl.host.disk_write_bps','disk_write_bytes_per_second','metric',0,'Bps')],
 'net': [('sl.net.mysql_latency_ms','latency_ms','metric',0,'ms'),
         ('sl.net.mysql_available','availability','metric',3,'')]
}
