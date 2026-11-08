"""Deterministic target guards, counter math, sender protocol and cleanup."""
import json
import socket
import struct
from contextlib import contextmanager


def guard(cfg, observed):
    required = {'container': 'signallens-lab-target', 'owner': 'signallens-lab',
                'role': 'target', 'database': 'lab_orders', 'marker': 'signallens-lab-v1'}
    if any(cfg.get(k) != v or observed.get(k) != v for k, v in required.items()):
        raise ValueError('Target allowlist/identity marker rejected')
    if not cfg.get('server_uuid') or observed.get('server_uuid') != cfg['server_uuid']:
        raise ValueError('Target UUID rejected')


def delta(previous, current, elapsed, max_gap=30):
    if previous is None or elapsed <= 0 or elapsed > max_gap or current < previous:
        return None
    return (current - previous) / elapsed


def frame(payload):
    body = json.dumps(payload, separators=(',', ':')).encode()
    return b'ZBXD\x01' + struct.pack('<Q', len(body)) + body


def recv_exact(sock, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = sock.recv(size - len(chunks))
        if not part:
            raise ConnectionError('Incomplete Zabbix sender response')
        chunks.extend(part)
    return bytes(chunks)


def send(rows, host='zabbix-server', port=10051):
    with socket.create_connection((host, port), timeout=5) as sock:
        sock.settimeout(5)
        sock.sendall(frame({'request': 'sender data', 'data': rows}))
        header = recv_exact(sock, 13)
        if header[:5] != b'ZBXD\x01':
            raise ValueError('Unexpected sender response framing')
        length = struct.unpack('<Q', header[5:])[0]
        if length > 1048576:
            raise ValueError('Oversized sender response')
        result = json.loads(recv_exact(sock, length))
        if result.get('response') != 'success' or 'failed: 0' not in result.get('info', ''):
            raise RuntimeError('Zabbix sender rejected data: ' + str(result))
        return result


@contextmanager
def managed_connections(factory):
    connections = []
    def acquire():
        conn = factory()
        connections.append(conn)
        return conn
    try:
        yield acquire
    finally:
        failures = []
        for conn in reversed(connections):
            try:
                conn.rollback()
            except Exception as exc:
                failures.append(exc)
            finally:
                try:
                    conn.close()
                except Exception as exc:
                    failures.append(exc)
        if failures:
            raise RuntimeError('Connection cleanup failed') from failures[0]
