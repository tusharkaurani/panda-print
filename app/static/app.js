// The shell: theme, launcher tiles, and a registry-driven set of tools.
import { api } from './lib/api.js';
import { h, clear } from './lib/dom.js';
import { icon } from './lib/icons.js';
import { makeKit } from './lib/kit.js';
import { errorBox } from './lib/ui.js';

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

// ---------------------------------------------------------------- tools
const mounted = new Map();  // id -> {root}
let tools = [];

function pickTool() {
  const wanted = location.hash.slice(1);
  return tools.find((t) => t.id === wanted) || tools[0];
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
    await mod.mount(content, makeKit(root));
  } catch (e) {
    console.error(`tool ${t.id} failed`, e);
    content.append(errorBox({ message: `The "${t.title}" tool could not start.`, hint: String(e.message || e) }));
  }
}

function showTool() {
  const current = pickTool();
  for (const [id, { root }] of mounted) root.hidden = id !== current.id;
  document.querySelectorAll('#tool-tabs a').forEach((a) => {
    a.toggleAttribute('aria-current', a.dataset.tool === current.id);
    if (a.dataset.tool === current.id) a.setAttribute('aria-current', 'page');
  });
  window.dispatchEvent(new Event('pp:tool-shown'));
}

async function init() {
  const res = await api('/tools');
  if (!res.ok || !res.data?.tools) {
    $('tools').append(errorBox(res.error, () => location.reload()));
    return;
  }
  tools = res.data.tools;
  $('mock-pill').hidden = !res.data.mock;
  $('version').textContent = `panda-print ${res.data.version}`;

  if (tools.length > 1) {
    const tabs = $('tool-tabs');
    tabs.hidden = false;
    tabs.append(...tools.map((t) => h('a', { class: 'tab', href: `#${t.id}`, 'data-tool': t.id }, icon(t.icon || 'wrench', 16), t.title)));
  }
  await Promise.all(tools.map(mountTool));
  showTool();
  window.addEventListener('hashchange', showTool);
}

loadLinks();
setInterval(() => { if (!document.hidden) loadLinks(); }, 60_000);
document.addEventListener('visibilitychange', () => { if (!document.hidden) loadLinks(); });
init();
