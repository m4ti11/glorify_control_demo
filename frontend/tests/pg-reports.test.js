import assert from 'node:assert/strict';
import test from 'node:test';
import { queryRows, queryMetric, rankQueries, formatReportValue, reportDay } from '../pg-reports.js';
test('captured query ranking sorts cumulative time and filters SQL text', () => {
  const reports = [{ database: 'orders', available: true, pg_stat_statements: { available: true, rows: [{ queryid: '1', query: 'SELECT id FROM orders', calls: 10, total_exec_ms: 50 }, { queryid: '2', query: 'SELECT id FROM customers', calls: 20, total_exec_ms: 80 }] } }];
  const rows = queryRows(reports);
  assert.equal(rows.length, 2);
  assert.equal(queryMetric(rows[0], 'total'), 50);
  assert.equal(rankQueries(rows, 'total')[0].queryid, '2');
  assert.equal(rankQueries(rows, 'calls', 'orders').length, 2);
  assert.notEqual(formatReportValue(null), '0');
});
test('report date follows the reporting timezone across UTC midnight', () => {
  assert.equal(reportDay(new Date('2026-01-15T01:00:00Z')), '2026-01-13');
});
