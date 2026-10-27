import assert from 'node:assert/strict';
import test from 'node:test';
import { cpuWindowLabel, latestPostgresSample, postgresConnectionSegments, postgresConnectionTotals, sortContainers, sortPostgresConnections, usagePercent, filterContainers, renderContainers } from '../metrics.js';

test('container filters combine state and image search while including unhealthy running services', () => {
  const items = [
    { metrics: { name: 'api', image: 'glorify/backend', state: 'running', health: 'unhealthy', restart_count: 0 } },
    { metrics: { name: 'worker', image: 'glorify/jobs', state: 'exited', restart_count: 2 } },
    { metrics: { name: 'db', image: 'postgres:16', state: 'running', health: 'healthy', restart_count: 0 } },
  ];
  assert.deepEqual(filterContainers(items, 'attention').map(item => item.metrics.name), ['api', 'worker']);
  assert.deepEqual(filterContainers(items, 'running', 'POSTGRES').map(item => item.metrics.name), ['db']);
  assert.deepEqual(filterContainers(items, 'stopped').map(item => item.metrics.name), ['worker']);
  assert.deepEqual(filterContainers(items, 'stopped', 'api'), []);
  assert.equal(items.length, 3);
});

test('connection segments account for other PostgreSQL states and stay within the total', () => {
  const values = connection => postgresConnectionSegments(connection).map(segment => segment.value);
  assert.deepEqual(values({ connections: 12, active: 3, idle: 7 }), [3, 7, 2]);
  assert.deepEqual(values({ connections: 0, active: 0, idle: 0 }), [0, 0, 0]);
  assert.deepEqual(values({ connections: 4 }), [0, 0, 4]);
  assert.deepEqual(values({ connections: 4, active: 8, idle: 2 }), [4, 0, 0]);
  assert.deepEqual(values({ connections: 4, active: -1, idle: NaN }), [0, 0, 4]);
});

test('labels the actual CPU window, including delayed collection', () => {
  assert.equal(cpuWindowLabel({ method: 'cumulative_counters', window_seconds: 119.99 }), 'Average over the last 2 min');
  assert.equal(cpuWindowLabel({ method: 'cumulative_counters', window_seconds: 240 }), 'Average over the last 4 min');
  assert.equal(cpuWindowLabel({ method: 'cumulative_counters', window_seconds: 125 }), 'Average over the last 125 s');
});

test('legacy readings never claim to be two-minute averages', () => {
  for (const cpu of [undefined, {}, { method: 'instant', window_seconds: 120 }, { method: 'cumulative_counters', window_seconds: 0 }, { method: 'cumulative_counters', window_seconds: '120' }]) {
    assert.equal(cpuWindowLabel(cpu), 'Measurement window unavailable');
  }
});

test('container ranking puts missing measurements last, preserves input, and supports alternate sorts', () => {
  const resources = [
    { metrics: { name: 'api', cpu_percent: 12, memory_usage_mb: 400, restart_count: 0 } },
    { metrics: { name: 'worker', cpu_percent: 140, memory_usage_mb: 100, restart_count: 3 } },
    { metrics: { name: 'stopped', cpu_percent: null, memory_usage_mb: null } },
    { metrics: { name: 'idle', cpu_percent: 0, memory_usage_mb: 0, restart_count: 0 } },
  ];
  const names = sort => sortContainers(resources, sort).map(resource => resource.metrics.name);
  assert.deepEqual(names('cpu'), ['worker', 'api', 'idle', 'stopped']);
  assert.deepEqual(names('memory'), ['api', 'worker', 'idle', 'stopped']);
  assert.deepEqual(names('restarts'), ['worker', 'api', 'idle', 'stopped']);
  assert.deepEqual(names('name'), ['api', 'idle', 'stopped', 'worker']);
  assert.equal(resources[0].metrics.name, 'api');
  assert.deepEqual(sortContainers([]), []);
});

test('chart fills stay in range for missing and multicore readings', () => {
  assert.deepEqual([null, undefined, NaN, Infinity, -5, 45, 240].map(usagePercent), [0, 0, 0, 0, 0, 45, 100]);
});

test('uses the newest available PostgreSQL heartbeat sample and totals its databases', () => {
  const postgres = { samples: [
    { timestamp: '2026-10-22T12:00:00Z', available: true, connections: [{ database: 'app', connections: 3, active: 1, idle: 2 }] },
    { timestamp: '2026-10-22T12:01:00Z', available: false, connections: [] },
    { timestamp: '2026-10-22T12:02:00Z', available: true, connections: [{ database: 'app', connections: 5, active: 2, idle: 3 }, { database: 'jobs', connections: 2, active: 1, idle: 1 }] },
  ] };
  const sample = latestPostgresSample(postgres);
  assert.equal(sample.timestamp, '2026-10-22T12:02:00Z');
  assert.deepEqual(postgresConnectionTotals(sample), { connections: 7, active: 3, idle: 4 });
  assert.deepEqual(sortPostgresConnections(sample.connections).map(connection => connection.database), ['app', 'jobs']);
  assert.equal(latestPostgresSample({ samples: [{ available: false, connections: [] }] }), null);
  assert.equal(postgresConnectionTotals(null), null);
});


test('unknown container CPU and memory render as Unavailable without a fake usage bar', () => {
  const all = [];
  const element = () => ({ children: [], style: {}, textContent: '', append(...nodes) { this.children.push(...nodes); }, setAttribute() {}, replaceChildren(...nodes) { this.children = nodes; } });
  const ids = new Map();
  const doc = { getElementById(id) { if (!ids.has(id)) ids.set(id, element()); return ids.get(id); }, createElement() { const node = element(); all.push(node); return node; }, createElementNS() { return element(); } };
  renderContainers(doc, [{ metrics: { name: 'app', state: 'running', cpu_percent: null, memory_usage_mb: null, restart_count: 0 } }]);
  assert.equal(all.filter(node => node.textContent === 'Unavailable').length, 2);
  assert.equal(all.filter(node => node.className?.includes('resource-bar')).length, 0);
});
