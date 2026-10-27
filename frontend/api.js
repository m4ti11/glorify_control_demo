// The deployed system reads API Gateway using a Cognito JWT. This showcase
// reads only bundled example files; configuration changes stay in this module.
const FILES = Object.freeze({
  latest: 'metrics-latest.json', history: 'metrics-history.json', events: 'events.json',
  pg: 'pg-statements.json', operational: 'operational-report.json',
  'alert-rules': 'alert-rules.json', 'health-checks': 'health-checks.json',
  'notification-channels': 'notification-channels.json',
});
const cache = new Map();
const session = new Map();
const anchor = Date.now();
const reference = Date.parse('2026-01-15T12:00:00Z');
const day = () => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Argentina/Buenos_Aires', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(anchor - 86400000));
let refreshCount = 0;
function rebase(value) {
  if (Array.isArray(value)) return value.map(rebase);
  if (value && typeof value === 'object') return Object.fromEntries(Object.entries(value).map(([key, child]) => [key, rebase(child)]));
  if (typeof value === 'string' && /^2026-01-\d{2}T/.test(value)) return new Date(Date.parse(value) + anchor - reference).toISOString();
  return value;
}
async function read(kind) {
  if (!Object.hasOwn(FILES, kind)) throw new Error('Unknown demo dataset');
  if (!cache.has(kind)) {
    const pending = (async () => {
      const url = new URL('./demo/' + FILES[kind], import.meta.url);
      // The filename comes exclusively from FILES, never from user input.
      const response = await fetch(url);
      if (!response.ok) throw new Error('Could not load the bundled demo file');
      return rebase(await response.json());
    })();
    cache.set(kind, pending);
    pending.catch(() => cache.delete(kind));
  }
  return structuredClone(await cache.get(kind));
}
async function records(kind) {
  if (!session.has(kind)) session.set(kind, (await read(kind)).items);
  return session.get(kind);
}
function validate(kind, data) {
  if (!data?.name?.trim()) throw new Error('Name is required');
  if (kind === 'alert-rules') {
    if (data.resource_type !== 'host' || data.resource_id !== 'host' || !['cpu_percent', 'memory_percent', 'disk_percent'].includes(data.metric)) throw new Error('Only host CPU, memory and disk rules are supported');
    if (![data.threshold, data.recovery_threshold].every(n => Number.isFinite(n) && n >= 0 && n <= 100)) throw new Error('Thresholds must be between 0 and 100');
    if ((data.operator === '>=' && data.threshold <= data.recovery_threshold) || (data.operator === '<=' && data.threshold >= data.recovery_threshold)) throw new Error('Recovery must leave a margin below or above the alert threshold');
  }
  if (kind === 'health-checks') {
    if (!/^https:\/\/[^\s]+$/.test(data.url)) throw new Error('Enter an HTTPS URL');
    if (data.timeout_seconds >= data.interval_seconds) throw new Error('Timeout must be shorter than interval');
  }
  if (kind === 'notification-channels' && (!data.emails?.length || data.emails.some(email => !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)))) throw new Error('Enter valid email addresses');
}
export const api = {
  async latest() {
    const snapshot = await read('latest');
    const phase = ++refreshCount;
    const host = snapshot.resources.find(resource => resource.resource_type === 'HOST').metrics;
    host.cpu_percent = Math.round((29 + Math.sin(phase * 0.7) * 3) * 10) / 10;
    host.memory.percent = Math.round((56 + Math.cos(phase * 0.5) * 0.8) * 10) / 10;
    host.memory.used_mb = Math.round(host.memory.total_mb * host.memory.percent / 100);
    snapshot.measured_at = new Date(Date.now() - 12000).toISOString();
    snapshot.received_at = new Date(Date.now() - 9000).toISOString();
    return snapshot;
  },
  history: () => read('history'),
  async events(filters = {}, cursor = null) {
    const data = await read('events');
    const localDay = value => new Intl.DateTimeFormat('en-CA', { timeZone: 'America/Argentina/Buenos_Aires', year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(value));
    const from = filters.from || localDay(anchor - 6 * 86400000);
    const to = filters.to || localDay(anchor);
    const items = data.items.filter(item => localDay(item.occurred_at) >= from && localDay(item.occurred_at) <= to);
    const offset = Number(cursor || 0);
    return { items: items.slice(offset, offset + 10), filters: { from, to }, next_cursor: offset + 10 < items.length ? String(offset + 10) : null };
  },
  async pgReport(selectedDay = day()) {
    if (selectedDay !== day()) throw new Error('This demo includes one sample report for yesterday.');
    const data = await read('pg'); data.report.details.report_date = day(); return data;
  },
  async operationalReport(selectedDay = day()) {
    if (selectedDay !== day()) throw new Error('This demo includes one sample report for yesterday.');
    const data = await read('operational'); data.report_date = day(); return data;
  },
  pdfUrl: () => new URL('./demo/operational-report.pdf', import.meta.url).href,
  async configuration(kind, id = null, action = null, options = {}) {
    if (!['alert-rules', 'notification-channels', 'health-checks'].includes(kind)) throw new Error('Unknown configuration category');
    const items = await records(kind);
    const index = items.findIndex(item => item.id === id);
    const current = items[index];
    const body = options.body ? JSON.parse(options.body) : {};
    const method = options.method || 'GET';
    if (action === 'status') return { status: 'SIMULATED', recipients: (current?.data.emails || []).map(email => ({ email, status: 'SIMULATED' })) };
    if (action === 'sync' || id === 'import') return { simulated: true };
    if (method === 'GET') return { items: structuredClone(items), next_cursor: null, notifications_initialized: true };
    if (method !== 'POST' && !current) throw new Error('Record no longer exists');
    if (current && body.version != null && body.version !== current.version) throw new Error('Record changed; refresh before retrying');
    if (method === 'DELETE') {
      if (kind === 'notification-channels') {
        for (const other of ['health-checks', 'alert-rules']) if ((await records(other)).some(item => item.data.notification_channel_ids?.includes(id))) throw new Error('Remove references to this channel before deleting it');
      }
      items.splice(index, 1); return {};
    }
    validate(kind, body.data);
    const item = { kind, id: current?.id || body.id || crypto.randomUUID(), data: body.data, version: (current?.version || 0) + 1, updated_at: new Date().toISOString(), delivery: { status: 'SIMULATED' } };
    if (method === 'POST') items.push(item); else if (method === 'PUT') items[index] = item; else throw new Error('Unsupported demo operation');
    return structuredClone(item);
  },
};
