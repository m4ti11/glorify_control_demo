import assert from 'node:assert/strict';
import test from 'node:test';
import { emailLabel, filterEvents } from '../events.js';
test('notification filters distinguish pending and published provider states', () => {
  const items = [{ event_id: 'one', subject: { id: 'worker' }, notification: { status: 'PENDING' } }, { event_id: 'two', subject: { id: 'api' }, notification: { status: 'PUBLISHED' } }];
  assert.equal(emailLabel({ status: 'PENDING' }), 'Pending publication');
  assert.deepEqual(filterEvents(items, 'WORKER', 'pending').map(item => item.event_id), ['one']);
  assert.deepEqual(filterEvents(items, 'api', 'pending'), []);
});
