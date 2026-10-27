export const MODULES = {
  metrics: { title: 'Metrics', description: 'CPU, memory and containers on your VPS.', group: 'Monitoring', view: 'metrics' },
  events: { title: 'Events', description: 'Agent activity and notification status.', group: 'Monitoring', view: 'events' },
  reports: { title: 'Reports', description: 'PostgreSQL data and assisted operational analysis.', group: 'Monitoring', view: 'reports' },
  configuration: { title: 'Configuration', description: 'Alerts, sites and recipients.', group: 'Administration', view: 'configuration' },
};

export function moduleFromHash(hash) {
  const route = hash.replace(/^#/, '').split('/')[0];
  const key = ['health-checks', 'alert-rules', 'notification-channels'].includes(route) ? 'configuration' : ['postgresql', 'operational-report'].includes(route) ? 'reports' : route;
  return Object.hasOwn(MODULES, key) ? key : 'metrics';
}

export class ModuleNavigation {
  constructor(onChange, browser = window, doc = document) {
    this.browser = browser; this.doc = doc; this.onChange = onChange;
    this.active = null;
    browser.addEventListener('hashchange', () => this.render(true));
    this.render();
  }

  render(focus = false) {
    const key = moduleFromHash(this.browser.location.hash);
    if (key === this.active) return;
    const module = MODULES[key];
    this.active = key;
    for (const panel of this.doc.querySelectorAll('[data-module-view]')) panel.hidden = panel.dataset.moduleView !== module.view;
    for (const link of this.doc.querySelectorAll('[data-module]')) {
      if (link.dataset.module === key) link.setAttribute('aria-current', 'page');
      else link.removeAttribute('aria-current');
    }
    this.doc.getElementById('module-title').textContent = module.title;
    this.doc.getElementById('module-description').textContent = module.description;
    this.doc.getElementById('module-eyebrow').textContent = module.group;
    this.doc.getElementById('status').hidden = key !== 'metrics';
    this.doc.title = `Glorify Control · ${module.title}`;
    if (focus && !this.doc.getElementById('app-screen').hidden) this.doc.getElementById('module-title').focus();
    this.onChange(key, module);
  }
}
