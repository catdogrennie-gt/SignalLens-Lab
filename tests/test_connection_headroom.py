import pytest

from lab.connection_headroom import FixedConnectionHeadroom


class Connection:
    def __init__(self, world, observer):
        self.world, self.observer, self.closed = world, observer, False
        self.statements = []
    def cursor(self): return self
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql): self.statements.append(sql)
    def fetchone(self):
        if self.statements[-1].startswith('SELECT @@server_uuid'):
            return {'server_uuid':self.world.uuid,'capacity':self.world.capacity}
        return {'Variable_name':'Threads_connected','Value':str(self.world.count)}
    def close(self):
        if not self.closed:
            self.closed=True
            if not self.observer:self.world.count-=1


class World:
    def __init__(self):
        self.count,self.capacity,self.uuid,self.calls,self.fail_at=9,40,'actual-fixed-server',[],None
    def connect(self, *, observer):
        if not observer and self.fail_at is not None and len([c for c in self.calls if not c.observer])>=self.fail_at:
            raise OSError('simulated_connection_failure')
        c=Connection(self,observer);self.calls.append(c)
        if not observer:self.count+=1
        return c


@pytest.fixture
def holder():
    world=World();clock=[100.]
    h=FixedConnectionHeadroom({'expected_server_uuid':world.uuid,'expected_capacity':40},
        connector=world.connect,monotonic=lambda:clock[0])
    yield h,world,clock
    h.close()


def test_fixed_connections_repeated_on_is_idempotent_and_off_releases(holder):
    h,w,_=holder
    h.control('connections','owner-1234567890123',12);h.tick()
    assert h.state()=={'normal':False,'held_connections':25,'held_read_view':False}
    assert w.count==34
    h.control('connections','owner-1234567890123',12);h.tick()
    assert len([c for c in w.calls if not c.observer])==25
    h.control('normal','owner-1234567890123',1);h.tick()
    assert h.state()['normal']is True and w.count==9
    assert all(c.closed for c in w.calls)


@pytest.mark.parametrize('damage',['capacity','identity','headroom'])
def test_preflight_rejects_wrong_or_unsafe_target_before_allocating(holder,damage):
    h,w,_=holder
    if damage=='capacity':w.capacity=41
    elif damage=='identity':w.uuid='another-server'
    else:w.count=12
    h.control('connections','owner-1234567890123',12);h.tick()
    assert not [c for c in w.calls if not c.observer]
    assert h.state()['held_connections']==0
    with pytest.raises(ValueError):h.control('connections','owner-1234567890123',12)


def test_preflight_requires_six_spare_slots_not_only_runtime_floor(holder):
    h,w,_=holder
    w.count=10  # 10 + 25 would leave only five slots, despite the runtime floor of four.
    h.control('connections','owner-1234567890123',12);h.tick()
    assert not [c for c in w.calls if not c.observer]
    assert h.state()['held_connections']==0
    with pytest.raises(ValueError):h.control('connections','owner-1234567890123',12)


def test_partial_connect_failure_releases_every_session_and_latches_until_off(holder):
    h,w,_=holder;w.fail_at=7
    h.control('connections','owner-1234567890123',12);h.tick()
    assert w.count==9 and all(c.closed for c in w.calls)
    with pytest.raises(ValueError):h.control('connections','owner-1234567890123',12)
    h.control('normal','owner-1234567890123',1);h.tick()
    w.fail_at=None
    h.control('connections','owner-1234567890123',12);h.tick()
    assert w.count==34


def test_lease_expiry_and_lost_headroom_are_real_cleanup_boundaries(holder):
    h,w,clock=holder
    h.control('connections','owner-1234567890123',12);h.tick()
    clock[0]=113;h.tick()
    assert w.count==9 and h.state()['normal']is True
    h.control('connections','owner-1234567890123',12);h.tick()
    w.count+=3;h.tick()
    assert h.state()['held_connections']==0 and w.count==12


def test_foreign_owner_cannot_cancel_and_no_write_sql_is_issued(holder):
    h,w,_=holder
    h.control('connections','owner-1234567890123',12);h.tick()
    with pytest.raises(ValueError):h.control('normal','different-owner-12345',1)
    assert all(sql.startswith(('SELECT ','SHOW ','SET SESSION ')) for c in w.calls for sql in c.statements)


def test_stopping_during_allocation_does_not_leave_an_untracked_session(holder):
    h,w,_=holder;real=w.connect
    def stop_during(*,observer):
        c=real(observer=observer)
        if not observer:h.control('normal','owner-1234567890123',1)
        return c
    h.connector=stop_during
    h.control('connections','owner-1234567890123',12);h.tick()
    assert w.count==9 and h.state()['normal']is True


def test_off_on_during_allocation_keeps_the_cleanup_barrier(holder):
    h,w,_=holder;real=w.connect;changed=[False]
    def off_on(*,observer):
        c=real(observer=observer)
        if not observer and h.state()['held_connections'] and not changed[0]:
            changed[0]=True
            h.control('normal','owner-1234567890123',1)
            h.control('connections','owner-1234567890123',12)
        return c
    h.connector=off_on
    h.control('connections','owner-1234567890123',12);h.tick()
    assert w.count==9 and h.state()['held_connections']==0
    h.tick()
    assert w.count==34 and h.state()['held_connections']==25


@pytest.mark.parametrize('count',[0,-1,26,True,23.0,'23',None])
def test_private_hold_count_must_be_an_integer_within_original_maximum(count):
    with pytest.raises(ValueError,match='invalid_fixed_hold_count'):
        FixedConnectionHeadroom({'expected_server_uuid':'actual-fixed-server',
                                 'expected_capacity':40,'hold_count':count})


def test_smaller_private_load_keeps_six_spare_slots_and_repeated_on_is_bounded(holder):
    _,w,clock=holder;w.count=11
    h=FixedConnectionHeadroom({'expected_server_uuid':w.uuid,'expected_capacity':40,
                              'hold_count':23},connector=w.connect,monotonic=lambda:clock[0])
    try:
        h.control('connections','owner-1234567890123',12);h.tick()
        assert h.state()['held_connections']==23 and w.count==34
        h.control('connections','owner-1234567890123',12);h.tick()
        assert len([c for c in w.calls if not c.observer])==23
        h.control('normal','owner-1234567890123',1);h.tick()
        assert h.state()['normal']is True and w.count==11
    finally:h.close()


def test_smaller_private_load_still_refuses_insufficient_initial_headroom(holder):
    _,w,clock=holder;w.count=12
    h=FixedConnectionHeadroom({'expected_server_uuid':w.uuid,'expected_capacity':40,
                              'hold_count':23},connector=w.connect,monotonic=lambda:clock[0])
    try:
        h.control('connections','owner-1234567890123',12);h.tick()
        assert h.state()['held_connections']==0 and w.count==12
        with pytest.raises(ValueError):h.control('connections','owner-1234567890123',12)
    finally:h.close()
