import assert from 'node:assert/strict';
import test from 'node:test';
import { shiftReportDay } from '../reports.js';
test('date navigation handles month and year boundaries', () => {
  assert.equal(shiftReportDay('2026-01-01', -1), '2025-12-31');
  assert.equal(shiftReportDay('2024-02-28', 1), '2024-02-29');
  assert.equal(shiftReportDay('invalid', 1), 'invalid');
});
