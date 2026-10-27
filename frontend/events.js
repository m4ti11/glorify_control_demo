import { formatDay, formatDateTime } from './dates.js';
import { api } from './api.js';
import { uiIcon } from './ui-icons.js';

export function emailLabel(notification = {}) {
  if (notification.email_status === 'ACCEPTED') return 'HTML email accepted by SES';
  if (notification.email_status === 'PUBLISHED') return 'Published for email';
  if (notification.email_status === 'SUPPRESSED' && notification.suppression_reason === 'container_health_only') return 'No notification: container health-only change';
  if (notification.status === 'PUBLISHED') return 'Published to SNS';
  if (notification.status === 'SKIPPED') return 'No active recipients';
  if (notification.status === 'PENDING') return 'Pending publication';
  return 'No publication recorded';
}

export function notificationGroup(notification = {}) {
  if (notification.status === 'PUBLISHED' || ['PUBLISHED', 'ACCEPTED'].includes(notification.email_status)) return 'published';
  if (notification.status === 'PENDING') return 'pending';
  return 'other';
}

export function filterEvents(items, query = '', group = 'all') {
  const search = query.trim().toLocaleLowerCase('en');
  return items.filter(event => (group === 'all' || notificationGroup(event.notification) === group)
    && [event.event_type, event.source, event.system_id, event.subject?.id, event.subject?.type, event.notification?.email_recipient, emailLabel(event.notification)].filter(Boolean).join(' ').toLocaleLowerCase('en').includes(search));
}

const ZONE = 'America/Argentina/Buenos_Aires';
const localDay = now => new Intl.DateTimeFormat('en-CA', { timeZone: ZONE, year: 'numeric', month: '2-digit', day: '2-digit' }).format(now);
export function eventDatePreset(preset, now = new Date()) {
  const today = localDay(now);
  const offset = days => new Date(Date.parse(`${today}T12:00:00Z`) - days * 86400000).toISOString().slice(0, 10);
  if (preset === 'today') return { from: today, to: today };
  if (preset === 'yesterday') return { from: offset(1), to: offset(1) };
  return { from: offset(6), to: today };
}
function rangeLabel(from, to) {
  return from === to ? formatDay(from) : `${formatDay(from)} – ${formatDay(to)}`;
}
const formatDate = formatDateTime;

export class EventsPanel {
  constructor(browser = window, doc = document) {
    
    
    
    this.browser = browser;
    this.doc = doc;
    this.filters = {};
    this.cursor = null;
    this.items = [];
    this.search = ''; this.mailFilter = 'all';
    this.controller = null;
    this.enabled = false;
    const $ = id => doc.getElementById(id);
    this.$ = $;
    const closeFilter = () => {
      $('events-date-dialog').close();
      $('events-filter-toggle').setAttribute('aria-expanded', 'false');
      $('events-filter-toggle').focus();
    };
    const restoreDates = () => {
      const filters = this.filters.from ? this.filters : eventDatePreset('week');
      $('events-from').value = filters.from; $('events-to').value = filters.to;
      this.updateDateControls();
    };
    $('events-filter-toggle').addEventListener('click', () => {
      restoreDates();
      $('events-date-error').hidden = true;
      $('events-date-dialog').showModal();
      $('events-filter-toggle').setAttribute('aria-expanded', 'true');
      $('date-filter-title').focus();
    });
    for (const id of ['events-cancel', 'events-date-close']) $(id).addEventListener('click', () => { restoreDates(); closeFilter(); });
    $('events-date-dialog').addEventListener('cancel', event => { event.preventDefault(); restoreDates(); closeFilter(); });
    $('events-date-dialog').addEventListener('click', event => {
      if (event.target !== $('events-date-dialog')) return;
      const bounds = event.target.getBoundingClientRect();
      if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) { restoreDates(); closeFilter(); }
    });
    $('events-filters').addEventListener('keydown', event => {
      if (event.key === 'Escape') { event.preventDefault(); restoreDates(); closeFilter(); }
    });
    for (const id of ['events-from', 'events-to']) $(id).addEventListener('input', () => this.updateDateControls());
    $('events-filters').addEventListener('submit', async event => {
      event.preventDefault();
      const from = $('events-from').value, to = $('events-to').value;
      if (!from || !to || from > to || to > localDay(new Date())) {
        this.error('Choose a valid range ending today or earlier.'); return;
      }
      if (await this.load({ filters: { from, to } })) closeFilter();
    });
    this.doc.querySelectorAll('[data-date-preset]').forEach(button => button.addEventListener('click', async () => {
      if (await this.load({ filters: eventDatePreset(button.dataset.datePreset) })) closeFilter();
    }));
    $('events-refresh').addEventListener('click', () => this.load());
    $('events-more').addEventListener('click', () => this.load({ more: true }));
    $('events-search').addEventListener('input', () => { this.search = $('events-search').value; this.render(); });
    $('events-mail-filters').addEventListener('click', event => {
      const button = event.target.closest('[data-event-filter]');
      if (!button) return;
      this.mailFilter = button.dataset.eventFilter;
      this.doc.querySelectorAll('[data-event-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
      this.render();
    });
  }

  setEnabled(value) {
    if (!value && (this.enabled || this.controller)) {
      this.controller?.abort();
      this.controller = null;
      this.items = [];
      this.search = ''; this.mailFilter = 'all';
      this.$('events-search').value = '';
      this.doc.querySelectorAll('[data-event-filter]').forEach(item => item.setAttribute('aria-pressed', String(item.dataset.eventFilter === 'all')));
      this.cursor = null;
      this.filters = {};
      this.$('events-rows').replaceChildren();
      this.$('events-table').hidden = true;
      this.$('events-more').hidden = true;
      this.$('events-notice').hidden = true;
      this.$('events-from').value = '';
      this.$('events-to').value = '';
      this.$('events-range').textContent = 'This week · 10 events per page · Buenos Aires time';
      this.$('events-status').textContent = 'Load the simulated event timeline.';
      this.busy(false);
    }
    this.enabled = value;
  }

  updateDateControls() {
    const from = this.$('events-from'), to = this.$('events-to');
    const today = localDay(new Date());
    from.max = to.value || today; to.min = from.value; to.max = today;
    this.doc.querySelectorAll('[data-date-preset]').forEach(button => {
      const preset = eventDatePreset(button.dataset.datePreset);
      button.setAttribute('aria-pressed', String(preset.from === from.value && preset.to === to.value));
    });
  }

  busy(value) {
    this.$('events-panel').setAttribute('aria-busy', String(value));
    for (const id of ['events-more', 'events-refresh', 'events-apply']) this.$(id).disabled = value;
    this.doc.querySelectorAll('[data-date-preset]').forEach(button => { button.disabled = value; });
  }

  error(message) {
    if (this.$('events-date-dialog').open) { this.$('events-date-error').textContent = message; this.$('events-date-error').hidden = false; }
    this.$('events-notice').textContent = message;
    this.$('events-notice').hidden = false;
  }

  async load({ more = false, filters = this.filters } = {}) {
    const generation = this.generation = (this.generation || 0) + 1;
    this.busy(true); this.$('events-notice').hidden = true;
    try {
      const result = await api.events(filters, more ? this.cursor : null);
      if (generation !== this.generation) return;
      this.filters = result.filters; this.cursor = result.next_cursor;
      this.items = more ? [...this.items, ...result.items] : result.items;
      this.$('events-from').value = result.filters.from;
      this.$('events-to').value = result.filters.to;
      this.$('events-range').textContent = `${rangeLabel(result.filters.from, result.filters.to)} · Buenos Aires time`;
      this.$('events-filter-label').textContent = rangeLabel(result.filters.from, result.filters.to);
      this.updateDateControls();
      this.render();
      return true;
    } catch (error) { this.error(error.message); }
    finally { if (generation === this.generation) this.busy(false); }
  }

  render() {
    const element = (tag, text, className) => { const el = this.doc.createElement(tag); if (text != null) el.textContent = text; if (className) el.className = className; return el; };
    const visible = filterEvents(this.items, this.search, this.mailFilter);
    const cards = visible.map(event => {
      const card = element('article', null, 'event-card'); card.setAttribute('role', 'listitem');
      const isAlert = /exit|fail|down|alert|unhealthy|error/i.test(event.event_type);
      const isRecovery = /recover|healthy|start/i.test(event.event_type) && !isAlert;
      card.setAttribute('data-tone', isAlert ? 'warning' : isRecovery ? 'ok' : 'info');
      const icon = element('div', null, 'event-kind-icon'); icon.append(uiIcon(this.doc, isAlert ? 'alert' : isRecovery ? 'check' : 'activity'));
      const content = element('div', null, 'event-content');
      const heading = element('div', null, 'event-card-heading');
      const title = element('h3', event.event_type);
      const time = element('time', formatDate(event.occurred_at)); time.setAttribute('datetime', event.occurred_at);
      heading.append(title, time);
      const context = element('p', `${event.source} · ${event.system_id}`, 'event-context');
      const subject = element('span', event.subject?.id || 'No resource', 'event-subject');
      subject.prepend(uiIcon(this.doc, 'server'));
      if (event.subject?.type) subject.append(element('small', event.subject.type));
      const notification = event.notification || {};
      const group = notificationGroup(notification);
      const mail = element('div', null, `event-mail is-${group}`);
      const mailIcon = element('span', null, 'event-mail-icon'); mailIcon.append(uiIcon(this.doc, 'mail'), uiIcon(this.doc, group === 'published' ? 'check' : group === 'pending' ? 'clock' : 'alert', 'mail-status-icon'));
      const delivery = element('div'); delivery.append(element('strong', emailLabel(notification)));
      if (notification.email_recipient) delivery.append(element('small', notification.email_recipient));
      if (notification.published_at) delivery.append(element('small', formatDate(notification.published_at)));
      mail.append(mailIcon, delivery);
      const details = element('details', null, 'event-details');
      details.append(element('summary', 'View details'), element('pre', JSON.stringify(event.details, null, 2)));
      content.append(heading, context, subject);
      card.append(icon, content, mail, details); return card;
    });
    this.$('events-rows').replaceChildren(...cards);
    this.$('events-table').hidden = visible.length === 0;
    this.$('events-more').hidden = !this.cursor;
    this.$('events-status').textContent = this.items.length
      ? visible.length ? `${visible.length} of ${this.items.length} events loaded · Newest first` : 'No matching events. Try another search or load more.'
      : 'No events in this date range.';
  }
}
