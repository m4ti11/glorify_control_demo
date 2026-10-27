import assert from 'node:assert/strict';
import test from 'node:test';
import { formData, configurationSummary, CONFIGURATION_KINDS, ConfigurationPanel, METRICS, validateConfiguration } from '../configuration.js';

const field = (value, checked = false) => ({ value: String(value), checked });
const common = () => ({ name: field(' API '), enabled: field('', true), cooldown_seconds: field(1800), notify_recovery: field('', true), maintenance_until: field('') });

test('health-check form preserves failures, recovery, destination references and maintenance', () => {
  const fields = { ...common(), url: field('https://example.com/health'), method: field('GET'), interval_seconds: field(60), timeout_seconds: field(5), failure_count: field(3), recovery_count: field(2), expected_status_codes: field('200, 204') };
  const data = formData('health-checks', fields, ['id']);
  assert.equal(data.name, 'API'); assert.equal(data.enabled, true);
  assert.deepEqual(data.expected_status_codes, [200, 204]); assert.equal(data.timeout_seconds, 5);
  assert.deepEqual(data.notification_channel_ids, ['id']); assert.equal(data.maintenance_until, null);
});

test('only implemented host rules can be offered and submitted', () => {
  assert.deepEqual(Object.keys(METRICS), ['cpu_percent', 'memory_percent', 'disk_percent']);
  const base = { resource_type: 'host', resource_id: 'host', metric: 'cpu_percent', threshold: 80, recovery_threshold: 70, operator: '>=' };
  assert.equal(validateConfiguration('alert-rules', base), base);
  for (const change of [{ resource_type: 'container' }, { resource_type: 'cpu_core' }, { resource_type: 'interface' }, { resource_type: 'disk' }, { resource_id: 'other' }, { metric: 'network_rx_bytes_per_second' }, { metric: 'network_tx_bytes_per_second' }]) {
    assert.throws(() => validateConfiguration('alert-rules', { ...base, ...change }), /host/i);
  }
});

test('email form accepts lines, commas or semicolons, without another hardcoded destination', () => {
  const data = formData('notification-channels', { name: field('Operaciones'), enabled: field('', true), emails: field('one@example.com\ntwo@example.com, three@example.com; four@example.com') });
  assert.deepEqual(data.emails, ['one@example.com', 'two@example.com', 'three@example.com', 'four@example.com']);
  assert.equal('notification_channel_ids' in data, false);
});


test('constructing the demo configuration panel needs no session', () => {
  const doc = { getElementById: () => ({ addEventListener() {} }) };
  assert.doesNotThrow(() => new ConfigurationPanel({}, doc));
});
