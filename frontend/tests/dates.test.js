import assert from 'node:assert/strict';
import test from 'node:test';
import { formatDay, formatDateTime } from '../dates.js';

test('calendar dates use ISO format without timezone shifts', () => {
  assert.equal(formatDay('2026-10-26'), '2026-10-26');
  assert.equal(formatDay('2027-01-01'), '2027-01-01');
  assert.equal(formatDay(''), 'YYYY-MM-DD');
});
test('timestamps use the reporting timezone across midnight and year boundaries', () => {
  assert.equal(formatDateTime('2027-01-01T01:30:00Z'), '2026-12-31 22:30');
  assert.equal(formatDateTime('2026-10-26T15:05:00Z'), '2026-10-26 12:05');
  assert.equal(formatDateTime('invalid'), 'Unavailable');
});
