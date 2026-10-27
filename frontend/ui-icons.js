const PATHS = {
  copy: 'M9 9h11v11H9zM15 5V3H3v12h2',
  mail: 'M3 5h18v14H3zM3 6l9 7 9-7',
  check: 'm5 12 4 4L19 6',
  clock: 'M12 8v4l3 2M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0',
  alert: 'M12 3 2 20h20L12 3ZM12 9v4M12 17h.01',
  activity: 'M3 12h4l3-8 4 16 3-8h4',
  server: 'M4 3h16v7H4zM4 14h16v7H4zM7 6h.01M7 17h.01M11 6h6M11 17h6',
  cpu: 'M6 6h12v12H6zM9 9h6v6H9zM9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3',
  memory: 'M3 6h18v12H3zM7 10v4M12 10v4M17 10v4M7 18v3M17 18v3',
  restart: 'M20 7v5h-5M4 17v-5h5M6 7a7 7 0 0 1 12-1l2 3M4 15l2 3a7 7 0 0 0 12-1',
};
export function uiIcon(doc, name, className = 'ui-icon') {
  const icon = doc.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({ class: className, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.7', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) icon.setAttribute(key, value);
  const path = doc.createElementNS('http://www.w3.org/2000/svg', 'path');
  path.setAttribute('d', PATHS[name] || PATHS.activity); icon.append(path); return icon;
}
