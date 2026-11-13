from lab.application_event_log import ApplicationEventLog


def test_normal_requests_produce_a_real_window_summary_without_claiming_a_fault():
    log=ApplicationEventLog(epoch='one',started=0)
    for n in range(60):log.record(request_ms=3,db_ms=2,pool_wait_ms=1,failed=False,now=n+1)
    result=log.snapshot(now=60)
    assert len(result['rows'])==1
    assert result['rows'][0]['event_kind']=='request_window_summary'
    assert result['rows'][0]['request_count']==60
    assert result['rows'][0]['error_count']==0
    assert result['rows'][0]['severity']=='info'


def test_slow_and_failed_requests_have_stable_ids_times_and_no_free_text():
    log=ApplicationEventLog(epoch='one',started=0)
    log.record(request_ms=300,db_ms=250,pool_wait_ms=1,failed=False,now=1)
    log.record(request_ms=600,db_ms=None,pool_wait_ms=600,failed=True,error_code=1205,now=2)
    a=log.snapshot(now=3);b=log.snapshot(now=4)
    assert a['rows']==b['rows']
    assert [r['event_kind'] for r in a['rows']]==['request_slow','request_failed']
    assert a['rows'][1]['error_code']=='mysql_1205'
    assert 'message' not in str(a) and 'sql_text' not in str(a)


def test_snapshot_is_bounded_and_discloses_truncation_and_eviction():
    log=ApplicationEventLog(epoch='one',started=0,capacity=3)
    for n in range(5):log.record(request_ms=300,db_ms=200,pool_wait_ms=1,failed=False,now=n+1)
    result=log.snapshot(now=6,limit=2)
    assert len(result['rows'])==2 and result['returned_row_count']==3
    assert result['snapshot_truncated'] is True
    assert result['capture_gaps']==['ring_evicted_inside_window','process_started_inside_window']
    assert log.snapshot(now=400)['rows']==[]


def test_late_summary_does_not_import_an_old_failure_into_a_new_window():
    log=ApplicationEventLog(epoch='one',started=0)
    log.record(request_ms=600,db_ms=200,pool_wait_ms=1,failed=True,now=1)
    log.record(request_ms=3,db_ms=2,pool_wait_ms=1,failed=False,now=400)
    result=log.snapshot(now=400)
    assert result['rows']==[]
    assert result['capture_gaps']==['summary_crosses_window_boundary']
