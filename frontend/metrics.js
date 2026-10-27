import { uiIcon } from './ui-icons.js';

export function cpuWindowLabel(cpu) {
  if (cpu?.method !== 'cumulative_counters' || !Number.isFinite(cpu.window_seconds) || cpu.window_seconds <= 0) {
    return 'Measurement window unavailable';
  }
  const seconds = Math.max(1, Math.round(cpu.window_seconds));
  const duration = seconds % 60 === 0 ? `${seconds / 60} min` : `${seconds} s`;
  return `Average over the last ${duration}`;
}

export function usagePercent(value) {
  return Number.isFinite(value) ? Math.min(100, Math.max(0, value)) : 0;
}

export function latestPostgresSample(postgres) {
  const samples = postgres?.samples;
  if (!Array.isArray(samples)) return null;
  return samples
    .filter(sample => sample?.available === true && Array.isArray(sample.connections))
    .sort((a, b) => Date.parse(b.timestamp) - Date.parse(a.timestamp))[0] ?? null;
}

export function postgresConnectionTotals(sample) {
  if (!sample) return null;
  return sample.connections.reduce((totals, connection) => ({
    connections: totals.connections + (Number.isFinite(connection.connections) ? connection.connections : 0),
    active: totals.active + (Number.isFinite(connection.active) ? connection.active : 0),
    idle: totals.idle + (Number.isFinite(connection.idle) ? connection.idle : 0),
  }), { connections: 0, active: 0, idle: 0 });
}

export function sortPostgresConnections(connections = []) {
  return [...connections].sort((a, b) => b.connections - a.connections || String(a.database).localeCompare(String(b.database), 'en'));
}

export function postgresConnectionSegments(connection) {
  const count = value => Number.isFinite(value) ? Math.max(0, value) : 0;
  const total = count(connection.connections);
  const active = Math.min(total, count(connection.active));
  const idle = Math.min(total - active, count(connection.idle));
  return [
    { key: 'active', label: 'Active', value: active },
    { key: 'idle', label: 'Idle', value: idle },
    { key: 'other', label: 'Other states', value: total - active - idle },
  ];
}

export function sortContainers(resources, sort = 'cpu') {
  const field = { cpu: 'cpu_percent', memory: 'memory_usage_mb', restarts: 'restart_count' }[sort];
  const numeric = value => Number.isFinite(value) ? value : -Infinity;
  return [...resources].sort((a, b) => {
    const difference = field ? numeric(b.metrics[field]) - numeric(a.metrics[field]) : 0;
    return difference || String(a.metrics.name ?? '').localeCompare(String(b.metrics.name ?? ''), 'en', { numeric: true });
  });
}

export function filterContainers(resources, filter = 'all', query = '') {
  const search = query.trim().toLocaleLowerCase('en');
  return resources.filter(({ metrics: item }) => {
    const matches = filter === 'running' ? item.state === 'running'
      : filter === 'stopped' ? item.state !== 'running'
      : filter === 'attention' ? ['unhealthy', 'dead'].includes(item.health) || item.state === 'dead' || item.restart_count > 0 : true;
    return matches && `${item.name || ''} ${item.image || ''}`.toLocaleLowerCase('en').includes(search);
  });
}

export function renderContainers(doc, resources, sort = 'cpu', filter = 'all', query = '') {
  const body = doc.getElementById('containers');
  const element = (tag, text, className) => { const el = doc.createElement(tag); if (text != null) el.textContent = text; if (className) el.className = className; return el; };
  const format = (value, unit = '') => Number.isFinite(value) ? `${value.toLocaleString('en-US', { maximumFractionDigits: 1 })}${unit}` : 'Unavailable';
  const memoryMax = Math.max(1, ...resources.map(({ metrics }) => Number.isFinite(metrics.memory_usage_mb) ? metrics.memory_usage_mb : 0));
  const visible = sortContainers(filterContainers(resources, filter, query), sort);
  const cards = visible.map(({ metrics: item }) => {
    const card = element('article', null, 'container-card'); card.setAttribute('role', 'listitem');
    const identity = element('div', null, 'container-card-identity');
    const icon = doc.createElementNS('http://www.w3.org/2000/svg', 'svg'); icon.setAttribute('class', 'docker-icon'); icon.setAttribute('aria-hidden', 'true');
    const use = doc.createElementNS('http://www.w3.org/2000/svg', 'use'); use.setAttribute('href', '#icon-docker'); icon.append(use);
    const name = element('div'); name.append(element('h3', item.name || 'Unnamed'), element('small', item.image || 'No image'));
    identity.append(icon, name);
    const states = element('div', null, 'container-card-states');
    for (const [label, good, bad] of [[item.state || 'Unknown state', item.state === 'running', ['dead', 'exited'].includes(item.state)], [item.health || 'Health unavailable', item.health === 'healthy', item.health === 'unhealthy']]) {
      states.append(element('span', label, `pill${good ? ' ok' : ''}${bad ? ' danger' : ''}`));
    }
    if (item.restart_count > 0) states.append(element('span', `${item.restart_count} ${item.restart_count === 1 ? 'restart recorded' : 'restarts recorded'}`, 'pill warning'));
    if (item.health === 'unhealthy') states.append(element('span', 'Health check failing', 'pill warning'));
    if (item.state === 'dead') states.append(element('span', 'Container stopped unexpectedly', 'pill warning'));
    const readings = element('dl', null, 'container-readings');
    for (const [label, value, iconName, percent] of [
      ['CPU', format(item.cpu_percent, '%'), 'cpu', Number.isFinite(item.cpu_percent) ? usagePercent(item.cpu_percent) : null],
      ['Memory', format(item.memory_usage_mb, ' MB'), 'memory', Number.isFinite(item.memory_usage_mb) ? usagePercent(item.memory_usage_mb / memoryMax * 100) : null],
      ['Restarts', format(item.restart_count), 'restart', null],
    ]) {
      const reading = element('div', null, 'container-reading');
      const title = element('dt'); title.append(uiIcon(doc, iconName), element('span', label));
      reading.append(title, element('dd', value));
      if (percent !== null) { const rail = element('div', null, `resource-bar ${iconName === 'memory' ? 'memory-bar' : ''}`); rail.setAttribute('aria-hidden', 'true'); rail.title = iconName === 'memory' ? 'Memory relative to the largest container' : 'CPU · scale from 0 to 100%'; const fill = element('span'); fill.style.width = `${percent}%`; rail.append(fill); reading.append(rail); }
      readings.append(reading);
    }
    card.append(identity, states, readings); return card;
  });
  body.replaceChildren(...cards);
  for (const key of ['all', 'running', 'attention', 'stopped']) doc.getElementById('container-filter-' + key).textContent = filterContainers(resources, key).length;
  doc.getElementById('no-containers').hidden = visible.length > 0;
}
