// Runs one tab of one tool's UI against recorded answers of the real API, with a fake DOM, and checks what comes out.
//   node smoke.mjs <bundle dir> <fixtures.json> <tool> <tab or -> [scenario]
// It fails (exit 1) on an exception, a console error, an unhandled rejection, a page that prints false/null/undefined as
// text, or a dropdown with no options.
import fs from 'node:fs';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import './fakedom.mjs';
import { FakeEl, FakeEvent } from './fakedom.mjs';

const [dir, fixturesPath, tool, tabArg, scenario] = process.argv.slice(2);
const tab = tabArg === '-' ? null : tabArg;
const fx = JSON.parse(fs.readFileSync(fixturesPath, 'utf8'));
const lib = (f) => import(pathToFileURL(path.join(dir, 'static/lib', f)).href);
const problems = [];
const errors = [];
console.error = (...a) => errors.push(a.map(String).join(' '));
process.on('unhandledRejection', (e) => errors.push(`unhandled rejection: ${e?.stack || e}`));

const { h, svg, clear } = await lib('dom.js');
const { icon } = await lib('icons.js');
const fmt = await lib('fmt.js');
const ui = await lib('ui.js');

const PRINTER = { id: 'brother-dcp-t730dw', name: 'Test printer' };
const calls = [];
const toasts = [];
const subs = [];
const pending = [];

async function api(p, opts = {}) {
  const method = opts.method || 'GET';
  calls.push(`${method} ${p}`);
  const clean = p.split('?')[0].replace(`/maintenance/${PRINTER.id}/`, '/maintenance/PID/');
  let key = `${method} ${clean}`;
  let hit = fx[key];
  if (key === 'POST /photosheet/layout') {  // the answer depends on how many photos were sent
    const n = Math.max(1, opts.body?.images?.length || 1);
    hit = fx[`${key}#${n}`] || fx[`${key}#1`];
  }
  if (key === 'POST /labels/layout') hit = fx[`${key}#${opts.body?.content?.mode === 'csv' ? 'csv' : 'text'}`];
  if (key === 'GET /labels/config') hit = fx[`${key}?printer=${p.includes('printer=') ? 'yes' : 'no'}`];
  if (key === 'GET /photosheet/config') hit = fx[`${key}?printer=${p.includes('printer=') ? 'yes' : 'no'}`];
  if (hit === undefined) {
    if (/\/pdf/.test(p)) return { ok: true, status: 200, data: new Blob(['%PDF-1.4']), error: null, retryAfter: 0 };  // a generated file
    if (method !== 'GET') return { ok: true, status: 200, data: {}, error: null, retryAfter: 0 };  // an unrecorded change succeeds
    return { ok: false, status: 404, data: null, error: { message: `no recorded answer for ${key}` }, retryAfter: 0 };
  }
  return { ok: true, status: 200, data: hit, error: null, retryAfter: 0 };
}

const kit = {
  toolId: tool, tabId: tab, api, h, svg, clear, icon, fmt, ui,
  toast: (m, k) => toasts.push(`${k || ''}:${m}`),
  printer: { get current() { return PRINTER; }, subscribe: (fn) => subs.push(fn) },
  poll: (fn) => { pending.push(Promise.resolve().then(fn)); },
};

const root = new FakeEl('div');
document.body.append(root);
const view = new FakeEl('section');  // the shell's view element, which the photo tool watches
view.append(root);
document.body.append(view);

// long enough for the page's own 120 ms wait before it asks for a new layout, then for the answers to be handled
const settle = async () => { await new Promise((r) => setTimeout(r, 140)); await Promise.allSettled(pending); await new Promise((r) => setTimeout(r, 20)); };
const $$ = (sel) => document.body.querySelectorAll(sel);
const $ = (sel) => document.body.querySelector(sel);
const change = (el, value) => { if (value !== undefined) el.value = value; el.dispatchEvent(new FakeEvent('change', { bubbles: true })); };
const typeInto = (el, value) => { el.value = value; el.dispatchEvent(new FakeEvent('input', { bubbles: true })); };
const fakeFile = (name) => new File(['x'], name, { type: 'image/png' });

// ---------------------------------------------------------------- mount
const entry = await import(pathToFileURL(path.join(dir, 'tools', tool, 'ui.js')).href);
try { await entry.mount(root, kit); } catch (e) { problems.push(`mount threw: ${e?.stack || e}`); }
await settle();

// ---------------------------------------------------------------- scenarios: what a person does next
async function photoScenario() {
  const input = $('.ps input[type=file]');
  input.files = [fakeFile('a.png'), fakeFile('b.png'), fakeFile('c.png')];
  input.dispatchEvent(new FakeEvent('change'));
  await settle();
  const rows = $$('.photo-row').length;
  if (rows !== 3) problems.push(`expected 3 photo rows after adding 3 photos, got ${rows}`);
  if (!$('.splitbar')) problems.push('no split bar with three photos');
  const handles = $$('.splitbar .handle');
  if (handles.length !== 2) problems.push(`expected 2 handles, got ${handles.length}`);
  const buttons = $$('.stepper .icon-mini');
  if (buttons.length !== 6) problems.push(`expected 6 stepper buttons, got ${buttons.length}`);
  buttons[1].click(); await settle();          // one more of the first photo
  const first = $$('.stepper input')[0];
  change(first, '12'); await settle();          // type a number
  handles[0].dispatchEvent(new FakeEvent('keydown', { key: 'ArrowRight', bubbles: true })); await settle();
  const adjust = $$('.adjust')[0];
  adjust.open = true; adjust.dispatchEvent(new FakeEvent('toggle')); await settle();
  const fitSel = $('.adjust select'); change(fitSel, 'fit'); await settle();
  const useAll = $$('.adjust-body .btn').find((b) => /Use for all/.test(b.textContent)); if (useAll) useAll.click(); await settle();
  for (const area of ['top', 'bottom', 'full']) { change(document.getElementById('ps-area'), area); await settle(); }
  const unit = $('.unit-pick'); change(unit, 'in'); await settle(); change(unit, 'cm'); await settle(); change(unit, 'mm'); await settle();
  for (const mode of ['grid', 'count', 'fill']) { change(document.getElementById('ps-mode'), mode); await settle(); }
  const chips = $$('.chip'); if (!chips.length) problems.push('no preset chips');
  else {
    typeInto(document.getElementById('ps-photoW'), '50'); typeInto(document.getElementById('ps-photoH'), '70'); await settle();
    chips[0].click(); await settle();
    const w = document.getElementById('ps-photoW').value, hh = document.getElementById('ps-photoH').value;
    if (w !== '35' || hh !== '45') problems.push(`a preset did not set the photo size: ${w} x ${hh}`);
    if (document.getElementById('ps-preset').value !== '0') problems.push(`the Size list does not show the 35 x 45 size after a preset: "${document.getElementById('ps-preset').value}"`);
    typeInto(document.getElementById('ps-photoW'), '41'); await settle();
    if (document.getElementById('ps-preset').value !== '') problems.push('the Size list should go back to its prompt when the size matches none');
    chips[chips.length - 1].click(); await settle();
  }
  const save = $$('button').find((b) => /Save these settings/.test(b.textContent)); save.click(); await settle();
  const name = document.getElementById('ps-preset-name'); typeInto(name, 'Mine'); await settle();
  const go = $$('dialog button').find((b) => b.textContent.trim() === 'Save preset'); go.click(); await settle();
  const remove = $$('.icon-mini').find((b) => /Remove/.test(b.getAttribute('aria-label') || '')); remove.click(); await settle();
  for (const s of subs) s(PRINTER);  // the printer selection changed
  await settle();
}

async function labelScenario() {
  const el = (id) => document.getElementById(id);
  const sheets = $$('#lb-template option');
  if (sheets.length < 10) problems.push(`the sheet list has only ${sheets.length} choices`);
  change(el('lb-template'), sheets[3].value); await settle();
  if (!el('lb-cols').value || !el('lb-width').value) problems.push('the measurements are empty after choosing a sheet');
  typeInto(el('lb-cols'), '2'); await settle();
  if (el('lb-template').value !== '') problems.push('editing the measurements should switch to the custom grid');
  change(el('lb-template'), sheets[0].value); await settle();
  typeInto(el('lb-text'), 'One\nTwo\n\nThree'); await settle();
  change(el('lb-perLine'), 'true'); await settle(); change(el('lb-perLine'), 'false'); await settle();
  change(el('lb-mode'), 'csv'); await settle();
  typeInto(el('lb-csv'), 'Name,City\nAda,London'); typeInto(el('lb-csvtemplate'), '{Name}'); await settle();
  const chip = $$('.lb .chips .chip')[0]; if (!chip) problems.push('no column chips after reading a spreadsheet'); else { chip.click(); await settle(); }
  change(el('lb-mode'), 'text'); await settle();
  change(el('lb-align'), 'center'); change(el('lb-valign'), 'top'); await settle();
  el('lb-auto').checked = true; change(el('lb-auto')); await settle(); el('lb-auto').checked = false; change(el('lb-auto')); await settle();
  for (const id of ['lb-bold', 'lb-firstBold', 'lb-fill', 'lb-outline']) { el(id).checked = true; change(el(id)); await settle(); }
  typeInto(el('lb-copies'), '3'); typeInto(el('lb-start'), '4'); typeInto(el('lb-pad'), '2'); await settle();
  change(el('lb-unit'), 'in'); await settle(); change(el('lb-unit'), 'mm'); await settle();
  $('.sheet-canvas').dispatchEvent(new FakeEvent('click', { clientX: 10, clientY: 10, bubbles: true })); await settle();
  const pr = $$('.lb button').find((b) => /^Print…$/.test(b.textContent.trim()) || /Print…/.test(b.textContent)); if (pr && !pr.disabled) { pr.click(); await settle(); }
  const dl = $$('.lb button').find((b) => /Download PDF/.test(b.textContent)); if (dl && !dl.disabled) { dl.click(); await settle(); }
  for (const s of subs) s(PRINTER);
  await settle();
}

async function overviewScenario() { for (const s of subs) s(PRINTER); await settle(); }
async function nozzleScenario() {
  for (const s of subs) s(PRINTER);
  await settle();
  const manage = $$('button').find((b) => /Manage pages/.test(b.textContent)); if (manage) { manage.click(); await settle(); }
  const run = $$('button').find((b) => b.textContent.trim() === 'Print now'); if (run && !run.disabled) { run.click(); await settle(); }
}

try {
  if (scenario === 'photos') await photoScenario();
  else if (scenario === 'labels') await labelScenario();
  else if (tool === 'maintenance' && tab === 'overview') await overviewScenario();
  else if (tool === 'maintenance' && tab === 'nozzle') await nozzleScenario();
} catch (e) { problems.push(`scenario threw: ${e?.stack || e}`); }
await settle();

// ---------------------------------------------------------------- checks on what is on the page
const BAD_TEXT = /^(false|null|undefined|NaN)$|\[object Object\]|undefined/;
(function walk(el) {
  for (const n of el.childNodes) {
    if (n instanceof FakeEl) walk(n);
    else if (BAD_TEXT.test(n.data.trim())) problems.push(`stray text "${n.data.trim()}" inside <${el.localName} class="${el.className}">`);
  }
})(document.body);
for (const sel of $$('select')) if (!sel.options.length) problems.push(`an empty dropdown: #${sel.id || '(no id)'} ${sel.getAttribute('aria-label') || ''}`);
for (const e of errors) problems.push(`console error: ${e}`);
for (const t of toasts.filter((t) => t.startsWith('bad:'))) problems.push(`error toast: ${t}`);

console.log(JSON.stringify({ problems, calls: calls.length, rows: $$('.photo-row').length, chips: $$('.chip').length, text: root.textContent.slice(0, 6000) }));
process.exit(problems.length ? 1 : 0);
