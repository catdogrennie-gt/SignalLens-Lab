import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch
from lab.scenarios import execute
from run_scenario import validate_container
from lab.export import public_manifest

class Boundaries(unittest.TestCase):
    def test_export_drops_private_identity_and_unknown_fields(self):
        result=public_manifest({'source_mode':'live_lab','target':{'password':'secret','server_uuid':'private-uuid'},'passwords':{'root':'secret'},'observations':[{'timestamp':'t','clock':1,'phase':'fault','values':{'sl.mysql.lock_waits':1},'provenance':{'target_uuid':'private-uuid'},'unknown':'secret'}]})
        serialized=json.dumps(result)
        self.assertNotIn('secret',serialized)
        self.assertNotIn('private-uuid',serialized)
        self.assertEqual(result['observations'][0]['values']['sl.mysql.lock_waits'],1)
    def test_actual_container_label_and_network_allowlist(self):
        data={'Name':'/signallens-lab-target','Config':{'Labels':{'sl.owner':'signallens-lab','sl.role':'target','com.docker.compose.project':'signallens-lab'}},'HostConfig':{'NetworkMode':'signallens-lab_lab'},'NetworkSettings':{'Networks':{'signallens-lab_lab':{}}}}
        validate_container(data)
        for mutate in [lambda x:x['Config']['Labels'].update({'sl.role':'control'}),lambda x:x['NetworkSettings']['Networks'].update({'monitoring':{}}),lambda x:x['HostConfig'].update({'NetworkMode':'host'})]:
            invalid=copy.deepcopy(data); mutate(invalid)
            with self.assertRaises(ValueError): validate_container(invalid)
    def test_scenario_bounds_rejected_before_connect(self):
        with patch('lab.scenarios.config') as cfg:
            for kind,duration in [('lock_wait',46),('lock_wait',9),('network',25)]:
                with self.assertRaises(ValueError): execute(kind,duration)
            cfg.assert_not_called()
    def test_private_files_excluded_and_no_runtime_export_of_passwords(self):
        self.assertIn('.runtime/',Path('.gitignore').read_text())
        private=Path('.runtime/private/target.json')
        if private.exists():
            secrets=json.loads(private.read_text())['passwords'].values()
            for file in Path('.runtime/runs').glob('*.json'):
                for secret in secrets: self.assertNotIn(secret,file.read_text())

if __name__=='__main__': unittest.main()
