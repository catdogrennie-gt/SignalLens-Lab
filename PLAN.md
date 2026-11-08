# SignalLens Lab implementation plan

Goal: independently reproduce real MySQL lock waits and bounded SQL load, measure the VM/database/probe, and supply tagged Zabbix evidence.
Architecture: MySQL8.4.13 isolated internal network; Python3.12.10 probe joins monitoring network; offline pinned wheels. The approved formal real-system plan Task4 is the specification.

Constraints: new VM only; no formal app writes, control DB access, emails, Git commits, synthetic telemetry, or published secrets. Two real cases use finally rollback/close/stop and dataset checks. No network fault claimed.

- [x] core.py guards, reset/gap math, sender framing and cleanup tests.
- [x] db.py seeds deterministic dataset and separate monitor/scenario grants.
- [x] collector.py reads actual MySQL performance_schema/status, VM /proc counters, authenticated connection/SELECT1 timing.
- [x] bootstrap.py seeds only Lab group hosts/items/triggers with explicit semantics.
- [x] scenarios.py saves actual baseline/fault/negative-control/recovery samples and cleanup.
- [x] Deploy isolated Compose; prove real samples accepted, both scenarios and trigger problem/recovery events.
- [x] Verify tests, secret exclusions, record exact commands and limitations.

Review focus: absent marker/UUID reject; sender partial response reject; reset/gap unknown; cleanup on timeout; errors preserved and no fallback values; monitor credentials separate from write identity.
