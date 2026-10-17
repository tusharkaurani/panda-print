// The shell: theme, launcher tiles, and a registry-driven set of tools.
import { api } from './lib/api.js';
import { h, clear } from './lib/dom.js';
import { icon } from './lib/icons.js';
import { makeKit } from './lib/kit.js';
import { errorBox } from './lib/ui.js';
import { store } from './lib/printer-store.js';
import { admin, wizard } from './printers.js';

const $ = (id) => document.getElementById(id);

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
  const attrs = { class: 'tile', href: link.url };
  if (link.new_tab) { attrs.target = '_blank'; attrs.rel = 'noopener noreferrer'; }
  return h('li', {}, h('a', attrs, plate,
    h('span', { class: 'tile-text' }, h('div', { class: 'tile-name' }, link.name),
      link.description && h('div', { class: 'tile-desc' }, link.description)),
    link.new_tab && h('span', { class: 'ext', 'aria-label': 'opens in a new tab' }, icon('external', 15))));
}

let lastLinks = '';
async function loadLinks() {
  const res = await api('/links');
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
  clear($('links-grid')).append(...cfg.links.map(tile));
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
const mounted = new Map();  // tool id -> {root}
let tools = [];
let shown = null;  // which view is on screen: 'setup' | 'printers' | 'tool:<id>'

const visibleTools = () => tools.filter((t) => !t.needs_printer || store.list.length > 0);

function activeTool() {
  const route = location.hash.slice(1);
  const list = visibleTools();
  return list.find((t) => t.id === route) || list[0] || null;
}

async function mountTool(t) {
  const root = h('section', { class: 'tool', id: `tool-${t.id}`, hidden: true },
    h('div', { class: 'tool-head' }, h('h2', {}, t.title), t.description && h('p', {}, t.description)));
  $('tools').append(root);
  mounted.set(t.id, { root });
  const content = h('div', { class: 'tool-body' });
  root.append(content);
  try {
    if (t.error) throw new Error(t.error);
    if (t.css) document.head.append(h('link', { rel: 'stylesheet', href: t.css }));
    const mod = await import(t.entry);
    await mod.mount(content, makeKit(root, t.id));
  } catch (e) {
    console.error(`tool ${t.id} failed`, e);
    content.append(errorBox({ message: `The "${t.title}" tool could not start.`, hint: String(e.message || e) }));
  }
}

function paintTabs() {
  const list = visibleTools();
  const tabs = $('tool-tabs');
  tabs.hidden = list.length < 2 || shown === 'setup' || shown === 'printers';
  const sig = list.map((t) => t.id).join();
  if (tabs.dataset.sig !== sig) {
    tabs.dataset.sig = sig;
    clear(tabs).append(...list.map((t) => h('a', { class: 'tab', href: `#${t.id}`, 'data-tool': t.id }, icon(t.icon || 'wrench', 16), t.title)));
  }
  const current = activeTool();
  tabs.querySelectorAll('a').forEach((a) => {
    if (current && a.dataset.tool === current.id) a.setAttribute('aria-current', 'page');
    else a.removeAttribute('aria-current');
  });
}

function paintPrinterBar() {
  const bar = $('printer-bar');
  const t = activeTool();
  bar.hidden = !(t?.needs_printer && store.list.length > 1 && shown?.startsWith('tool:'));
  if (bar.hidden) return;
  const select = h('select', { id: 'printer-select', class: 'input', onchange: (e) => store.select(e.target.value) },
    store.list.map((p) => h('option', { value: p.id, selected: p.id === store.currentId }, p.name)));
  clear(bar).append(icon('printer', 18), h('label', { for: 'printer-select' }, 'Printer'), select);
}

function show(view) {
  const changed = shown !== view;
  shown = view;
  if (changed) window.scrollTo(0, 0);
  $('view-setup').hidden = view !== 'setup';
  if (view !== 'setup') clear($('view-setup'));  // don't keep a finished wizard around
  $('view-printers').hidden = view !== 'printers';
  $('view-tools').hidden = !view.startsWith('tool:');
  $('launcher').classList.toggle('compact', view === 'setup');
  return changed;
}

async function render() {
  const route = location.hash.slice(1);
  const has = store.list.length > 0;
  const link = $('printers-link');
  link.hidden = !has;
  link.toggleAttribute('aria-current', has && route === 'printers');

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
  const t = activeTool();
  if (!t) {
    $('tools').replaceChildren(errorBox({ message: 'No tools are installed.' }));
    show('tool:none');
    return;
  }
  if (!mounted.has(t.id)) await mountTool(t);
  show(`tool:${t.id}`);
  for (const [id, { root }] of mounted) root.hidden = id !== t.id;
  paintTabs();
  paintPrinterBar();
  window.dispatchEvent(new Event('pp:tool-shown'));
}

async function init() {
  const [toolsRes] = await Promise.all([api('/tools'), loadPrinters()]);
  if (!toolsRes.ok || !toolsRes.data?.tools) {
    $('view-tools').append(errorBox(toolsRes.error, () => location.reload()));
    return;
  }
  tools = toolsRes.data.tools;
  $('mock-pill').hidden = !toolsRes.data.mock;
  $('version').textContent = `panda-print ${toolsRes.data.version}`;
  $('printers-link').append(icon('printer', 18), h('span', {}, 'Printers'));
  store.subscribe(() => { paintPrinterBar(); });
  window.addEventListener('hashchange', render);
  await render();
}

loadLinks();
setInterval(() => { if (!document.hidden) loadLinks(); }, 60_000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadLinks(); });
init();
