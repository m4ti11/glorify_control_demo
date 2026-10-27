import { api } from './api.js';
import { PgReportsPanel, reportDay } from './pg-reports.js';
import { OperationalReportsPanel } from './operational-reports.js';

export function shiftReportDay(day, offset) {
  const date = new Date(day + 'T12:00:00Z');
  if (!Number.isFinite(date.getTime())) return day;
  date.setUTCDate(date.getUTCDate() + offset);
  return date.toISOString().slice(0, 10);
}

export class ReportsPanel {
  constructor(browser = window, doc = document) {
      this.browser = browser; this.doc = doc; 
    this.$ = id => doc.getElementById(id); this.generation = 0; this.systemId = null;
    this.pg = new PgReportsPanel(browser, doc);
    this.ai = new OperationalReportsPanel(browser, doc);
    this.$('reports-day').value = reportDay(); this.$('reports-day').max = reportDay(new Date(), 0);
    this.$('reports-form').addEventListener('submit', event => { event.preventDefault(); this.load(); });
    this.$('reports-day').addEventListener('change', () => this.load());
    this.$('reports-prev').addEventListener('click', () => this.changeDay(-1));
    this.$('reports-next').addEventListener('click', () => this.changeDay(1));
    for (const key of ['raw', 'ai']) {
      this.$('reports-tab-' + key).addEventListener('click', () => {
        this.browser.location.hash = 'reports/' + key;
        this.select(key);
      });
      this.$('reports-tab-' + key).addEventListener('keydown', event => {
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 'raw' : event.key === 'End' ? 'ai' : key === 'raw' ? 'ai' : 'raw';
        this.$('reports-tab-' + next).click(); this.$('reports-tab-' + next).focus();
      });
    }
    browser.addEventListener('hashchange', () => this.selectRoute());
    this.selectRoute();
  }

  selectRoute() { this.select(/operational-report|reports\/ai/.test(this.browser.location.hash) ? 'ai' : 'raw'); }
  select(key) {
    for (const value of ['raw', 'ai']) {
      this.$('reports-' + value).hidden = value !== key;
      this.$('reports-tab-' + value).setAttribute('aria-selected', String(value === key));
      this.$('reports-tab-' + value).tabIndex = value === key ? 0 : -1;
    }
  }
  setSystem(id) { if (!this.systemId) this.systemId = id; }
  updateDateControls() {
    const enabled = true;
    this.$('reports-day').disabled = !enabled;
    this.$('reports-prev').disabled = !enabled;
    this.$('reports-next').disabled = !enabled || this.$('reports-day').value >= this.$('reports-day').max;
    this.$('reports-form').setAttribute('aria-busy', String(Boolean(this.busy)));
  }
  changeDay(offset) {
    const next = shiftReportDay(this.$('reports-day').value, offset);
    if (next > this.$('reports-day').max) return;
    this.$('reports-day').value = next;
    return this.load();
  }
  setEnabled(value) {
    this.pg.setEnabled(value); this.ai.setEnabled(value);
    if (!value) {
      this.busy = false; this.generation++; this.discovery?.abort(); this.discovery = null;
      this.systemId = null; this.$('reports-notice').hidden = true;
    }
    this.updateDateControls();
  }

  async load() {
    if (!this.$('reports-day').value) return;
    const generation = ++this.generation;
    this.discovery?.abort();
    this.pg.controller?.abort(); this.pg.controller = null; this.pg.busy(false);
    this.ai.controller?.abort(); this.ai.controller = null; this.ai.busy(false); this.ai.clearPdf();
    // Clear the previous server/date before either request can fail.
    for (const id of ['pg-results', 'operational-results']) { this.$(id).replaceChildren(); this.$(id).hidden = true; }
    for (const prefix of ['pg', 'operational']) { this.$(prefix + '-notice').hidden = true; this.$(prefix + '-status').textContent = 'Loading the selected date…'; }
    this.busy = true; this.updateDateControls(); this.$('reports-notice').hidden = true;
    const day = this.$('reports-day').value;
    let system = this.systemId;
    const controller = new AbortController(); this.discovery = controller;
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      if (!system) system = (await api.latest()).system_id;
      if (generation !== this.generation) return;
      this.systemId = system;
      for (const prefix of ['pg', 'operational']) {
        this.$(prefix + '-system').value = system;
        this.$(prefix + '-day').value = day;
      }
      await Promise.all([this.pg.load(), this.ai.load()]);
    } catch (error) {
      if (generation !== this.generation) return;
      this.$('reports-notice').textContent = error.name === 'AbortError' ? 'Loading took too long. Try again.' : error.message;
      this.$('reports-notice').hidden = false;
    } finally {
      clearTimeout(timer);
      if (generation === this.generation) { this.busy = false; this.discovery = null; this.updateDateControls(); }
    }
  }
}
