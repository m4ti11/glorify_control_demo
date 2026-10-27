#!/usr/bin/env python3
"""Generate deterministic fictional fixtures; never contact AWS or a database."""
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import uuid5, NAMESPACE_URL
import copy
import json
import random
import sys

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
SYSTEM = 'demo-vps-01'
RNG = random.Random(417)
def stamp(seconds=0):
    return (NOW + timedelta(seconds=seconds)).isoformat().replace('+00:00', 'Z')
def save(relative, value):
    path = ROOT / relative; path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + '\n')
def fixture(name, value):
    save('frontend/demo/' + name + '.json', value)
def event(kind, seconds, subject='host', details=None):
    return {'record_type': 'event', 'schema_version': 1, 'event_id': str(uuid5(NAMESPACE_URL, f'demo/{kind}/{seconds}')),
            'system_id': SYSTEM, 'event_type': kind, 'source': 'docker' if subject == 'worker' else 'agent',
            'occurred_at': stamp(seconds), 'subject': {'type': 'container' if subject == 'worker' else 'host', 'id': subject}, 'details': details or {}}
def main():
    host = {'hostname': SYSTEM, 'uptime_seconds': 1287600, 'cpu_percent': 31.5,
            'memory': {'total_mb': 8192, 'used_mb': 4587.5, 'percent': 56.0},
            'disk': {'path': '/', 'total_gb': 160.0, 'used_gb': 109.6, 'percent': 68.5},
            'extended': {'cpu': {'method': 'cumulative_counters', 'window_seconds': 120}}}
    containers = [{'name': name, 'image': name + ':demo', 'state': 'running', 'health': 'healthy', 'started_at': stamp(-7200 if name == 'worker' else -86400),
                   'restart_count': 1 if name == 'worker' else 0, 'cpu_percent': cpu, 'memory_usage_mb': mem, 'memory_limit_mb': 2048, 'memory_percent': round(mem / 2048 * 100, 1)}
                  for name, cpu, mem in [('api', 9.2, 610), ('web', 2.1, 180), ('worker', 13.2, 780), ('postgres', 6.0, 1240), ('redis', 1.2, 96)]]
    postgres = {'samples': [{'timestamp': stamp(-20), 'available': True, 'connections': [{'database': 'orders', 'connections': 18, 'active': 6, 'idle': 11}, {'database': 'customers', 'connections': 7, 'active': 2, 'idle': 5}]}]}
    payload = {'schema_version': 1, 'system_id': SYSTEM, 'agent_version': '0.1.1', 'timestamp': stamp(-12), 'host': host,
               'docker': {'available': True, 'running_containers': 5, 'total_containers': 5, 'containers': containers}, 'postgres': postgres}
    snapshot = {'system_id': SYSTEM, 'measured_at': stamp(-12), 'received_at': stamp(-9),
                'resources': [{'resource_type': 'HOST', 'resource_id': 'host', 'metrics': host}, *[{'resource_type': 'CONTAINER', 'resource_id': c['name'], 'metrics': c} for c in containers], {'resource_type': 'POSTGRES', 'resource_id': 'postgres', 'metrics': postgres}]}
    fixture('metrics-latest', snapshot)
    history = []
    for i in range(289):
        cpu = 26 + 8 * __import__('math').sin(i / 15) + RNG.uniform(-2, 2)
        if 150 <= i <= 160: cpu = 87 + RNG.uniform(-2, 5)
        history.append({'measured_at': stamp(-86400 + i * 300), 'cpu_percent': round(cpu, 1), 'disk_percent': 91 if 180 <= i <= 195 else 68.5, 'worker_restarts': int(i >= 264)})
    fixture('metrics-history', {'system_id': SYSTEM, 'items': history})
    events = [event('host.alert.recovered', -22000, details={'metric': 'disk_percent', 'value': 68.5, 'threshold': 90, 'recovery_threshold': 80}),
              event('host.alert.triggered', -28000, details={'metric': 'disk_percent', 'value': 91, 'threshold': 90}),
              event('container.started', -7200, 'worker', {'restart_count': 1}), event('container.exited', -7260, 'worker', {'exit_code': 137, 'oom_killed': True}),
              event('host.alert.triggered', -40000, details={'metric': 'cpu_percent', 'value': 92}), event('host.alert.recovered', -38000, details={'metric': 'cpu_percent', 'value': 31})]
    events += [event('agent.heartbeat', -i * 4900, details={'status': 'observed'}) for i in range(1, 12)]
    events.sort(key=lambda e: e['occurred_at'], reverse=True)
    for i, item in enumerate(events):
        item['notification'] = {'status': 'PENDING' if i == 0 else 'PUBLISHED', 'email_status': 'SIMULATED', 'email_recipient': 'admin@example.com'}
    fixture('events', {'items': events, 'next_cursor': None})
    queries = [{'queryid': '101', 'query': 'SELECT id, total FROM orders WHERE customer_id = $1 ORDER BY created_at DESC LIMIT $2', 'calls': 1280, 'rows': 14200, 'total_exec_ms': 57600, 'mean_exec_ms': 45},
               {'queryid': '102', 'query': 'SELECT id, email FROM customers WHERE id = $1', 'calls': 4600, 'rows': 4600, 'total_exec_ms': 9200, 'mean_exec_ms': 2},
               {'queryid': '103', 'query': 'SELECT count(*) FROM orders WHERE status = $1', 'calls': 320, 'rows': 320, 'total_exec_ms': 12480, 'mean_exec_ms': 39}]
    section = lambda rows: {'available': True, 'rows': rows}
    pg = event('postgres.daily_metrics', -7200, details={})
    pg.update(source='postgres', subject={'type': 'system', 'id': SYSTEM})
    pg['details'] = {'scope': 'system', 'database': 'all', 'available': True, 'report_date': '2026-01-14', 'timezone': 'America/Argentina/Buenos_Aires', 'captured_at': stamp(-7200),
                     'reports': [{'scope': 'database', 'database': 'orders', 'available': True, 'pg_stat_statements': section(queries),
                                  'database_stats': section([{'datname': 'orders', 'xact_commit': 9400, 'xact_rollback': 2, 'blks_hit': 83200}]),
                                  'tables': section([{'relname': 'orders', 'n_live_tup': 18000, 'n_dead_tup': 240}, {'relname': 'customers', 'n_live_tup': 3800, 'n_dead_tup': 12}])},
                                 {'scope': 'cluster', 'database': 'cluster', 'available': True, 'connections': section(postgres['samples'][0]['connections']), 'locks': section([]), 'blocked': section([])}]}
    fixture('pg-statements', {'report': pg})
    report = {'schema_version': 1, 'system_id': SYSTEM, 'report_date': '2026-01-14', 'timezone': 'America/Argentina/Buenos_Aires', 'generated_at': stamp(-5400),
              'source_status': {'host_metrics': {'status': 'available', 'count': 720, 'coverage': {'percentage': 100, 'present_intervals': 720, 'expected_intervals': 720, 'expected_interval_seconds': 120, 'largest_gap_seconds': 120, 'sufficient': True}}, 'events': {'status': 'available', 'count': 17}, 'postgresql': {'status': 'available', 'count': 1}},
              'analysis': {'summary': 'Fictional sample: a short CPU peak coincided with background work. Disk usage reached 91% and returned to 68.5% after cleanup. The worker restarted once after an OOM exit. This text is authored for the demo; no model was called.',
                           'findings': [{'severity': 'high', 'title': 'Disk threshold crossed and recovered', 'description': 'Usage exceeded the 90% threshold. Later samples were below the 80% recovery threshold.', 'evidence_refs': ['host:disk', 'event:disk-recovery']}, {'severity': 'medium', 'title': 'Worker restarted once', 'description': 'The fictional worker exited with code 137 and then restarted.', 'evidence_refs': ['event:worker-exit']}],
                           'recommendations': [{'priority': 'medium', 'action': 'Review the orders query plan before adding an index', 'description': 'Compare representative plans and cardinalities; cumulative query timings do not prove a daily regression.', 'evidence_refs': ['postgres:101']} ]}}
    fixture('operational-report', report)
    common = {'enabled': True, 'cooldown_seconds': 1800, 'notify_recovery': True, 'maintenance_until': None, 'notification_channel_ids': ['demo-channel']}
    def item(kind, id, data): return {'kind': kind, 'id': id, 'version': 1, 'updated_at': stamp(-3600), 'data': data, 'delivery': {'status': 'SIMULATED'}}
    fixture('alert-rules', {'items': [item('alert-rules', 'demo-' + metric, dict(common, name=name, system_id=SYSTEM, resource_type='host', resource_id='host', metric=metric, operator='>=', threshold=threshold, recovery_threshold=recovery, duration_seconds=180, consecutive_samples=3)) for metric, name, threshold, recovery in [('cpu_percent', 'Sustained CPU load', 80, 65), ('memory_percent', 'Memory pressure', 85, 75), ('disk_percent', 'Disk usage', 90, 80)]]})
    fixture('health-checks', {'items': [item('health-checks', 'demo-site', dict(common, name='Example API (configuration only)', url='https://example.com/health', method='GET', interval_seconds=60, timeout_seconds=5, failure_count=3, recovery_count=2, expected_status_codes=[200]))]})
    fixture('notification-channels', {'items': [item('notification-channels', 'demo-channel', {'name': 'Example operations', 'enabled': True, 'emails': ['admin@example.com']})]})
    # Contract fixtures use fixed calendar dates so boundary tests never depend on now.
    contract_heartbeat = copy.deepcopy(payload)
    connections = [{'database': name, 'connections': 6, 'active': 1, 'idle': 4} for name in ['orders', 'customers', 'postgres', 'template0', 'template1', None]]
    contract_heartbeat['postgres']['samples'] = [{'timestamp': stamp(-80), 'available': True, 'connections': connections}, {'timestamp': stamp(-20), 'available': True, 'connections': connections}]
    for path in ['samples/payloads/heartbeat.json', 'agent/examples/heartbeat.json', 'backend/lambda/ingest_metrics/test/payload.json']: save(path, contract_heartbeat)
    agent_pg = copy.deepcopy(pg)
    agent_pg['details']['report_date'] = '2026-10-22'
    row = agent_pg['details']['reports'][0]['pg_stat_statements']['rows'][0]
    row.update(queryid='101', collected_at='2026-10-23T03:00:00+00:00', stats_reset='2026-10-01T00:00:00+00:00', min_exec_ms=1.0, max_exec_ms=90.0, stddev_exec_ms=8.0)
    for path in ['samples/payloads/daily-pg-statements.json', 'agent/examples/daily-pg-statements.json']: save(path, agent_pg)
    single_pg = copy.deepcopy(pg)
    single_pg['details'] = dict(copy.deepcopy(pg['details']['reports'][0]), report_date='2026-10-19', timezone='America/Argentina/Buenos_Aires')
    single_pg['details']['top_total'] = section([{'queryid': '7823456789012345678', 'query': 'SELECT id FROM orders WHERE id = $1', 'calls': 20, 'total_exec_time': 400.0}])
    save('backend/lambda/ingest_pg_statements/test/payload.json', single_pg)
    restart = event('container.exited', -7260, 'worker', {'exit_code': 137, 'oom_killed': True})
    restart['occurred_at'] = '2026-10-17T20:15:32.123Z'
    for path in ['samples/payloads/container-restart.json', 'backend/lambda/process_events/test/payload.json']: save(path, restart)
    save('samples/reports/operational-report.json', report)
    output = ROOT / 'samples/notifications/disk-recovery.txt'; output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text('SIMULATED NOTIFICATION - no message was sent\nTo: admin@example.com\nSystem: demo-vps-01\nDisk usage recovered from 91% to 68.5%.\n')
    sys.path.insert(0, str(ROOT / 'backend/shared'))
    from report_pdf import render_pdf
    # Fixed metadata makes sample generation byte-for-byte reproducible.
    from reportlab import rl_config
    rl_config.invariant = 1
    pdf = render_pdf(report)
    for relative in ['frontend/demo/operational-report.pdf', 'samples/reports/operational-report.pdf']:
        path = ROOT / relative; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(pdf)
    print('Generated eight JSON datasets, payloads, notification and fictional PDF.')
if __name__ == '__main__': main()
