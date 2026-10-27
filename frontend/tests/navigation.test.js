import assert from 'node:assert/strict';
import test from 'node:test';
import { MODULES, moduleFromHash, ModuleNavigation } from '../navigation.js';

test('unknown and inherited hash names fall back to metrics', () => {
  for (const hash of ['', '#missing', '#constructor', '#__proto__']) assert.equal(moduleFromHash(hash), 'metrics');
  for (const key of Object.keys(MODULES)) assert.equal(moduleFromHash('#' + key), key);
});

test('navigation isolates views, restores history and keeps the selected configuration category', () => {
  const views = ['metrics', 'events', 'reports', 'configuration'].map(moduleView => ({ dataset: { moduleView } }));
  const links = Object.keys(MODULES).map(key => ({
    dataset: { module: key }, attrs: {},
    setAttribute(name, value) { this.attrs[name] = value; },
    removeAttribute(name) { delete this.attrs[name]; },
  }));
  let focused = 0;
  const elements = Object.fromEntries(['module-title', 'module-description', 'module-eyebrow', 'status', 'app-screen'].map(id => [id, { focus() { focused++; } }]));
  const doc = { getElementById: id => elements[id], querySelectorAll: selector => selector === '[data-module-view]' ? views : links };
  const handlers = {}; const browser = { location: { hash: '#configuration' }, addEventListener: (event, handler) => { handlers[event] = handler; } };
  const changes = [];
  const nav = new ModuleNavigation(key => changes.push(key), browser, doc);
  assert.equal(nav.active, 'configuration');
  assert.deepEqual(views.filter(view => !view.hidden).map(view => view.dataset.moduleView), ['configuration']);
  assert.equal(elements.status.hidden, true);
  for (const key of ['metrics', 'events', 'configuration', 'reports', 'metrics', 'configuration']) {
    browser.location.hash = '#' + key; handlers.hashchange();
    assert.equal(nav.active, key);
    assert.equal(views.filter(view => !view.hidden).length, 1);
    assert.equal(links.filter(link => link.attrs['aria-current'] === 'page')[0].dataset.module, key);
  }
  assert.equal(focused, 6);
  assert.equal(doc.title, 'Glorify Control · Configuration');
  handlers.hashchange();
  assert.equal(changes.length, 7, 'the same route does not trigger duplicate loads');
  elements['app-screen'].hidden = true;
  browser.location.hash = '#metrics'; handlers.hashchange();
  assert.equal(focused, 6, 'logged-out navigation does not focus hidden headings');
});

test('legacy configuration links open the unified module', () => {
  for (const kind of ['health-checks', 'alert-rules', 'notification-channels']) assert.equal(moduleFromHash('#' + kind), 'configuration');
});

test('old report URLs open the unified reports module', () => {
  for (const hash of ['#postgresql', '#operational-report', '#reports/raw', '#reports/ai']) assert.equal(moduleFromHash(hash), 'reports');
});
