import assert from 'node:assert/strict';
import test from 'node:test';
import { operationalReportDay } from '../operational-reports.js';
import { api } from '../api.js';
test('report date and PDF remain local and explicit', () => {
  assert.equal(operationalReportDay(new Date('2026-01-15T01:00:00Z')), '2026-01-13');
  assert.match(api.pdfUrl(), /\/demo\/operational-report\.pdf$/);
});
