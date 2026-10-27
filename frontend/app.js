import { formatDateTime, enhanceDateInputs } from './dates.js';
import { api } from './api.js';
import { EventsPanel } from './events.js?v=showcase-4';
import { ReportsPanel } from './reports.js';
import { ConfigurationPanel } from './configuration.js';
import { ModuleNavigation } from './navigation.js';
import { cpuWindowLabel, latestPostgresSample, postgresConnectionSegments, postgresConnectionTotals, renderContainers, sortPostgresConnections, usagePercent } from './metrics.js';

enhanceDateInputs(document);

const $ = (id) => document.getElementById(id);
const number = (value, unit = '') => typeof value === 'number' && Number.isFinite(value)
  ? `${value.toLocaleString('en-US', { maximumFractionDigits: 1 })}${unit}` : '—';
const date = (value) => formatDateTime(value);
let receivedAt = null;
function updateReadingAge() {
  if (!receivedAt || $('dashboard').hidden) return;
  const elapsed = Math.max(0, Date.now() - new Date(receivedAt).getTime());
  const seconds = Math.floor(elapsed / 1000);
  $('reading-age').textContent = seconds < 60 ? 'Updated less than a minute ago' : seconds < 3600 ? `Updated ${Math.floor(seconds / 60)} min` : `Updated at ${date(receivedAt)}`;
  const stale = elapsed > 5 * 60 * 1000;
  $('snapshot-age').hidden = !stale;
  $('snapshot-age').textContent = `This snapshot is old. Last available reading received at ${date(receivedAt)}.`;
  if (!loading) {
    $('status').textContent = stale ? 'Stale data' : 'Simulated reading';
    $('status').className = `status ${stale ? 'stale' : 'live'}`;
  }
}

let loading = false;
const eventsPanel = new EventsPanel();
const reportsPanel = new ReportsPanel();
const configurationPanel = new ConfigurationPanel();
const navigation = new ModuleNavigation((key, module) => {
  if (module.view === 'configuration') configurationPanel.setKind(['health-checks', 'notification-channels'].find(kind => window.location.hash.includes(kind)) || 'alert-rules');
  loadModule(key);
});
function loadModule(key) {
  if (key === 'metrics') refresh();
  else if (key === 'events') eventsPanel.load();
  else if (key === 'reports') reportsPanel.load();
  else if (key === 'configuration') configurationPanel.load();
}
function showError(error) {
  $('notice').textContent = error.message;
  $('notice').hidden = false;
}

let containerResources = [];
let containerFilter = 'all';
let containerSort = 'cpu';
function renderContainerCards() {
  renderContainers(document, containerResources, containerSort, containerFilter, $('container-search').value);
}
$('container-search').addEventListener('input', renderContainerCards);
$('container-sort').addEventListener('click', event => {
  const button = event.target.closest('[data-container-sort]');
  if (!button) return;
  containerSort = button.dataset.containerSort;
  document.querySelectorAll('[data-container-sort]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  renderContainerCards();
});
$('container-filters').addEventListener('click', event => {
  const button = event.target.closest('[data-container-filter]');
  if (!button) return;
  containerFilter = button.dataset.containerFilter;
  document.querySelectorAll('[data-container-filter]').forEach(item => item.setAttribute('aria-pressed', String(item === button)));
  renderContainerCards();
});

function renderPostgresChart(rows) {
  const chart = $('postgres-connections-chart');
  const maximum = Math.max(1, ...rows.map(row => row.connections));
  chart.replaceChildren(...rows.map(connection => {
    const item = document.createElement('div');
    item.className = 'postgres-bar';
    const label = document.createElement('span');
    label.className = 'postgres-bar-label';
    label.textContent = connection.database || 'Unnamed';
    label.title = label.textContent;
    const rail = document.createElement('div');
    rail.className = 'postgres-bar-rail';
    const fill = document.createElement('span');
    fill.className = 'postgres-bar-fill';
    const segments = postgresConnectionSegments(connection);
    const total = segments.reduce((sum, segment) => sum + segment.value, 0);
    fill.style.width = `${total / maximum * 100}%`;
    for (const segment of segments) {
      if (!segment.value) continue;
      const part = document.createElement('span');
      part.className = `postgres-segment is-${segment.key}`;
      part.style.width = `${segment.value / total * 100}%`;
      part.title = `${segment.label}: ${number(segment.value)}`;
      fill.append(part);
    }
    rail.setAttribute('role', 'img');
    rail.setAttribute('aria-label', `${label.textContent}: ${number(total)} connections. ${segments.map(segment => `${segment.label}: ${number(segment.value)}`).join(', ')}`);
    rail.append(fill);
    const value = document.createElement('strong');
    value.textContent = number(connection.connections);
    item.append(label, rail, value);
    return item;
  }));
  if (!rows.length) chart.textContent = 'No connections in this sample.';
}

function renderPostgresConnections(postgres) {
  const sample = latestPostgresSample(postgres);
  const totals = postgresConnectionTotals(sample);
  $('postgres-connections-panel').hidden = !sample;
  if (!sample || !totals) {
    $('postgres-connections').textContent = '—';
    $('postgres-detail').textContent = 'No heartbeat data';
    return;
  }
  $('postgres-connections').textContent = number(totals.connections);
  $('postgres-detail').textContent = `${number(totals.active)} active · ${number(totals.idle)} idle`;
  $('postgres-sample-time').textContent = `Sample: ${date(sample.timestamp)}`;
  const rows = sortPostgresConnections(sample.connections);
  renderPostgresChart(rows);
  const totalsByState = rows.reduce((counts, connection) => {
    postgresConnectionSegments(connection).forEach(segment => { counts[segment.key] += segment.value; });
    return counts;
  }, { active: 0, idle: 0, other: 0 });
  for (const [key, value] of Object.entries(totalsByState)) $('postgres-total-' + key).textContent = number(value);
  $('postgres-total-connections').textContent = number(totals.connections);
}

function render(snapshot) {
  if (!Array.isArray(snapshot.resources) || !snapshot.system_id || !snapshot.measured_at || !snapshot.received_at) {
    throw new Error('The demo snapshot is invalid.');
  }
  const host = snapshot.resources.find((resource) => resource.resource_type === 'HOST')?.metrics;
  if (!host?.memory || !host?.disk) throw new Error('The snapshot has no host metrics.');
  const containers = snapshot.resources.filter((resource) => resource.resource_type === 'CONTAINER');
  const postgres = snapshot.resources.find((resource) => resource.resource_type === 'POSTGRES')?.metrics;
  reportsPanel.setSystem(snapshot.system_id);
  configurationPanel.setSystem(snapshot.system_id);
  $('measured').textContent = `Measured: ${date(snapshot.measured_at)}`;
  $('received').textContent = `Last received: ${date(snapshot.received_at)}`;
  for (const [id, value] of [['cpu', host.cpu_percent], ['memory', host.memory.percent], ['disk', host.disk.percent]]) {
    $(id).textContent = number(value, '%');
    $(`${id}-meter`).value = usagePercent(value);
    $(`${id}-chart`).style.setProperty('--usage', usagePercent(value));
    $(`${id}-chart`).classList.toggle('unavailable', !Number.isFinite(value));
  }
  $('cpu-detail').textContent = cpuWindowLabel(host.extended?.cpu);
  $('memory-detail').textContent = `${number(host.memory.used_mb)} / ${number(host.memory.total_mb)} MB`;
  $('disk-detail').textContent = `${number(host.disk.used_gb)} / ${number(host.disk.total_gb)} GB · ${host.disk.path}`;
  const running = containers.filter((resource) => resource.metrics.state === 'running').length;
  $('containers-count').textContent = `${running} / ${containers.length}`;
  $('containers-detail').textContent = 'Running / included in snapshot';
  $('containers-chart').style.setProperty('--usage', containers.length ? running / containers.length * 100 : 0);
  containerResources = containers;
  renderContainerCards();
  renderPostgresConnections(postgres);
  $('raw').textContent = JSON.stringify(snapshot, null, 2);
  $('dashboard').hidden = false;
  $('empty').hidden = true;
  receivedAt = snapshot.received_at;
  updateReadingAge();

}

async function refresh(event) {
  event?.preventDefault();
  if (loading) return;
  loading = true;
  $('refresh').disabled = true;
  $('metrics-notice').hidden = true;
  $('status').textContent = 'Loading…';
  $('status').className = 'status';
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12000);
  try {
    const snapshot = await api.latest();
    render(snapshot);
  } catch (error) {
    $('dashboard').hidden = true;
    $('empty').hidden = false;
    receivedAt = null;
    $('reading-age').textContent = '';
    $('metrics-notice').textContent = error.name === 'AbortError' ? 'Loading took too long. Try again.'
      : error instanceof TypeError ? 'Could not load the bundled demo files. Check the local static server.' : error.message;
    $('metrics-notice').hidden = false;
    $('status').textContent = 'No current data';
    $('status').className = 'status error';
  } finally {
    clearTimeout(timeout);
    loading = false;
    $('refresh').disabled = false;
    updateReadingAge();
  }
}

$('connection').addEventListener('submit', refresh);
$('auto').checked = true;
$('auto').disabled = false;
$('refresh').disabled = false;
configurationPanel.setEnabled(true);
reportsPanel.setEnabled(true);
eventsPanel.setEnabled(true);
loadModule(navigation.active);
setInterval(updateReadingAge, 1000);
setInterval(() => {
  if ($('auto').checked && !document.hidden && navigation.active === 'metrics') refresh();
}, 30000);

const menuToggle = $('menu-toggle');
const closeMenu = () => { menuToggle.setAttribute('aria-expanded', 'false'); menuToggle.setAttribute('aria-label', 'Open navigation menu'); };
menuToggle.addEventListener('click', () => {
  const expanded = menuToggle.getAttribute('aria-expanded') !== 'true';
  menuToggle.setAttribute('aria-expanded', String(expanded));
  menuToggle.setAttribute('aria-label', expanded ? 'Close navigation menu' : 'Open navigation menu');
});
$('module-navigation').addEventListener('click', event => {
  if (event.target.closest('a')) { closeMenu(); $('module-title').focus(); }
});
window.addEventListener('hashchange', closeMenu);
document.querySelector('.sidebar').addEventListener('keydown', event => {
  if (event.key === 'Escape' && menuToggle.getAttribute('aria-expanded') === 'true') { closeMenu(); menuToggle.focus(); }
});
