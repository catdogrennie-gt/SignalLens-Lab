import json
import struct
import unittest
from lab.core import guard, delta, frame, managed_connections

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.cfg = dict(container='signallens-lab-target', owner='signallens-lab',
                        role='target',database='lab_orders',marker='signallens-lab-v1',server_uuid='uuid')
    def test_guard_rejects_control_db_unknown_uuid_marker_labels(self):
        guard(self.cfg, self.cfg)
        for field, value in [('database','zabbix'),('server_uuid','unknown'),('marker',None),('owner','other')]:
            changed = {**self.cfg, field:value}
            with self.assertRaises(ValueError): guard(self.cfg, changed)
        with self.assertRaises(ValueError): guard({}, {})
    def test_counter_reset_gap_and_initial_are_unknown(self):
        self.assertEqual(delta(10,20,2),5)
        for args in [(None,1,1),(20,10,2),(1,2,31),(1,2,0)]:
            self.assertIsNone(delta(*args))
    def test_sender_framing(self):
        payload={'request':'sender data','data':[{'value':'实测'}]}
        data=frame(payload)
        self.assertEqual(data[:5],b'ZBXD\x01')
        self.assertEqual(struct.unpack('<Q',data[5:13])[0],len(data[13:]))
        self.assertEqual(json.loads(data[13:]),payload)
    def test_cleanup_on_timeout(self):
        class Conn:
            def __init__(self): self.events=[]
            def rollback(self): self.events.append('rollback')
            def close(self): self.events.append('close')
        conn=Conn()
        with self.assertRaises(TimeoutError):
            with managed_connections(lambda:conn) as acquire:
                acquire()
                raise TimeoutError()
        self.assertEqual(conn.events,['rollback','close'])
    def test_rollback_failure_still_closes_every_connection(self):
        events=[]
        class Conn:
            def rollback(self): events.append('rollback'); raise OSError('disconnect')
            def close(self): events.append('close')
        with self.assertRaises(RuntimeError):
            with managed_connections(Conn) as acquire: acquire(); acquire()
        self.assertEqual(events,['rollback','close','rollback','close'])

if __name__=='__main__': unittest.main()
