"""Bounded structured application logs; no SQL, exception text or control mode."""
from collections import deque
from datetime import datetime,timezone
import threading
import time
from uuid import uuid4


class ApplicationEventLog:
    def __init__(self,*,epoch=None,started=None,capacity=4096):
        self.epoch=epoch or uuid4().hex
        self.started=time.time() if started is None else started
        self.events=deque(maxlen=capacity)
        self.lock=threading.RLock()
        self.sequence=0;self.evicted_at=None
        self.summary_at=self.started;self.count=0;self.errors=0
        self.maximum=0;self.db_maximum=0;self.pool_maximum=0

    def _append(self,now,kind,severity,**values):
        self.sequence+=1
        if len(self.events)==self.events.maxlen:self.evicted_at=self.events[0]['event_epoch']
        self.events.append({'event_epoch':now,'event_id':self.epoch+':'+str(self.sequence),
            'server_event_at':datetime.fromtimestamp(now,timezone.utc).isoformat(),
            'event_kind':kind,'severity':severity,**values})

    def record(self,*,request_ms,db_ms,pool_wait_ms,failed,error_code=None,now=None):
        now=time.time() if now is None else now
        with self.lock:
            self.count+=1;self.errors+=int(failed)
            self.maximum=max(self.maximum,request_ms)
            self.db_maximum=max(self.db_maximum,db_ms or 0)
            self.pool_maximum=max(self.pool_maximum,pool_wait_ms)
            if failed or request_ms>=200:
                code='mysql_'+str(error_code) if type(error_code)is int and 0<=error_code<100000 else 'request_failed' if failed else None
                self._append(now,'request_failed' if failed else 'request_slow','error' if failed else 'warning',
                    request_ms=round(request_ms,3),db_ms=round(db_ms,3)if db_ms is not None else None,
                    pool_wait_ms=round(pool_wait_ms,3),error_code=code)
            if now-self.summary_at>=60:
                self._append(now,'request_window_summary','info',request_count=self.count,error_count=self.errors,
                    request_max_ms=round(self.maximum,3),db_max_ms=round(self.db_maximum,3),
                    pool_wait_max_ms=round(self.pool_maximum,3),window_seconds=round(now-self.summary_at,3),
                    interval_start_at=datetime.fromtimestamp(self.summary_at,timezone.utc).isoformat(),
                    interval_end_at=datetime.fromtimestamp(now,timezone.utc).isoformat())
                self.summary_at=now;self.count=0;self.errors=0
                self.maximum=0;self.db_maximum=0;self.pool_maximum=0

    def snapshot(self,*,now=None,limit=100):
        now=time.time()if now is None else now
        with self.lock:
            rows=[dict(r)for r in self.events if now-300<=r['event_epoch']<=now]
            boundary=[r for r in rows if r['event_kind']=='request_window_summary' and datetime.fromisoformat(r['interval_start_at']).timestamp()<now-300]
            rows=[r for r in rows if r not in boundary]
            count=len(rows);gap=[]
            if self.evicted_at is not None and self.evicted_at>=now-300:gap.append('ring_evicted_inside_window')
            if self.started>now-300:gap.append('process_started_inside_window')
            if boundary:gap.append('summary_crosses_window_boundary')
            return {'producer_contract':'db-evidence-application-events-v1','query_name':'application_logs',
                'rows':rows[-limit:],'snapshot_complete':count<=limit,'snapshot_truncated':count>limit,
                'returned_row_count':min(count,limit+1),'capture_gaps':gap,'window_seconds':300,
                'capture_started_at':datetime.fromtimestamp(self.started,timezone.utc).isoformat()}
