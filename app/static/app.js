// The shell: theme, launcher tiles, and a registry-driven set of tools.
import { api } from './lib/api.js';
import { h, clear, fill } from './lib/dom.js';
import { icon } from './lib/icons.js';
import { makeKit } from './lib/kit.js';
import { errorBox } from './lib/ui.js';
import { store } from './lib/printer-store.js';
import { admin, wizard } from './printers.js';

const $ = (id) => document.getElementById(id);
const MANAGE = '__manage';

// ---------------------------------------------------------------- theme
const THEMES = ['auto', 'light', 'dark'];
const THEME_ICON = { auto: 'auto', light: 'sun', dark: 'moon' };

function currentTheme() {
  const t = document.documentElement.getAttribute('data-theme');
  return t === 'light' || t === 'dark' ? t : 'auto';
}

function applyTheme(theme) {
  if (theme === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', theme);
  try { theme === 'auto' ? localStorage.removeItem('pp-theme') : localStorage.setItem('pp-theme', theme); } catch { /* blocked */ }
  const btn = $('theme-btn');
  btn.replaceChildren(icon(THEME_ICON[theme], 20));
  btn.setAttribute('aria-label', `Theme: ${theme}. Click to change.`);
  btn.title = `Theme: ${theme}`;
}

$('theme-btn').addEventListener('click', () => {
  applyTheme(THEMES[(THEMES.indexOf(currentTheme()) + 1) % THEMES.length]);
});
applyTheme(currentTheme());

// ---------------------------------------------------------------- launcher
function tile(link) {
  const letter = () => h('span', { class: 'plate letter', style: { '--hue': link.hue }, 'aria-hidden': 'true' }, link.letter);
  let plate;
  if (link.logo) {
    const img = h('img', { src: link.logo, alt: '', loading: 'lazy', decoding: 'async' });
    plate = h('span', { class: 'plate', 'aria-hidden': 'true' }, img);
    img.addEventListener('error', () => plate.replaceWith(letter()));  // broken image -> letter tile
  } else {
    plate = letter();
  }
  const attrs = { class: 'tile', href: link.url, title: link.name };  // full name when a narrow tile clips it
  if (link.new_tab) { attrs.target = '_blank'; attrs.rel = 'noopener noreferrer'; }
  return h('li', {}, h('a', attrs, plate,
    h('span', { class: 'tile-text' }, h('div', { class: 'tile-name' }, link.name),
      link.description && h('div', { class: 'tile-desc' }, link.description)),
    link.new_tab && h('span', { class: 'ext', 'aria-label': 'opens in a new tab' }, icon('external', 15))));
}

let lastLinks = '';
async function loadLinks() {
  // links follow the selected printer: {ip}, {queue}... are filled in for it by the server
  const id = store.current?.id;
  const res = await api('/links' + (id ? `?printer=${encodeURIComponent(id)}` : ''));
  if (!res.data) return;  // server unreachable: keep what is on screen
  const cfg = res.data;
  const sig = JSON.stringify(cfg);
  if (sig === lastLinks) return;
  lastLinks = sig;

  const notice = clear($('links-notice'));
  if (cfg.error) {
    notice.append(h('div', { class: 'notice bad', role: 'alert' }, icon('alert', 18), h('div', {}, cfg.error)));
  }
  if (cfg.warnings?.length) {
    notice.append(h('div', { class: 'notice warn' }, icon('info', 18),
      h('details', {}, h('summary', {}, `${cfg.warnings.length} note${cfg.warnings.length === 1 ? '' : 's'} about links.yml`),
        h('ul', {}, cfg.warnings.map((w) => h('li', {}, w))))));
  }
  const grid = clear($('links-grid'));
  grid.style.setProperty('--n', Math.max(1, cfg.links.length));  // the CSS shares the row between this many tiles
  grid.append(...cfg.links.map(tile));
  $('launcher').hidden = !(cfg.links.length || cfg.error || cfg.warnings?.length);
}

// ---------------------------------------------------------------- printers
async function loadPrinters() {
  const res = await api('/printers');
  if (!res.ok) return false;
  store.set(res.data.printers, res.data.defaults);
  return true;
}

// ---------------------------------------------------------------- views
// A "view" is one tab of one tool (a tool without tabs has a single view). Address: #<tool> or #<tool>/<tab>.
const mounted = new Map();  // view key -> {root}
const cssLoaded = new Set();
let tools = [];
let views = [];
let shown = null;  // which page is on screen: 'setup' | 'printers' | 'view:<key>'

function buildViews() {
  views = tools.flatMap((t) => (t.error || !t.tabs?.length
    ? [{ key: t.id, tool: t, tabId: null, title: t.title, icon: t.icon }]
    : t.tabs.map((tab) => ({ key: `${t.id}/${tab.id}`, tool: t, tabId: tab.id, title: tab.title, icon: tab.icon || t.icon }))));
}

const visibleViews = () => views.filter((v) => !v.tool.needs_printer || store.list.length > 0);

function activeView() {
  const route = location.hash.slice(1);
  const list = visibleViews();
  return list.find((v) => v.key === route)
    || list.find((v) => v.tool.id === route)  // "#maintenance" means that tool's first tab
    || list[0] || null;
}

async function mountView(v) {
  const t = v.tool;
  const root = h('section', { class: 'tool', id: `tool-${v.key.replace('/', '-')}`, role: 'tabpanel', hidden: true });
  $('tools').append(root);
  mounted.set(v.key, { root });
  const content = h('div', { class: 'tool-body' });
  root.append(content);
  try {
    if (t.error) throw new Error(t.error);
    if (t.css && !cssLoaded.has(t.id)) { cssLoaded.add(t.id); document.head.append(h('link', { rel: 'stylesheet', href: t.css })); }
    const mod = await import(t.entry);
    await mod.mount(content, makeKit(root, t.id, v.tabId));
  } catch (e) {
    console.error(`tool ${v.key} failed`, e);
    content.append(errorBox({ message: `The "${v.title}" tab could not start.`, hint: String(e.message || e) }));
  }
}

function paintTabs() {
  const list = visibleViews();
  const tabs = $('tool-tabs');
  tabs.hidden = list.length < 2 || !shown?.startsWith('view:');
  const sig = list.map((v) => v.key).join();
  if (tabs.dataset.sig !== sig) {
    tabs.dataset.sig = sig;
    fill(tabs, list.map((v) => h('a', { class: 'tab', href: `#${v.key}`, role: 'tab', 'data-key': v.key }, icon(v.icon || 'wrench', 16), v.title)));
  }
  const current = activeView();
  tabs.querySelectorAll('a').forEach((a) => {
    const on = !!current && a.dataset.key === current.key;
    a.setAttribute('aria-selected', String(on));
    if (on) a.setAttribute('aria-current', 'page'); else a.removeAttribute('aria-current');
  });
}

// Header dropdown: every printer, then "Manage printers..." which opens the admin page
function paintPrinterSelect() {
  const wrap = $('printer-pick'), select = $('printer-select');
  wrap.hidden = store.list.length === 0;
  if (wrap.hidden) return;
  const managing = location.hash.slice(1) === 'printers';
  fill(select,
    store.list.map((p) => h('option', { value: p.id, selected: !managing && p.id === store.currentId }, p.name)),
    h('hr'),
    h('option', { value: MANAGE, selected: managing }, 'Manage printers\u2026'));
}

function show(view) {
  const changed = shown !== view;
  shown = view;
  if (changed) window.scrollTo(0, 0);
  $('view-setup').hidden = view !== 'setup';
  if (view !== 'setup') clear($('view-setup'));  // don't keep a finished wizard around
  $('view-printers').hidden = view !== 'printers';
  $('view-tools').hidden = !view.startsWith('view:');
  return changed;
}

async function render() {
  const route = location.hash.slice(1);
  const has = store.list.length > 0;
  paintPrinterSelect();

  if (!has) {  // nothing to show without a printer: walk the user through adding one
    if (show('setup')) {
      wizard(clear($('view-setup')), { mode: 'first', onDone: async () => { await loadPrinters(); location.hash = ''; render(); } });
    }
    return;
  }
  if (route === 'printers') {
    if (show('printers')) {
      admin(clear($('view-printers')), { onChanged: async () => { await loadPrinters(); render(); } });
    }
    paintTabs();
    return;
  }
  const v = activeView();
  if (!v) {
    $('tools').replaceChildren(errorBox({ message: 'No tools are installed.' }));
    show('view:none');
    return;
  }
  if (!mounted.has(v.key)) await mountView(v);
  show(`view:${v.key}`);
  for (const [key, { root }] of mounted) root.hidden = key !== v.key;
  paintTabs();
  window.dispatchEvent(new Event('pp:tool-shown'));
}

// If the server is restarted with a new version while this page is open, say so instead of showing a page
// whose styles and scripts no longer match it.
let build = null;
async function checkBuild() {
  const res = await api('/health');
  if (res.ok && build && res.data?.build && res.data.build !== build) $('update-banner').hidden = false;
}

async function init() {
  const [toolsRes] = await Promise.all([api('/tools'), loadPrinters()]);
  if (!toolsRes.ok || !toolsRes.data?.tools) {
    $('view-tools').append(errorBox(toolsRes.error, () => location.reload()));
    return;
  }
  tools = toolsRes.data.tools;
  build = toolsRes.data.build;
  $('update-reload').addEventListener('click', () => location.reload());
  buildViews();
  $('version').textContent = `panda-print ${toolsRes.data.version}`;
  $('printer-pick').prepend(icon('printer', 18));
  $('printer-select').addEventListener('change', (e) => {
    if (e.target.value === MANAGE) { location.hash = 'printers'; return; }
    store.select(e.target.value);
    if (location.hash.slice(1) === 'printers') location.hash = '';
  });
  store.subscribe(() => { paintPrinterSelect(); loadLinks(); });
  window.addEventListener('hashchange', render);
  await render();
  loadLinks();
}

setInterval(() => { if (!document.hidden) { loadLinks(); checkBuild(); } }, 30_000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) { loadLinks(); checkBuild(); } });
init();
