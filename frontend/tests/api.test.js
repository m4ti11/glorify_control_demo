import assert from 'node:assert/strict';
import test from 'node:test';
import { readFile } from 'node:fs/promises';
const requests = [];
globalThis.fetch = async url => {
  assert.equal(url.protocol, 'file:');
  assert.match(url.pathname, /\/frontend\/demo\/[a-z-]+\.json$/);
  requests.push(url.pathname);
  return { ok: true, json: async () => JSON.parse(await readFile(url, 'utf8')) };
};
const { api } = await import('../api.js');
test('latest is fresh, varies across refreshes and does not mutate the source', async () => {
  const first = await api.latest();
  first.resources[0].metrics.disk.percent = 99;
  const second = await api.latest();
  assert.equal(second.system_id, 'demo-vps-01');
  assert.ok(Date.now() - Date.parse(second.measured_at) < 20000);
  assert.notEqual(first.resources[0].metrics.cpu_percent, second.resources[0].metrics.cpu_percent);
  assert.equal(second.resources[0].metrics.disk.percent, 68.5);
});
test('history spans 24h with a load spike, disk recovery and a worker restart', async () => {
  const { items } = await api.history();
  assert.equal(Date.parse(items.at(-1).measured_at) - Date.parse(items[0].measured_at), 86400000);
  assert.ok(items.some(item => item.cpu_percent > 85));
  assert.ok(items.some(item => item.disk_percent >= 90));
  assert.ok(items.at(-1).disk_percent < 80);
  assert.equal(items.at(-1).worker_restarts, 1);
});
test('event pages preserve filters and expose restart and recovery', async () => {
  const first = await api.events();
  assert.equal(first.items.length, 10);
  assert.ok(first.items.some(item => item.event_type === 'container.started'));
  assert.ok(first.items.some(item => item.details.metric === 'disk_percent'));
  const next = await api.events(first.filters, first.next_cursor);
  assert.equal(new Set([...first.items, ...next.items].map(item => item.event_id)).size, 17);
  const empty = await api.events({ from: '2000-01-01', to: '2000-01-02' });
  assert.equal(empty.items.length, 0);
});
test('each configuration kind supports in-memory CRUD and optimistic versions', async () => {
  for (const kind of ['alert-rules', 'health-checks', 'notification-channels']) {
    const before = await api.configuration(kind);
    const original = before.items[0];
    const id = 'temporary-' + kind;
    await api.configuration(kind, null, null, { method: 'POST', body: JSON.stringify({ id, data: { ...original.data, name: 'Session example' } }) });
    const created = (await api.configuration(kind)).items.find(item => item.id === id);
    assert.equal(created.version, 1);
    await api.configuration(kind, id, null, { method: 'PUT', body: JSON.stringify({ version: 1, data: { ...created.data, name: 'Edited example' } }) });
    await assert.rejects(api.configuration(kind, id, null, { method: 'PUT', body: JSON.stringify({ version: 1, data: created.data }) }), /changed/);
    await api.configuration(kind, id, null, { method: 'DELETE' });
    assert.equal((await api.configuration(kind)).items.length, before.items.length);
    assert.equal(JSON.parse(await readFile(new URL('../demo/' + kind + '.json', import.meta.url), 'utf8')).items.length, before.items.length);
  }
});
test('invalid thresholds and referenced channel deletion are rejected', async () => {
  const rule = (await api.configuration('alert-rules')).items[0];
  await assert.rejects(api.configuration('alert-rules', rule.id, null, { method: 'PUT', body: JSON.stringify({ data: { ...rule.data, threshold: 50, recovery_threshold: 60 } }) }), /margin/);
  await assert.rejects(api.configuration('notification-channels', 'demo-channel', null, { method: 'DELETE' }), /references/);
});
test('a new module session restores fixtures and report dates are explicit', async () => {
  const { api: fresh } = await import('../api.js?fresh-session');
  const data = await fresh.configuration('health-checks');
  assert.equal(data.items.length, 1);
  const report = await api.operationalReport();
  assert.equal(report.system_id, 'demo-vps-01');
  assert.match(report.analysis.summary, /no model was called/);
  await assert.rejects(api.pgReport('2000-01-01'), /one sample/);
  assert.ok(requests.every(path => path.includes('/demo/')));
});
