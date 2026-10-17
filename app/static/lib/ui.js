// Shared UI building blocks. Tools get these through the `kit` passed to mount().
import { h, svg, clear } from './dom.js';
import { icon } from './icons.js';
import * as fmt from './fmt.js';

export function badge(text, kind = '') {
  return h('span', { class: `badge ${kind}`.trim() }, text);
}

// health ('ok' | 'warn' | 'error' | 'unknown') -> badge kind
export const healthKind = (health) => ({ ok: 'ok', warn: 'warn', error: 'bad' }[health] || '');

export function skeleton(lines = 3) {
  return h('div', { class: 'skel', 'aria-hidden': 'true' }, Array.from({ length: lines }, () => h('i')));
}

export function errorBox(error, onRetry) {
  return h('div', { class: 'err', role: 'alert' },
    h('strong', {}, icon('alert', 18), error?.message || 'Something went wrong.'),
    error?.hint && h('span', { class: 'hint' }, error.hint),
    onRetry && h('button', { class: 'btn', type: 'button', title: 'Ask again', onclick: onRetry }, icon('refresh', 16), 'Try again'));
}

// A titled card whose body can be swapped, with a refresh button and a "busy" state.
export function card({ title, iconName, wide = false, onRefresh }) {
  const badgeSlot = h('span');
  const updated = h('span', { class: 'updated' });
  const heading = h('h3', {}, iconName && icon(iconName, 18), h('span', { class: 'card-title' }, title));
  const body = h('div', { class: 'card-body' }, skeleton());
  const refresh = onRefresh && h('button', { class: 'refresh', type: 'button', 'aria-label': `Refresh ${title}`, title: 'Refresh', onclick: onRefresh }, icon('refresh', 16));
  const el = h('section', { class: `card${wide ? ' wide' : ''}`, 'aria-label': title },
    h('div', { class: 'card-head' }, heading, badgeSlot, updated, refresh),
    body);
  return {
    el, body,
    set(...children) { clear(body).append(...children.flat(Infinity).filter(Boolean)); },
    setTitle(text) { heading.querySelector('.card-title').textContent = text; el.setAttribute('aria-label', text); },
    setBadge(node) { clear(badgeSlot); if (node) badgeSlot.append(node); },
    busy(on) { el.setAttribute('aria-busy', on ? 'true' : 'false'); },
    stamp(blank) { updated.textContent = blank === '' ? '' : `Updated ${new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}`; },
  };
}

export function stat(label, value, sub) {
  return h('div', { class: 'stat' }, h('div', { class: 'k' }, label), h('div', { class: 'v' }, value),
    sub && h('div', { class: 's' }, sub));
}

// Ring gauge for one ink colour. ink = {key, name, percent|null, low, empty}
const animated = new Set();  // gauges sweep in once, not on every refresh

export function gauge(ink) {
  const R = 40, C = 2 * Math.PI * R;
  const known = ink.percent != null;
  const firstTime = !animated.has(ink.key);
  animated.add(ink.key);
  const fill = svg('circle', { class: 'fill', cx: 50, cy: 50, r: R, fill: 'none', 'stroke-width': 11,
    'stroke-linecap': 'round', 'stroke-dasharray': `0 ${C}`, transform: 'rotate(-90 50 50)' });
  const full = known ? `${(C * ink.percent) / 100} ${C}` : `0 ${C}`;
  if (!firstTime) fill.setAttribute('stroke-dasharray', full);
  else requestAnimationFrame(() => requestAnimationFrame(() => fill.setAttribute('stroke-dasharray', full)));
  const label = known ? `${ink.name} ink, about ${ink.percent} percent (the printer's own estimate)` : `${ink.name} ink level unknown`;
  // a plain HTML tooltip (SVG <title> tooltips are unreliable across browsers)
  const tip = known ? `${ink.name}: about ${ink.percent}%. Levels are the printer's own estimate.` : `${ink.name}: the printer didn't report a level.`;
  return h('div', { class: `gauge ${ink.key}${known ? '' : ' unknown'}${ink.percent === 0 ? ' zero' : ''}`, title: tip },
    svg('svg', { viewBox: '0 0 100 100', role: 'img', 'aria-label': label },
      svg('circle', { class: 'track', cx: 50, cy: 50, r: R, fill: 'none', 'stroke-width': 11 }),
      fill,
      svg('text', { x: 50, y: 58, 'text-anchor': 'middle' }, known ? `${ink.percent}%` : '?')),
    h('div', { class: 'name' }, ink.name),
    ink.empty ? badge('Empty', 'bad') : ink.low ? badge('Low', 'warn') : null);
}

export function toast(message, kind = '') {
  let box = document.getElementById('toasts');
  if (!box) { box = h('div', { id: 'toasts', role: 'status', 'aria-live': 'polite' }); document.body.append(box); }
  const el = h('div', { class: `toast ${kind}`.trim() }, message);
  box.append(el);
  setTimeout(() => el.remove(), 5000);
}

// Run fn now and every `ms` while the page is visible and the tool is shown.
export function poll(fn, ms, active = () => true) {
  let running = false, stopped = false;
  const tick = async () => {
    if (stopped || running || document.hidden || !active()) return;
    running = true;
    try { await fn(); } finally { running = false; }
  };
  const timer = setInterval(tick, ms);
  document.addEventListener('visibilitychange', tick);
  window.addEventListener('pp:tool-shown', tick);
  tick();
  return {
    now: tick,
    stop() { stopped = true; clearInterval(timer); document.removeEventListener('visibilitychange', tick); window.removeEventListener('pp:tool-shown', tick); },
  };
}
