import { formatDay, formatDateTime } from './dates.js';
import { api } from './api.js';
const ZONE = 'America/Argentina/Buenos_Aires';
const SOURCES = { host_metrics: 'Host', events: 'Events', postgresql: 'PostgreSQL' };
const STATES = { available: 'Available', missing: 'No data', incomplete: 'Partial', stale: 'Stale', unavailable: 'Unavailable' };
const PRIORITIES = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low', info: 'Info', normal: 'Normal' };

export function operationalReportDay(now = new Date()) {
  const parts = new Intl.DateTimeFormat('en', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).formatToParts(now);
  const value = Object.fromEntries(parts.map(part => [part.type, part.value]));
  const day = new Date(Date.UTC(Number(value.year), Number(value.month) - 1, Number(value.day) - 1));
  return day.toISOString().slice(0, 10);
}

export class OperationalReportsPanel {
  constructor(browser = window, doc = document) {
       this.browser = browser; this.doc = doc;
    this.$ = id => doc.getElementById(id); this.controller = null; this.enabled = false;
    this.$('pdf-preview-close').addEventListener('click', () => this.clearPdf());
    this.$('pdf-preview-dialog').addEventListener('cancel', event => { event.preventDefault(); this.clearPdf(); });
    this.$('pdf-preview-dialog').addEventListener('click', event => {
      if (event.target !== this.$('pdf-preview-dialog')) return;
      const rect = event.target.getBoundingClientRect();
      if (event.clientX < rect.left || event.clientX > rect.right || event.clientY < rect.top || event.clientY > rect.bottom) this.clearPdf();
    });
    this.$('operational-day').value = operationalReportDay(); this.$('operational-day').max = operationalReportDay();
    this.$('operational-form').addEventListener('submit', event => { event.preventDefault(); this.load(); });
  }

  setSystem(systemId) {
    if (!this.$('operational-system').value) this.$('operational-system').value = systemId;
  }

  clearPdf() {
    this.pdfController?.abort(); this.pdfController = null;
    if (this.pdfUrl) this.browser.URL.revokeObjectURL(this.pdfUrl);
    this.pdfUrl = null;
    const dialog = this.$('pdf-preview-dialog');
    if (dialog.open) dialog.close();
    this.$('pdf-preview-body').replaceChildren();
    if (this.pdfTrigger?.isConnected) this.pdfTrigger.focus();
    this.pdfTrigger = null;
  }

  setEnabled(value) {
    if (!value && (this.enabled || this.controller)) {
      this.controller?.abort(); this.controller = null; this.clearPdf();
      this.$('operational-results').replaceChildren(); this.$('operational-results').hidden = true;
      this.$('operational-notice').hidden = true; this.$('operational-system').value = '';
      this.$('operational-status').textContent = 'Select a date to load the sample operational report.';
      this.busy(false);
    }
    this.enabled = value;
  }

  busy(value) { this.$('operational-search').disabled = value; this.$('operational-panel').setAttribute('aria-busy', String(value)); }

  async load() {
    const generation = this.generation = (this.generation || 0) + 1;
    const prefix = 'operational';
    this.clearPdf();
    this.busy(true); this.$(prefix + '-notice').hidden = true;
    try {
      const day = this.$(prefix + '-day').value;
      const result = await api.operationalReport(day);
      if (generation !== this.generation) return;
      this.render(result);
    } catch (error) {
      this.$(prefix + '-status').textContent = 'No sample available for the selected date.';
      this.$(prefix + '-notice').textContent = error.message;
      this.$(prefix + '-notice').hidden = false;
    } finally { if (generation === this.generation) this.busy(false); }
  }

  element(tag, value, className) {
    const node = this.doc.createElement(tag); if (value != null) node.textContent = value;
    if (className) node.className = className; return node;
  }

  async loadPdf(report, download, button) {
    button.disabled = true;
    try {
      const href = api.pdfUrl();
      if (download) {
        const link = this.element('a'); link.href = href; link.download = 'demo-operational-report.pdf'; link.click();
      } else {
        const frame = this.element('iframe'); frame.src = href; frame.title = 'Fictional operational report'; frame.className = 'report-pdf';
        this.clearPdf();
        this.pdfTrigger = button;
        this.$('pdf-preview-body').replaceChildren(frame);
        this.$('pdf-preview-open').href = href;
        this.$('pdf-preview-download').href = href;
        this.$('pdf-preview-dialog').showModal();
        this.$('pdf-preview-title').focus();
      }
    } finally { button.disabled = false; }
  }

  render(report) {
    const { analysis, source_status: sources = {} } = report;
    const status = Object.entries(sources).map(([name, source]) => {
      const issues = (source.issues || []).filter(issue => issue !== source.status).map(issue => STATES[issue] || issue);
      return `${SOURCES[name] || name}: ${STATES[source.status] || source.status}${issues.length ? ` [${issues.join(', ')}]` : ''}${source.count != null ? ` (${source.count})` : ''}`;
    }).join(' · ');
    this.$('operational-status').textContent = `Report for ${formatDay(report.report_date)} · UTC−3 · ${report.system_id}${report.generated_at ? ` · Generated: ${formatDateTime(report.generated_at)}` : ''} · Sources: ${status}`;
    const parts = [];
    const actions = this.element('div', null, 'report-actions');
    for (const [label, download] of [['Preview PDF', false], ['Download PDF', true]]) {
      const button = this.element('button', label, 'secondary'); button.type = 'button';
      button.addEventListener('click', () => this.loadPdf(report, download, button)); actions.append(button);
    }
    parts.push(actions);
    const coverage = this.element('div', null, 'report-source-grid');
    for (const [name, source] of Object.entries(sources)) {
      const card = this.element('article', null, 'panel report-source');
      card.setAttribute('data-state', source.status);
      const heading = this.element('div', null, 'report-source-heading');
      heading.append(this.element('h3', SOURCES[name] || name), this.element('span', STATES[source.status] || source.status, 'pill'));
      card.append(heading);
      const facts = this.element('dl', null, 'report-facts');
      const entries = [['Records analyzed', source.count ?? '—']];
      for (const [key, label] of [['observed_count', 'Records observed'], ['omitted_count', 'Records omitted'], ['report_date', 'Capture date']]) if (source[key] != null) entries.push([label, key === 'report_date' ? formatDay(source[key]) : String(source[key])]);
      for (const [key, label] of [['captured_at', 'Captured'], ['last_received_at', 'Last received']]) if (source[key]) entries.push([label, formatDateTime(source[key])]);
      for (const [label, value] of entries) { const fact = this.element('div'); fact.append(this.element('dt', label), this.element('dd', value)); facts.append(fact); }
      card.append(facts);
      const issues = (source.issues || []).filter(issue => issue !== source.status);
      if (issues.length) card.append(this.element('p', issues.map(issue => STATES[issue] || issue).join(' · '), 'muted report-caption'));
      coverage.append(card);
    }
    const summary = this.element('article', null, 'panel report-ai-summary');
    summary.append(this.element('span', 'ASSISTED ANALYSIS · FICTIONAL SAMPLE', 'eyebrow'), this.element('h2', 'Operational summary'), this.element('p', analysis.summary, 'operational-summary'));
    parts.push(coverage, summary);
    for (const [title, list, getLabel] of [
      ['Findings', analysis.findings || [], item => `${PRIORITIES[item.severity] || item.severity}: ${item.title}`],
      ['Recommendations', analysis.recommendations || [], item => `${PRIORITIES[item.priority] || item.priority}: ${item.action}`],
    ]) {
      const section = this.element('section', null, 'report-analysis-section'); section.append(this.element('h3', title));
      if (!list.length) section.append(this.element('p', 'No items reported.', 'muted'));
      for (const item of list) {
        const article = this.element('article', null, 'panel operational-item'); article.setAttribute('data-priority', item.severity || item.priority || 'info');
        article.append(this.element('h4', getLabel(item)));
        if (item.description) article.append(this.element('p', item.description));
        const evidence = this.element('details', null, 'report-evidence');
        evidence.append(this.element('summary', 'Cited sources'), this.element('small', `Evidence: ${(item.evidence_refs || []).join(', ')}`));
        article.append(evidence); section.append(article);
      }
      parts.push(section);
    }
    this.$('operational-results').replaceChildren(...parts); this.$('operational-results').hidden = false;
  }
}
