export const LOCALE = 'en-GB';
export const TIME_ZONE = 'America/Argentina/Buenos_Aires';
export function formatDay(value) {
  return /^\d{4}-\d{2}-\d{2}$/.test(value || '') ? value : 'YYYY-MM-DD';
}
export function formatDateTime(value) {
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return 'Unavailable';
  const parts = Object.fromEntries(new Intl.DateTimeFormat(LOCALE, { timeZone: TIME_ZONE, hourCycle: 'h23', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit' }).formatToParts(date).map(part => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}
// Keep the native calendar and keyboard controls while presenting one date format.
// Native date-field text otherwise follows the browser/OS locale rather than lang.
export function enhanceDateInputs(doc = document) {
  doc.querySelectorAll('input[type="date"]').forEach(input => {
    if (input.classList.contains('formatted-date')) return;
    const wrapper = doc.createElement('span'); wrapper.className = 'date-input';
    const display = doc.createElement('span'); display.className = 'date-input-display'; display.setAttribute('aria-hidden', 'true');
    input.before(wrapper); wrapper.append(input, display);
    input.classList.add('formatted-date'); input.lang = LOCALE;
    const update = () => { display.textContent = formatDay(input.value); };
    const prototype = Object.getPrototypeOf(input);
    const descriptor = Object.getOwnPropertyDescriptor(prototype, 'value');
    Object.defineProperty(input, 'value', { configurable: true, get() { return descriptor.get.call(this); }, set(value) { descriptor.set.call(this, value); update(); } });
    input.addEventListener('input', update); input.addEventListener('change', update); update();
  });
}
