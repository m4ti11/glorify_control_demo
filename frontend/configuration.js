import { formatDateTime } from './dates.js';
import { api } from './api.js';
export const CONFIGURATION_KINDS = {
  'health-checks': 'Site definitions', 'alert-rules': 'Alert rules', 'notification-channels': 'Notifications',
};
export const METRICS = {
  cpu_percent: 'CPU (%)', memory_percent: 'Memory (%)', disk_percent: 'Disk (%)',
};
const COMMON = [['name', 'Name', 'text', ''], ['enabled', 'Enabled', 'checkbox', true]];
const RULE = [['cooldown_seconds', 'Minimum time between notifications (seconds)', 'number', 1800], ['notify_recovery', 'Notify on recovery', 'checkbox', true], ['maintenance_until', 'Pause until (local time, optional)', 'datetime-local', '']];
const FIELDS = {
  'health-checks': [...COMMON, ['url', 'URL', 'url', ''], ['method', 'Proposed HTTP method', { GET: 'GET', HEAD: 'HEAD' }, 'GET'], ['interval_seconds', 'Proposed interval (seconds)', 'number', 60], ['timeout_seconds', 'Proposed timeout (seconds)', 'number', 5], ['failure_count', 'Consecutive failures before outage', 'number', 3], ['recovery_count', 'Consecutive successes before recovery', 'number', 2], ['expected_status_codes', 'Expected HTTP codes, comma-separated', 'text', '200'], ...RULE],
  'alert-rules': [...COMMON, ['system_id', 'System', 'text', ''], ['resource_type', 'Resource type', { host: 'Host' }, 'host'], ['resource_id', 'Resource', { host: 'Host' }, 'host'], ['metric', 'Metric and unit', METRICS, 'cpu_percent'], ['operator', 'Trigger when', { '>=': 'Greater than or equal', '<=': 'Less than or equal' }, '>='], ['threshold', 'Alert threshold', 'number', 80], ['recovery_threshold', 'Recovery threshold', 'number', 70], ['duration_seconds', 'Minimum duration (seconds)', 'number', 180], ['consecutive_samples', 'Minimum readings', 'number', 3], ...RULE],
  'notification-channels': [...COMMON, ['emails', 'Email addresses (one per line or comma-separated)', 'textarea', '']],
};

function configurationKey(_unused, kind, id = null, action = null) { return { kind, id, action }; }

export function formData(kind, fields, selectedChannels = []) {
  const result = {};
  for (const [name, , type] of FIELDS[kind]) {
    const field = fields[name];
    result[name] = type === 'checkbox' ? field.checked : type === 'number' ? Number(field.value) : field.value.trim();
  }
  if (kind === 'notification-channels') result.emails = result.emails.split(/[,;\n]+/).map(value => value.trim()).filter(Boolean);
  else {
    result.notification_channel_ids = selectedChannels;
    result.maintenance_until = result.maintenance_until ? new Date(result.maintenance_until).toISOString() : null;
  }
  if (kind === 'health-checks') result.expected_status_codes = result.expected_status_codes.split(',').map(value => Number(value.trim()));
  return result;
}

export function validateConfiguration(kind, data) {
  if (kind === 'health-checks' && data.timeout_seconds >= data.interval_seconds) throw new Error('Timeout must be shorter than interval.');
  if (kind === 'alert-rules') {
    if (data.resource_type !== 'host' || data.resource_id !== 'host' || !Object.hasOwn(METRICS, data.metric)) throw new Error('Only host CPU, memory and disk rules are supported.');
    if (data.metric.endsWith('percent') && Math.max(data.threshold, data.recovery_threshold) > 100) throw new Error('Percentages must be between 0 and 100.');
    if ((data.operator === '>=' && data.recovery_threshold >= data.threshold) || (data.operator === '<=' && data.recovery_threshold <= data.threshold)) throw new Error('Recovery must leave a margin from the alert threshold.');
  }
  return data;
}

export function configurationSummary(item) {
  const d = item.data;
  if (item.kind === 'health-checks') return `${d.method} ${d.url} · every ${d.interval_seconds} s · ${d.failure_count} failures before outage`;
  if (item.kind === 'alert-rules') {
    const unit = d.metric.endsWith('percent') ? '%' : ' bytes/s';
    return `Alert: ${d.operator === '>=' ? '≥' : '≤'} ${d.threshold}${unit} · Recovery: ${d.recovery_threshold}${unit} · ${d.system_id}`;
  }
  return d.emails.join(', ');
}

export class ConfigurationPanel {
  constructor(browser = window, doc = document) {
       this.browser = browser; this.doc = doc;
    this.$ = id => doc.getElementById(id); this.kind = 'alert-rules'; this.items = []; this.channels = [];
    this.cursor = null; this.current = null; this.controllers = new Set(); this.generation = 0; this.enabled = false;
    this.initialized = false; this.saving = false;
    this.$('configuration-refresh').addEventListener('click', () => this.load());
    this.$('configuration-more').addEventListener('click', () => this.load(true));
    this.$('configuration-new').addEventListener('click', () => this.openEditor());
    for (const id of ['configuration-close', 'configuration-cancel']) this.$(id).addEventListener('click', () => this.closeEditor());
    this.$('configuration-form').addEventListener('submit', event => { event.preventDefault(); this.save(); });
    this.$('configuration-import').addEventListener('click', () => this.importRecipients());
    this.fields = null; this.systemId = null;
    for (const kind of Object.keys(CONFIGURATION_KINDS)) this.$('tab-' + kind).addEventListener('click', () => {
      this.setKind(kind); this.setEnabled(true); this.load();
    });
  }

  element(tag, value, className) { const item = this.doc.createElement(tag); if (value != null) item.textContent = value; if (className) item.className = className; return item; }
  icon(kind) {
    const paths = {
      cpu: 'M6 6h12v12H6zM9 9h6v6H9zM9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3',
      memory: 'M3 6h18v12H3zM7 10v4M12 10v4M17 10v4M7 18v3M17 18v3',
      disk: 'M4 4h16v16H4zM4 14h16M15 17h2M7 17h.01',
      site: 'M3 4h18v13H3zM8 21h8M12 17v4M7 10h3l2-3 2 6 2-3h2',
      mail: 'M4 5h16v14H4zM4 6l8 7 8-7',
      alert: 'M12 3 2 20h20L12 3ZM12 9v4M12 17h.01',
    };
    const svg = this.doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
    for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(key, value);
    const path = this.doc.createElementNS('http://www.w3.org/2000/svg', 'path');
    path.setAttribute('d', paths[kind] || paths.alert); svg.append(path); return svg;
  }
  canWrite() { return Boolean(true); }

  setKind(kind) {
    if (!Object.hasOwn(CONFIGURATION_KINDS, kind)) throw new Error('Invalid configuration category.');
    for (const controller of this.controllers) controller.abort();
    this.controllers.clear(); this.generation++;
    this.kind = kind; this.items = []; this.channels = []; this.cursor = null; this.current = null; this.fields = null;
    this.$('configuration-list').replaceChildren();
    this.$('configuration-more').hidden = true;
    this.$('configuration-notice').hidden = true;
    this.closeEditor();
    this.$('configuration-bootstrap').hidden = true;
    this.$('configuration-sites-note').hidden = kind !== 'health-checks';
    this.$('configuration-heading').textContent = CONFIGURATION_KINDS[kind];
    this.$('configuration-new').textContent = kind === 'notification-channels' ? 'New channel' : kind === 'health-checks' ? 'Add site definition' : 'New rule';
    for (const tab of Object.keys(CONFIGURATION_KINDS)) {
      this.$('tab-' + tab).setAttribute('aria-selected', String(tab === kind));
      this.$('tab-' + tab).tabIndex = tab === kind ? 0 : -1;
      this.$('tab-' + tab).onkeydown = event => {
        const tabs = Object.keys(CONFIGURATION_KINDS);
        if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (tabs.indexOf(kind) + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
        this.$('tab-' + tabs[next]).click(); this.$('tab-' + tabs[next]).focus();
      };
    }
    this.$('configuration-content').setAttribute('aria-labelledby', 'tab-' + kind);
    this.$('configuration-system').textContent = this.systemId ? `Host: ${this.systemId}` : '';
    this.$('configuration-status').textContent = 'Loading configuration…';
  }

  setSystem(id) { this.systemId = id; this.$('configuration-system').textContent = `Host: ${id}`; }

  setEnabled(value) {
    if (!value && this.enabled) {
      for (const controller of this.controllers) controller.abort(); this.controllers.clear(); this.generation++;
      this.items = []; this.channels = []; this.cursor = null; this.current = null; this.initialized = false; this.systemId = null;
      this.$('configuration-list').replaceChildren(); this.$('configuration-notice').hidden = true; this.reset();
      this.$('configuration-status').textContent = 'Load simulated configuration.';
    }
    this.enabled = value;
    if (!this.fields) this.reset();
    if (!value || !this.canWrite()) this.closeEditor();
    this.$('configuration-new').hidden = !this.canWrite();
    this.$('configuration-import').disabled = !this.canWrite() || this.saving;
    this.$('configuration-import-hint').hidden = this.canWrite();
  }

  async request(key, options = {}) { return api.configuration(key.kind, key.id, key.action, options); }

  error(error) {
    if (this.$('configuration-editor').open) { this.$('configuration-editor-notice').textContent = error.name === 'AbortError' ? 'Loading took too long. Try again.' : error.message; this.$('configuration-editor-notice').hidden = false; }
    this.$('configuration-notice').textContent = error.name === 'AbortError' ? 'Loading took too long. Try again.' : error.message; this.$('configuration-notice').hidden = false; }

  async load(more = false) {
    if (!true) return;
    const generation = ++this.generation; const kind = this.kind;
    this.$('configuration-notice').hidden = true; this.$('configuration-status').textContent = 'Loading configuration…';
    this.$('configuration-more').disabled = true;
    try {
      const url = configurationKey(null, kind);

      const [data, channels] = await Promise.all([this.request(url), kind === 'notification-channels' ? Promise.resolve(null) : this.request(configurationKey(null, 'notification-channels'))]);
      if (generation !== this.generation || !true) return;
      this.items = more ? [...this.items, ...data.items] : data.items; this.cursor = data.next_cursor;
      this.channels = kind === 'notification-channels' ? data.items : channels.items;
      this.initialized = data.notifications_initialized;
      if (kind === 'alert-rules' && this.items.length && new Set(this.items.map(item => item.data.system_id)).size === 1) this.setSystem(this.items[0].data.system_id);
      this.$('configuration-bootstrap').hidden = kind !== 'notification-channels' || this.initialized;
      this.$('configuration-status').textContent = this.items.length ? `${this.items.length} ${this.items.length === 1 ? 'record loaded' : 'records loaded'}` : kind === 'notification-channels'
        ? this.initialized ? 'There are no channels. Create one with example recipient addresses.' : 'Notifications are not initialized. Create example recipients to continue.'
        : `There are no ${kind === 'health-checks' ? 'site definitions' : 'rules'} saved yet. ${this.canWrite() ? kind === 'alert-rules' ? 'Host rules are not available yet.' : 'Add the first site definition.' : 'Create the first record.'}`;
      this.renderList(); if (!this.current) this.reset();
    } catch (error) { if (generation === this.generation && true) this.error(error); }
    finally { if (generation === this.generation) this.$('configuration-more').disabled = false; }
  }

  closeEditor() { if (this.$('configuration-editor').open) this.$('configuration-editor').close(); }

  openEditor(item = null) {
    if (!this.canWrite()) return;
    this.reset(item);
    this.$('configuration-editor-notice').hidden = true;
    this.$('configuration-editor').showModal();
    this.$('configuration-editor-title').focus();
  }

  reset(item = null) {
    this.current = item; this.createId = item?.id || this.browser.crypto.randomUUID(); this.fields = {}; this.channelInputs = [];
    const root = this.$('configuration-fields'); root.replaceChildren();
    const advanced = this.element('details', null, 'configuration-advanced');
    advanced.append(this.element('summary', 'Advanced options'));
    const advancedFields = this.element('div', null, 'configuration-fields'); advanced.append(advancedFields);
    const basic = this.kind === 'alert-rules' ? [...(item ? [] : ['name', 'system_id', 'metric']), 'enabled', 'threshold', 'recovery_threshold', 'duration_seconds', 'consecutive_samples'] : this.kind === 'health-checks' ? ['name', 'enabled', 'url', 'interval_seconds'] : ['name', 'enabled', 'emails'];
    this.$('configuration-editor-title').textContent = item ? `Edit: ${item.data.name}` : 'New configuration';
    for (const [name, title, type, fallback] of FIELDS[this.kind]) {
      const unit = item?.data.metric?.endsWith('percent') ? ' (%)' : item?.data.metric?.startsWith('network_') ? ' (bytes/s)' : '';
      const label = this.element('label', name === 'threshold' ? 'Alert threshold' + unit : name === 'recovery_threshold' ? 'Recovery threshold' + unit : title);
      let input;
      if (typeof type === 'object') {
        input = this.element('select');
        for (const [value, text] of Object.entries(type)) { const option = this.element('option', text); option.value = value; input.append(option); }
      } else { input = this.element(type === 'textarea' ? 'textarea' : 'input'); if (type !== 'textarea') input.type = type; }
      input.id = 'configuration-field-' + name; input.name = name;
      const value = item?.data[name] ?? (name === 'system_id' ? this.systemId || '' : fallback);
      if (type === 'checkbox') { input.checked = Boolean(value); label.className = 'configuration-check'; }
      else if (name === 'emails') { input.value = Array.isArray(value) ? value.join('\n') : value; input.rows = 3; }
      else if (name === 'expected_status_codes') input.value = Array.isArray(value) ? value.join(', ') : value;
      else if (name === 'maintenance_until' && value) { const date = new Date(value); input.value = new Date(date - date.getTimezoneOffset() * 60000).toISOString().slice(0, 16); }
      else input.value = value;
      input.required = !['checkbox', 'datetime-local'].includes(type);
      if (type === 'number') {
        const bounds = { interval_seconds: [10, 86400], timeout_seconds: [1, 60], failure_count: [1, 20], recovery_count: [1, 20], cooldown_seconds: [0, 604800], duration_seconds: [0, 86400], consecutive_samples: [1, 100] }[name];
        input.min = String(bounds?.[0] ?? 0); if (bounds) input.max = String(bounds[1]);
        input.step = ['threshold', 'recovery_threshold'].includes(name) ? 'any' : '1';
      }
      if (name === 'name') input.maxLength = 100;
      if (this.kind === 'alert-rules' && ['name', 'metric', 'system_id', 'resource_type', 'resource_id'].includes(name)) input.disabled = Boolean(item);
      if (['threshold', 'recovery_threshold'].includes(name) && item?.data.metric.endsWith('percent')) input.max = '100';
      label.append(input);
      if (!(item && this.kind === 'alert-rules' && ['name', 'metric', 'system_id', 'resource_type', 'resource_id'].includes(name))) (basic.includes(name) ? root : advancedFields).append(label);
      this.fields[name] = input;
    }
    if (this.kind !== 'notification-channels') {
      const group = this.element('fieldset', null, 'configuration-destinations'); group.append(this.element('legend', 'Notification channels'));
      if (!this.channels.length) group.append(this.element('p', 'Create a channel in Notifications first.', 'muted'));
      for (const channel of this.channels) {
        const label = this.element('label', channel.data.name, 'configuration-check'); const input = this.element('input'); input.type = 'checkbox'; input.value = channel.id; input.checked = Boolean(item?.data.notification_channel_ids?.includes(channel.id)); label.append(input); group.append(label); this.channelInputs.push(input);
      }
      root.append(group);
    }
    if (advancedFields.children.length) root.append(advanced);
    this.$('configuration-save').disabled = this.saving || (this.kind === 'notification-channels' && !this.initialized);
    if (item && this.canWrite()) this.$('configuration-editor-title').focus();
  }

  renderList() {
    const rows = this.items.map(item => {
      const card = this.element('article', null, 'configuration-item');
      const d = item.data;
      const theme = item.kind === 'alert-rules' ? ({ cpu_percent: 'cpu', memory_percent: 'memory', disk_percent: 'disk' }[d.metric] || 'alert') : item.kind === 'health-checks' ? 'site' : 'mail';
      card.dataset.theme = theme;
      const header = this.element('div', null, 'configuration-item-heading');
      const identity = this.element('div', null, 'configuration-identity');
      const icon = this.element('span', null, 'configuration-item-icon'); icon.append(this.icon(theme));
      const title = this.element('div');
      title.append(this.element('small', item.kind === 'alert-rules' ? METRICS[d.metric] || d.metric : CONFIGURATION_KINDS[item.kind]), this.element('h3', d.name));
      identity.append(icon, title);
      header.append(identity, this.element('span', item.kind === 'health-checks' ? 'Definition only' : d.enabled ? 'Enabled' : 'Paused', 'pill' + (d.enabled ? ' ok' : ''))); card.append(header);
      const facts = this.element('dl', null, 'configuration-facts');
      const unit = d.metric?.endsWith('percent') ? '%' : ' bytes/s';
      const fields = item.kind === 'alert-rules'
        ? [['Alert', `${d.operator === '>=' ? '≥' : '≤'} ${d.threshold}${unit}`], ['Recovery', `${d.recovery_threshold}${unit}`], ['Minimum duration', `${d.duration_seconds} s`], ['Minimum readings', d.consecutive_samples]]
        : item.kind === 'health-checks'
          ? [['Method', d.method], ['Proposed interval', `${d.interval_seconds} s`], ['Proposed failure count', d.failure_count], ['Timeout', `${d.timeout_seconds} s`]]
          : [['Recipients', d.emails.length], ['State', d.enabled ? 'Sending enabled' : 'Sending paused']];
      for (const [label, value] of fields) {
        const fact = this.element('div'); fact.append(this.element('dt', label), this.element('dd', value)); facts.append(fact);
      }
      card.append(facts);
      card.append(this.element('p', item.kind === 'alert-rules' ? `Host: ${d.system_id} · Resource: ${d.resource_id}` : item.kind === 'health-checks' ? d.url : d.emails.join(', '), 'muted configuration-summary'));
      card.append(this.element('small', `Last modified: ${formatDateTime(item.updated_at)}`, 'muted'));
      

      const actions = this.element('div', null, 'configuration-actions');
      if (this.canWrite()) {
        const edit = this.element('button', 'Edit', 'secondary'); edit.type = 'button'; edit.addEventListener('click', () => this.openEditor(item)); actions.append(edit);
        const remove = this.element('button', 'Delete', 'secondary danger'); remove.type = 'button'; remove.addEventListener('click', () => this.remove(item)); actions.append(remove);
      }
      if (item.kind === 'notification-channels') {
        const state = this.element('p', ({ SIMULATED: 'Simulated only; no subscriptions or messages are created.', PENDING_SYNC: 'Simulating recipient sync…', SYNCED: 'Recipients configured. Delivery is simulated.', DISABLED: 'Channel paused.', ERROR: 'Sync failed. Try again.' })[item.delivery?.status] || 'Pending sync', 'configuration-state');
        card.append(state);
        const status = this.element('button', 'Check recipients', 'secondary'); status.type = 'button'; status.addEventListener('click', async () => {
          status.disabled = true;
          try { const data = await this.request(configurationKey(null, this.kind, item.id, 'status')); state.textContent = data.recipients.length ? data.recipients.map(recipient => `${recipient.email}: ${recipient.status === 'SIMULATED' ? 'simulated; no email sent' : recipient.status === 'CONFIRMED' ? 'confirmed' : 'pending confirmation'}`).join(' · ') : data.status === 'DISABLED' ? 'Channel paused.' : 'Sync is still pending.'; } catch (error) { this.error(error); } finally { status.disabled = false; }
        }); actions.append(status);
        if (this.canWrite()) { const sync = this.element('button', 'Simulate sync', 'secondary'); sync.type = 'button'; sync.addEventListener('click', () => this.resync(item)); actions.append(sync); }
      }
      card.append(actions); return card;
    });
    this.$('configuration-list').replaceChildren(...rows); this.$('configuration-more').hidden = !this.cursor;
  }

  async write(operation) {
    if (this.saving || !this.canWrite()) return;
    const generation = this.generation; this.saving = true; this.$('configuration-save').disabled = true; this.$('configuration-import').disabled = true; this.$('configuration-notice').hidden = true;
    try { await operation(); if (generation !== this.generation || !true) return; this.closeEditor(); this.current = null; await this.load(); }
    catch (error) { if (generation === this.generation && true) this.error(error); }
    finally { this.saving = false; this.setEnabled(true); this.$('configuration-save').disabled = !this.canWrite() || (this.kind === 'notification-channels' && !this.initialized); }
  }

  save() {
    const kind = this.kind; const current = this.current;
    return this.write(() => this.request(configurationKey(null, kind, current?.id), {
      method: current ? 'PUT' : 'POST', body: JSON.stringify({ ...(current ? { version: current.version } : { id: this.createId }), data: validateConfiguration(kind, formData(kind, this.fields, this.channelInputs.filter(input => input.checked).map(input => input.value))) }),
    }));
  }
  remove(item) { return this.write(() => { const url = configurationKey(null, this.kind, item.id);  return this.request(url, { method: 'DELETE' }); }); }
  resync(item) { return this.write(() => this.request(configurationKey(null, 'notification-channels', item.id, 'sync'), { method: 'POST', body: JSON.stringify({ version: item.version }) })); }
  importRecipients() { return this.write(() => this.request(configurationKey(null, 'notification-channels', 'import'), { method: 'POST' })); }
}
