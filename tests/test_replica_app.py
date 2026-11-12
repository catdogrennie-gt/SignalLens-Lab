from lab.replica_app import ReplicaWorkload,replication_observations
from lab.replica_bootstrap import prepare_replica
from lab.scenario_bootstrap import prepare
import json
import pytest


def test_missing_replication_measurement_is_unknown_not_zero_lag():
    assert replication_observations(None)=={'replica_io_running':None,'replica_sql_running':None,'replica_lag_seconds':None}
    measured=replication_observations({'Replica_IO_Running':'Connecting','Replica_SQL_Running':'Yes','Seconds_Behind_Source':None})
    assert measured=={'replica_io_running':0,'replica_sql_running':1,'replica_lag_seconds':None}


def test_replica_application_restricts_modes_and_keeps_observations_separate():
    work=ReplicaWorkload({})
    work.control('replica','owner-1234567890123')
    metrics=work.metrics()
    assert metrics['read_version_gap'] is None
    assert not {'mode','owner','scenario','expected_root_cause'}.intersection(metrics)
    assert work.allowed_modes=={'normal','replica'}


def test_private_replica_bootstrap_reuses_credentials_and_needs_real_coordinates(tmp_path):
    result=prepare_replica(tmp_path)
    before=(tmp_path/'credentials.private.json').read_text()
    assert result=={'prepared':True,'coordinates_configured':False}
    assert not (tmp_path/'d-replication.sql').exists()
    assert len(json.loads(before)['replication'])<=32
    prepare(tmp_path) # Base preparation stays compatible with the extension.
    prepare_replica(tmp_path,'binlog.000002',12345)
    assert (tmp_path/'credentials.private.json').read_text()==before
    assert 'SOURCE_LOG_POS=12345' in (tmp_path/'d-replication.sql').read_text()
    with pytest.raises(ValueError):prepare_replica(tmp_path,'bad; DROP',12345)
