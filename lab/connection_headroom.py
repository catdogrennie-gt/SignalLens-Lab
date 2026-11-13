"""Fixed SELECT-only connection occupancy; no analysis or arbitrary execution.

The global scenario controller owns this workload's short renewable lease. A
separate dedicated MAX_USER_CONNECTIONS account is provisioned by the operator.
"""
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import threading
import time

import pymysql

HOLD_COUNT = 25
EXPECTED_CAPACITY = 40
MIN_SPARE = 4
INITIAL_SPARE = 6


class FixedConnectionHeadroom:
    def __init__(self, config, *, connector=None, monotonic=None):
        if not isinstance(config.get('expected_server_uuid'), str) or not config['expected_server_uuid']:
            raise ValueError('pinned_server_identity_required')
        if config.get('expected_capacity') != EXPECTED_CAPACITY:
            raise ValueError('fixed_capacity_required')
        hold_count = config.get('hold_count', HOLD_COUNT)
        if type(hold_count) is not int or not 1 <= hold_count <= HOLD_COUNT:
            raise ValueError('invalid_fixed_hold_count')
        self.hold_count = hold_count
        self.config = config
        self.connector = connector or self._connect
        self.clock = monotonic or time.monotonic
        self.lock, self.tick_lock = threading.RLock(), threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.held = []
        self.held_generation, self.closing, self.cleanup_unknown_until = None, 0, 0
        self.mode, self.owner, self.expires = 'normal', None, 0
        self.generation, self.failed, self.opening = 0, False, False
        self.last_keepalive = 0

    def _connect(self, *, observer):
        prefix = 'monitor' if observer else 'database'
        return pymysql.connect(host=self.config['database_host'], port=3306,
            user=self.config[prefix+'_user'], password=self.config[prefix+'_password'],
            autocommit=True, connect_timeout=1, read_timeout=1, write_timeout=1,
            init_command='SET SESSION wait_timeout=20',
            cursorclass=pymysql.cursors.DictCursor)

    def current_mode(self):
        return self.mode if self.clock() < self.expires else 'normal'

    def control(self, mode, owner, ttl=12):
        if (mode not in ('normal', 'connections') or not isinstance(owner, str)
                or not 16 <= len(owner) <= 80 or type(ttl) not in (int, float)
                or not 1 <= ttl <= 15):
            raise ValueError('invalid_fixed_control')
        with self.lock:
            if self.owner and self.owner != owner and self.current_mode() != 'normal':
                raise ValueError('another_owner_is_active')
            if mode == 'connections' and self.failed:
                raise ValueError('unsafe_headroom_requires_off')
            if mode != self.mode or self.owner != owner:
                self.generation += 1
            self.mode, self.owner, self.expires = mode, owner, self.clock()+ttl
            if mode == 'normal':
                self.failed = False
        return {'accepted': True}

    def state(self):
        with self.lock:
            return {'normal': self.current_mode() == 'normal' and not self.held and not self.opening
                    and not self.closing and self.clock() >= self.cleanup_unknown_until,
                    'held_connections': len(self.held)+self.closing, 'held_read_view': False}

    def _current(self, generation):
        with self.lock:
            return (not self.stop.is_set() and not self.failed
                    and self.generation == generation and self.current_mode() == 'connections')

    def _measure(self):
        connection = self.connector(observer=True)
        try:
            with connection.cursor() as cursor:
                cursor.execute('SELECT @@server_uuid AS server_uuid, @@max_connections AS capacity')
                identity = cursor.fetchone()
                cursor.execute("SHOW GLOBAL STATUS LIKE 'Threads_connected'")
                count = cursor.fetchone()
            capacity, connected = int(identity['capacity']), int(count['Value'])
            if (identity['server_uuid'] != self.config['expected_server_uuid']
                    or capacity != EXPECTED_CAPACITY or not 0 <= connected <= capacity):
                raise ValueError('target_identity_or_capacity_mismatch')
            return capacity, connected
        finally:
            connection.close()

    def _require_spare(self, additional, *, min_spare=MIN_SPARE):
        capacity, connected = self._measure()
        if connected + additional > capacity - min_spare:
            raise ValueError('monitoring_headroom_not_available')

    def _release(self):
        with self.lock:
            held, self.held = self.held, []
            self.held_generation = None
            self.closing += len(held)
        for connection in held:
            try:
                connection.close()
            except Exception:
                # Server-side wait_timeout is a second cleanup boundary.
                with self.lock:
                    self.cleanup_unknown_until = max(self.cleanup_unknown_until, self.clock()+20)
            finally:
                with self.lock:
                    self.closing -= 1

    def tick(self):
        if not self.tick_lock.acquire(blocking=False):
            return
        try:
            with self.lock:
                generation = self.generation
                active = self._current(generation)
                self.opening = active and not self.held
                old_generation = self.held_generation is not None and self.held_generation != generation
            if old_generation:
                self._release()
            if not active:
                self._release()
                return
            if not self.held:
                self._require_spare(self.hold_count, min_spare=INITIAL_SPARE)
                for index in range(self.hold_count):
                    if not self._current(generation):
                        return
                    connection = self.connector(observer=False)
                    try:
                        with connection.cursor() as cursor:
                            cursor.execute('SET SESSION wait_timeout=20')
                            cursor.execute('SELECT 1')
                        with self.lock:
                            if self._current(generation):
                                self.held.append(connection)
                                self.held_generation = generation
                                connection = None
                    finally:
                        if connection is not None:
                            connection.close()
                    if (index+1) % 5 == 0:
                        self._require_spare(0)
            self._require_spare(0)
            now = self.clock()
            if now-self.last_keepalive >= 5:
                for connection in list(self.held):
                    if not self._current(generation):
                        return
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT 1')
                self.last_keepalive = now
        except Exception:
            with self.lock:
                self.failed = True
            self._release()
        finally:
            if not self._current(generation):
                self._release()
            with self.lock:
                self.opening = False
            self.tick_lock.release()

    def start(self):
        if self.thread is not None:
            raise RuntimeError('already_started')
        def maintain():
            try:
                while not self.stop.is_set():
                    self.tick()
                    self.stop.wait(.5)
            finally:
                self._release()
        self.thread = threading.Thread(target=maintain, daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=8)
            if self.thread.is_alive():
                raise RuntimeError('cleanup_thread_not_stopped')
        self._release()


def main():
    config = json.loads(Path(os.environ.get('CONNECTIONS_CONFIG',
        '/run/secrets/connections.json')).read_text(encoding='utf-8'))
    workload = FixedConnectionHeadroom(config)
    workload.start()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def send(self, status, value):
            raw = json.dumps(value, separators=(',', ':')).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def allowed(self):
            return hmac.compare_digest(self.headers.get('Authorization', ''),
                                       'Bearer '+config['control_token'])
        def do_GET(self):
            if self.path == '/health':
                return self.send(200, {'ready': True})
            if self.path == '/control' and self.allowed():
                return self.send(200, workload.state())
            self.send(403, {'error': 'forbidden'})
        def do_PUT(self):
            if self.path != '/control' or not self.allowed():
                return self.send(403, {'error': 'forbidden'})
            try:
                size = int(self.headers.get('Content-Length', 0))
                if not 0 < size < 1024:
                    raise ValueError()
                data = json.loads(self.rfile.read(size))
                if set(data) != {'mode', 'owner', 'ttl'}:
                    raise ValueError()
                self.send(200, workload.control(**data))
            except (ValueError, TypeError):
                self.send(409, {'error': 'fixed_control_rejected'})
    try:
        ThreadingHTTPServer(('0.0.0.0', 8080), Handler).serve_forever()
    finally:
        workload.close()


if __name__ == '__main__':
    main()
