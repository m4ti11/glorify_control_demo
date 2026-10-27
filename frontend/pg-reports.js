import { formatDateTime } from './dates.js';
import { api } from './api.js';
import { uiIcon } from './ui-icons.js';

const ZONE = 'America/Argentina/Buenos_Aires';

export function reportDay(now = new Date(), daysAgo = 1) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const values = Object.fromEntries(parts.map(part => [part.type, part.value]));
  return new Date(Date.UTC(Number(values.year), Number(values.month) - 1, Number(values.day) - daysAgo)).toISOString().slice(0, 10);
}

export function formatReportValue(value, column) {
  if (value == null) return '—';
  if (typeof value === 'boolean') return value ? 'Yes' : 'No';
  if (typeof value === 'number') {
    if (column === 'size_bytes') return `${(value / 1024 ** 3).toLocaleString('en-US', { maximumFractionDigits: 2 })} GiB`;
    const number = value.toLocaleString('en-US', { maximumFractionDigits: (/exec_(time|ms)/.test(column)) ? 3 : 2 });
    return number + ((/exec_(time|ms)/.test(column)) ? ' ms' : column.endsWith('percent') ? ' %' : '');
  }
  if (typeof value === 'object') return Array.isArray(value) ? value.map(item => formatReportValue(item, column)).join(' · ') : Object.entries(value).map(([key, item]) => `${COLUMNS[key] || key}: ${formatReportValue(item, key)}`).join(' · ');
  if (['stats_reset', 'last_autovacuum', 'last_autoanalyze'].includes(column)) return formatDateTime(value);
  return String(value);
}

const COLUMNS = {
  datname: 'Database', size_bytes: 'Size', numbackends: 'Connections', blks_hit: 'Cached blocks', blks_read: 'Read blocks', cache_hit_percent: 'Cache hit rate', deadlocks: 'Deadlocks', stats_reset: 'Statistics reset',
  schemaname: 'Schema', relname: 'Table', n_live_tup: 'Live rows (estimated)', n_dead_tup: 'Dead rows (estimated)', last_autovacuum: 'Last autovacuum', autovacuum_count: 'Autovacuums', last_autoanalyze: 'Last autoanalyze',
  dbid: 'Database ID', userid: 'User ID', queryid: 'Query ID', calls: 'Calls', total_exec_time: 'Total time', mean_exec_time: 'Mean time', rows: 'Rows',
  query: 'SQL query', total_exec_ms: 'Total time', mean_exec_ms: 'Mean time', min_exec_ms: 'Minimum time', max_exec_ms: 'Maximum time', stddev_exec_ms: 'Time deviation', shared_blks_hit: 'Cached blocks', shared_blks_read: 'Read blocks', shared_blks_dirtied: 'Dirtied blocks', shared_blks_written: 'Written blocks', temp_blks_read: 'Temporary blocks read', temp_blks_written: 'Temporary blocks written', blk_read_time: 'Read time (ms)', blk_write_time: 'Write time (ms)', collected_at: 'Captured', postgres_version: 'PostgreSQL version', toplevel: 'Top-level query',
  total: 'Total', count: 'Count', mode: 'Mode', granted: 'Granted', state: 'State',
};
const SECTIONS = [['database_stats', 'Database'], ['tables', 'Tables and maintenance'], ['top_total', 'Queries by total time'], ['top_mean', 'Queries by mean time'], ['connection_totals', 'Total connections'], ['connections', 'Connections by state'], ['locks', 'Locks'], ['blocked', 'Blocked processes']];

export function queryMetric(row, metric) {
  const aliases = { total: ['total_exec_ms', 'total_exec_time', 'total_time'], mean: ['mean_exec_ms', 'mean_exec_time', 'mean_time'], calls: ['calls'] };
  for (const key of aliases[metric] || []) if (Number.isFinite(row[key])) return row[key];
  return null;
}

export function queryRows(reports) {
  const rows = new Map();
  for (const report of reports) {
    if (report.available === false) continue;
    for (const key of ['pg_stat_statements', 'top_total', 'top_mean']) {
      if (report[key]?.available !== true) continue;
      for (const [index, row] of (report[key].rows || []).entries()) {
        const database = row.database || (report.scope === 'cluster' ? row.datname : report.database) || 'Database unavailable';
        const identity = JSON.stringify([database, row.userid, row.queryid ?? row.query ?? row.funcname ?? [key, index], row.toplevel]);
        const previous = rows.get(identity);
        rows.set(identity, { ...previous, ...row, database });
      }
    }
  }
  return [...rows.values()];
}

export function rankQueries(rows, sort = 'total', search = '') {
  const query = search.trim().toLocaleLowerCase('en');
  return rows.filter(row => [row.database, row.query, row.funcname, row.queryid].filter(value => value != null).join(' ').toLocaleLowerCase('en').includes(query))
    .sort((a, b) => (queryMetric(b, sort) ?? -Infinity) - (queryMetric(a, sort) ?? -Infinity) || String(a.queryid ?? a.query ?? '').localeCompare(String(b.queryid ?? b.query ?? '')));
}

const duration = value => value == null ? '—' : value >= 1000 ? `${(value / 1000).toLocaleString('en-US', { maximumFractionDigits: 2 })} s` : `${value.toLocaleString('en-US', { maximumFractionDigits: 2 })} ms`;

export class PgReportsPanel {
  constructor(browser = window, doc = document) {
       this.browser = browser; this.doc = doc;
    this.$ = id => doc.getElementById(id);
    this.controller = null; this.enabled = false;
    this.$('pg-day').value = reportDay(); this.$('pg-day').max = reportDay(new Date(), 0);
    this.$('pg-form').addEventListener('submit', event => { event.preventDefault(); this.load(); });
  }

  setSystem(systemId) {
    if (!this.$('pg-system').value) { this.$('pg-system').value = systemId; if (true) this.load(); }
  }

  setEnabled(value) {
    if (!value && (this.enabled || this.controller)) {
      this.controller?.abort(); this.controller = null;
      this.$('pg-results').replaceChildren(); this.$('pg-results').hidden = true;
      this.$('pg-notice').hidden = true; this.$('pg-system').value = '';
      this.$('pg-status').textContent = 'Select a date to load a sample report.';
      this.busy(false);
    }
    this.enabled = value;
  }

  busy(value) { this.$('pg-search').disabled = value; this.$('pg-panel').setAttribute('aria-busy', String(value)); }

  async load() {
    const generation = this.generation = (this.generation || 0) + 1;
    const prefix = 'pg';
    this.busy(true); this.$(prefix + '-notice').hidden = true;
    try {
      const day = this.$(prefix + '-day').value;
      const result = await api.pgReport(day);
      if (generation !== this.generation) return;
      this.render(result);
    } catch (error) {
      this.$(prefix + '-status').textContent = 'No sample available for the selected date.';
      this.$(prefix + '-notice').textContent = error.message;
      this.$(prefix + '-notice').hidden = false;
    } finally { if (generation === this.generation) this.busy(false); }
  }

  element(tag, text, className) {
    const element = this.doc.createElement(tag); if (text != null) element.textContent = text;
    if (className) element.className = className; return element;
  }

  facts(entries, className = 'report-facts') {
    const facts = this.element('dl', null, className);
    for (const [label, value] of entries) { const fact = this.element('div'); fact.append(this.element('dt', label), this.element('dd', value)); facts.append(fact); }
    return facts;
  }

  async copySql(text, button, message) {
    try {
      await this.browser.navigator.clipboard.writeText(text);
      button.replaceChildren(uiIcon(this.doc, 'check'));
      button.setAttribute('aria-label', 'Query copied'); button.title = 'Copied';
      message.textContent = 'Query copied to clipboard.';
      setTimeout(() => { button.replaceChildren(uiIcon(this.doc, 'copy')); button.setAttribute('aria-label', 'Copy SQL query'); button.title = 'Copy SQL query'; message.textContent = ''; }, 1800);
    } catch {
      message.textContent = 'Could not copy. Select the SQL text manually.';
      button.title = 'Could not copy';
    }
  }

  queryRanking(reports) {
    const root = this.element('section', null, 'query-ranking');
    const rows = queryRows(reports);
    const captured = reports.some(item => item.available !== false && ['pg_stat_statements', 'top_total', 'top_mean'].some(key => item[key]?.available));
    const knownCalls = rows.filter(row => Number.isFinite(row.calls));
    const knownTimes = rows.map(row => queryMetric(row, 'total')).filter(value => value != null);
    const means = rows.map(row => queryMetric(row, 'mean')).filter(value => value != null);
    root.append(this.facts([
      ['Captured queries', captured ? rows.length : '—'],
      ['Calls in capture', knownCalls.length ? knownCalls.reduce((sum, row) => sum + row.calls, 0).toLocaleString('en-US') : '—'],
      ['Cumulative time · capture', knownTimes.length ? duration(knownTimes.reduce((sum, value) => sum + value, 0)) : '—'],
      ['Highest mean time', means.length ? duration(Math.max(...means)) : '—'],
    ], 'report-kpis'));
    const controls = this.element('div', null, 'query-controls');
    const label = this.element('label', null, 'search-field');
    const search = this.element('input'); search.type = 'search'; search.placeholder = 'Search SQL, function, database or ID…'; search.setAttribute('aria-label', 'Search captured queries'); label.append(search);
    const sort = this.element('div', null, 'query-sort'); sort.setAttribute('role', 'group'); sort.setAttribute('aria-label', 'Sort queries');
    const buttons = []; let selected = 'total';
    const count = this.element('p', null, 'muted report-caption'); count.setAttribute('role', 'status');
    const list = this.element('div', null, 'query-list');
    const render = () => {
      const ranked = rankQueries(rows, selected, search.value);
      buttons.forEach(([key, button]) => button.setAttribute('aria-pressed', String(key === selected)));
      count.textContent = `${ranked.length} of ${rows.length} captured queries · Highest first`;
      const cards = ranked.map((row, index) => {
        const card = this.element('article', null, 'query-card');
        const heading = this.element('div', null, 'query-card-heading');
        heading.append(this.element('span', String(index + 1).padStart(2, '0'), 'query-rank'), this.element('h3', row.database), this.element('small', row.queryid != null ? `ID ${row.queryid}` : 'No query ID', 'muted'));
        card.append(heading, this.facts([
          ['Total time', duration(queryMetric(row, 'total'))], ['Mean per call', duration(queryMetric(row, 'mean'))],
          ['Calls', row.calls != null ? formatReportValue(row.calls, 'calls') : '—'], ['Rows', formatReportValue(row.rows, 'rows')],
        ]));
        if (row.query || row.funcname) {
          const block = this.element('div', null, 'query-sql-block');
          const copy = this.element('button', null, 'query-copy secondary'); copy.type = 'button'; copy.title = 'Copy SQL query'; copy.setAttribute('aria-label', 'Copy SQL query'); copy.append(uiIcon(this.doc, 'copy'));
          const message = this.element('span', null, 'sr-only'); message.setAttribute('role', 'status'); message.setAttribute('aria-live', 'polite');
          copy.addEventListener('click', () => this.copySql(row.query || row.funcname, copy, message));
          const sql = this.element('pre', null, 'query-sql'); sql.append(this.element('code', row.query || row.funcname)); block.append(copy, message, sql); card.append(block);
        } else card.append(this.element('p', 'SQL text is absent from this capture. The query is identified by its ID.', 'muted report-caption'));
        const extra = Object.entries(row).filter(([key]) => !['database', 'query', 'queryid', 'calls', 'rows', 'funcname', 'total_exec_ms', 'total_exec_time', 'mean_exec_ms', 'mean_exec_time'].includes(key));
        if (extra.length) {
          const detail = this.element('details', null, 'query-extra'); detail.append(this.element('summary', 'Execution metrics'), this.facts(extra.map(([key, value]) => [COLUMNS[key] || key.replaceAll('_', ' '), formatReportValue(value, key)]))); card.append(detail);
        }
        return card;
      });
      if (!cards.length) cards.push(this.element('p', rows.length ? 'No queries match this search.' : captured ? 'This capture contains no queries.' : 'Query statistics are unavailable in this capture.', 'panel report-empty'));
      list.replaceChildren(...cards);
    };
    for (const [key, text] of [['total', 'Total time ↓'], ['mean', 'Mean time ↓'], ['calls', 'Calls ↓']]) {
      const button = this.element('button', text); button.type = 'button'; button.addEventListener('click', () => { selected = key; render(); }); buttons.push([key, button]); sort.append(button);
    }
    search.addEventListener('input', render); controls.append(label, sort);
    root.append(controls, count, list);
    for (const item of reports) if (item.available === false) root.append(this.element('p', `Could not collect data for ${item.database || 'este recurso'}.`, 'muted report-caption'));
    root.append(this.element('p', 'The ranking includes captured queries and may omit activity. Timings accumulate since the statistics reset. Function calls appear in SQL; internal function timings are not measured separately.', 'muted report-caption'));
    render(); return root;
  }

  renderTable(section, label, article) {
    article.append(this.element('h4', label));
    if (!section.available) { article.append(this.element('p', 'Section unavailable in the capture.', 'muted')); return; }
    if (!section.rows?.length) { article.append(this.element('p', 'No rows in this section.', 'muted')); return; }
    const columns = [...new Set(section.rows.flatMap(row => Object.keys(row)))];
    const numeric = column => section.rows.some(row => typeof row[column] === 'number') || ['queryid', 'dbid', 'userid'].includes(column);
    const table = this.element('table'); const head = this.element('thead'); const tr = this.element('tr');
    for (const column of columns) { const th = this.element('th', COLUMNS[column] || column.replaceAll('_', ' '), numeric(column) ? 'numeric' : ''); th.setAttribute('scope', 'col'); tr.append(th); }
    head.append(tr); const body = this.element('tbody');
    for (const row of section.rows) { const line = this.element('tr'); for (const column of columns) line.append(this.element('td', formatReportValue(row[column], column), numeric(column) ? 'numeric' : '')); body.append(line); }
    table.append(head, body); const scroll = this.element('div', null, 'table-scroll'); scroll.append(table); article.append(scroll);
  }

  render(data) {
    const { report } = data;
    const reports = report.details.reports || [report.details];
    this.$('pg-status').textContent = '';
    const children = [];
    const tabs = this.element('div', null, 'pg-tabs'); tabs.setAttribute('role', 'tablist'); tabs.setAttribute('aria-label', 'Source data sections');
    const groups = [['queries', 'Queries and SQL calls', []], ['general', 'Databases', ['database_stats']], ['maintenance', 'Maintenance', ['tables']], ['cluster', 'Connections and locks', ['connection_totals', 'connections', 'locks', 'blocked']]];
    const panels = []; const buttons = [];
    const select = index => buttons.forEach((button, i) => { button.setAttribute('aria-selected', String(i === index)); button.tabIndex = i === index ? 0 : -1; panels[i].hidden = i !== index; });
    groups.forEach(([id, title, keys], index) => {
      const button = this.element('button', title); button.type = 'button'; button.id = `pg-tab-${id}`;
      button.setAttribute('role', 'tab'); button.setAttribute('aria-controls', `pg-section-${id}`);
      button.addEventListener('click', () => select(index));
      button.addEventListener('keydown', event => { if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return; event.preventDefault(); const next = event.key === 'Home' ? 0 : event.key === 'End' ? groups.length - 1 : (index + (event.key === 'ArrowRight' ? 1 : -1) + groups.length) % groups.length; select(next); buttons[next].focus(); });
      buttons.push(button); tabs.append(button);
      const panel = this.element('section'); panel.id = `pg-section-${id}`; panel.setAttribute('role', 'tabpanel'); panel.setAttribute('aria-labelledby', button.id); panel.tabIndex = 0; panels.push(panel);
      if (id === 'queries') { panel.append(this.queryRanking(reports)); return; }
      const resources = reports.filter(item => id === 'cluster' ? item.scope === 'cluster' : item.scope !== 'cluster' || keys.some(key => item[key]));
      if (!resources.length) panel.append(this.element('p', 'This section is unavailable in the capture.', 'muted report-empty'));
      for (const item of resources) {
        const article = this.element('article', null, 'panel pg-database'); article.append(this.element('h3', item.scope === 'cluster' ? 'PostgreSQL cluster' : item.database));
        if (!item.available) { article.append(this.element('p', 'Could not collect data for this resource.', 'muted')); panel.append(article); continue; }
        if (item.databases_truncated && id === 'cluster') article.append(this.element('p', 'The capture reached the configured database limit.', 'notice'));
        let hasSection = false;
        for (const [key, label] of SECTIONS) if (keys.includes(key) && item[key]) { hasSection = true; this.renderTable(item[key], label, article); }
        if (!hasSection) article.append(this.element('p', 'This capture contains no data for this section.', 'muted'));
        panel.append(article);
      }
    });
    select(0); children.push(tabs, ...panels);
    this.$('pg-results').replaceChildren(...children); this.$('pg-results').hidden = false;
  }
}
