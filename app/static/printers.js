// Printer management: the setup wizard (first run, add, edit) and the Printers admin page.
import { api } from './lib/api.js';
import { h, clear, fill } from './lib/dom.js';
import { icon } from './lib/icons.js';
import { store } from './lib/printer-store.js';
import * as ui from './lib/ui.js';
import { toast } from './lib/ui.js';

const spinner = () => h('span', { class: 'spin', role: 'img', 'aria-label': 'Checking' });

// One row of the connection check: state is 'run' | 'ok' | 'bad'
function checkRow(title, state, detail, hint) {
  const mark = state === 'run' ? spinner() : icon(state === 'ok' ? 'check' : 'alert', 18);
  return h('li', { class: `check ${state}` },
    h('span', { class: 'mark' }, mark),
    h('div', {}, h('strong', {}, title), detail && h('div', { class: 'sub' }, detail), hint && h('div', { class: 'sub' }, hint)));
}

// The three checks as list items, from a probe result (null while running)
export function probeRows(probe, running) {
  if (running) {
    return [checkRow('Printer', 'run', 'Looking for the printer…'),
      checkRow('Page counter', 'run', 'Reading the page counter…'),
      checkRow('Print queue', 'run', 'Asking CUPS…')];
  }
  if (!probe) return [];
  const { ipp, snmp, cups } = probe;
  const bad = (title, r) => checkRow(title, 'bad', r.error?.message, r.error?.hint);
  const queue = cups.ok && cups.suggested ? ` Queue: ${cups.suggested}.` : '';
  return [
    ipp.ok ? checkRow('Printer', 'ok', `${ipp.model || 'Printer'} · ${ipp.state}`) : bad('Printer', ipp),
    snmp.ok ? checkRow('Page counter', 'ok', `${snmp.value.toLocaleString()} pages printed so far`)
      : bad('Page counter', snmp),
    cups.ok ? checkRow('Print queue', 'ok', `CUPS is reachable.${queue}`) : bad('Print queue', cups),
  ];
}

// ---------------------------------------------------------------- wizard
// mode: 'first' (nothing set up yet) | 'add' | 'edit'
export function wizard(root, { mode, printer, onDone, onCancel }) {
  const d = store.defaults || {};
  const draft = mode === 'edit' ? { ...printer } : {
    name: '', ip: '', ipp_url: '', snmp_community: d.snmp_community || 'public', snmp_port: d.snmp_port || 161,
    counter_oid: d.counter_oid || '', cups_url: d.cups_url || 'http://127.0.0.1:631',
    cups_queue: d.cups_queue || '', host_state_file: d.host_state_file || '',
  };
  const s = { step: mode === 'edit' ? 'form' : 'find', scanning: false, found: null, notes: [], probe: null, probing: false, saving: false };
  let scanToken = 0, probeToken = 0;

  const title = mode === 'first' ? 'Welcome to panda-print' : mode === 'edit' ? `Edit ${printer.name}` : 'Add a printer';
  const lead = mode === 'first' ? 'Let’s connect your printer. It takes about a minute.'
    : mode === 'edit' ? 'Change how panda-print talks to this printer.' : 'Find a printer on your network or enter its address.';

  const frame = (...body) => h('section', { class: 'card wiz' },
    h('div', { class: 'wiz-head' },
      mode !== 'edit' && h('ol', { class: 'steps', 'aria-label': 'Progress' },
        h('li', { 'aria-current': s.step === 'find' ? 'step' : null }, h('b', {}, '1'), 'Find your printer'),
        h('li', { 'aria-current': s.step === 'form' ? 'step' : null }, h('b', {}, '2'), 'Connect and name')),
      h('h2', {}, title), h('p', { class: 'sub' }, lead)),
    h('div', { class: 'card-body' }, ...body));

  function paint() {
    fill(root, s.step === 'find' ? renderFind() : renderForm());
    root.querySelector('[autofocus], input')?.focus?.({ preventScroll: true });
  }

  // ---------------------------------------------------------- step 1: find
  const foundBox = h('div');

  async function scan() {
    const mine = ++scanToken;
    s.scanning = true; s.found = null; s.notes = [];
    paintFound();
    const res = await api('/printers/discover', { method: 'POST', body: {} });
    if (mine !== scanToken) return;
    s.scanning = false;
    s.found = res.ok ? res.data.candidates : [];
    s.notes = res.ok ? res.data.notes : [res.error.message];
    paintFound();  // only the results list, so anything typed in the address box is left alone
  }

  function pick(c) {
    Object.assign(draft, {
      ip: c.ip, ipp_url: c.ipp_url || '', name: c.model || '',  // blank: the connection check suggests the printer's own name
      cups_queue: c.queue || draft.cups_queue, cups_url: c.cups_url || draft.cups_url,
    });
    goForm();
  }

  function paintFound() {
    const list = s.scanning
      ? h('div', { class: 'scan' }, spinner(), h('span', {}, 'Looking for printers on your network and in CUPS\u2026'))
      : s.found?.length
        ? h('ul', { class: 'cands' }, s.found.map((c) => h('li', {},
            h('button', { class: 'cand', type: 'button', disabled: !!c.already_added, onclick: () => pick(c) },
              icon('printer', 22),
              h('span', { class: 'cand-text' }, h('strong', {}, c.model || c.name),
                h('span', { class: 'sub' }, [c.ip, c.queue && `CUPS queue ${c.queue}`, c.sources?.includes('mdns') && 'found on the network'].filter(Boolean).join(' \u00b7 '))),
              c.already_added ? ui.badge('Already added') : icon('chevron', 16)))))
        : h('p', { class: 'empty' }, 'No printers found automatically. Enter the address below.');
    fill(foundBox, h('h3', { class: 'sect' }, 'Printers we found'), list,
      s.notes.length > 0 && !s.scanning && h('ul', { class: 'notes' }, s.notes.map((n) => h('li', {}, n))),
      !s.scanning && h('button', { class: 'btn small', type: 'button', onclick: scan }, icon('refresh', 15), 'Search again'));
  }

  function renderFind() {
    const manual = h('input', { id: 'manual-ip', class: 'input', value: draft.ip, placeholder: '192.168.1.50 or printer.local',
      autocomplete: 'off', spellcheck: 'false', inputmode: 'url', oninput: (e) => { draft.ip = e.target.value; } });
    const err = h('p', { class: 'msg', role: 'alert' });
    const go = (e) => {
      e?.preventDefault();
      if (!manual.value.trim()) { err.textContent = 'Enter the printer\u2019s IP address or host name.'; manual.focus(); return; }
      draft.ip = manual.value.trim(); draft.name = ''; draft.ipp_url = ''; draft.cups_queue = d.cups_queue || '';
      goForm();
    };
    paintFound();
    return frame(
      foundBox,
      h('form', { class: 'manual', onsubmit: go },
        h('h3', { class: 'sect' }, 'Or enter the address yourself'),
        h('p', { class: 'sub' }, 'You can find it on the printer\u2019s screen (network settings) or on your router\u2019s device list.'),
        h('div', { class: 'row-inline' }, manual, h('button', { class: 'btn primary', type: 'submit' }, 'Continue')), err),
      mode === 'add' && h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', onclick: onCancel }, 'Cancel')));
  }

  // ---------------------------------------------------------- step 2: connect and name
  function goForm() { s.step = 'form'; s.probe = null; paint(); runProbe(); }

  const inputs = {}, errEls = {};

  function field(label, key, o = {}) {
    const input = h('input', { id: `f-${key}`, class: 'input', type: o.type || 'text', value: draft[key] ?? '',
      placeholder: o.placeholder || '', autocomplete: 'off', spellcheck: 'false',
      oninput: (e) => { draft[key] = e.target.value; showError(key, ''); } });
    inputs[key] = input;
    errEls[key] = h('p', { class: 'msg', role: 'alert' });
    return h('div', { class: 'field' }, h('label', { for: `f-${key}` }, label), input,
      o.hint && h('p', { class: 'hint' }, o.hint), errEls[key]);
  }

  function showError(key, message) {
    if (!errEls[key]) return;
    errEls[key].textContent = message;
    inputs[key]?.setAttribute('aria-invalid', message ? 'true' : 'false');
  }

  const checksBox = h('ul', { class: 'checks', 'aria-live': 'polite' });
  const queueBox = h('div', { class: 'field' });
  const actionsBox = h('div', { class: 'actions' });

  function renderForm() {
    const advanced = h('details', { class: 'more' }, h('summary', {}, icon('chevron', 16), 'Advanced settings'),
      h('div', { class: 'adv' },
        field('IPP address', 'ipp_url', { placeholder: 'ipp://192.168.1.50/ipp/print', hint: 'Leave blank to use the standard address.' }),
        field('SNMP community', 'snmp_community'),
        field('SNMP port', 'snmp_port', { type: 'number' }),
        field('Page counter OID', 'counter_oid'),
        field('CUPS server', 'cups_url', { hint: 'Where the print queues live.' }),
        field('Nozzle-check timer state file', 'host_state_file',
          { placeholder: '/host-state/state', hint: 'Optional. A file written by an existing nozzle-check timer on the host.' })));
    const form = h('div', { class: 'form' },
      field('Name', 'name', { placeholder: 'Study printer' }),
      field('Address', 'ip', { placeholder: '192.168.1.50', hint: 'IP address or host name of the printer.' }),
      h('div', {}, h('div', { class: 'row-inline between' }, h('h3', { class: 'sect' }, 'Connection check'),
        h('button', { class: 'btn small', type: 'button', onclick: runProbe }, icon('refresh', 15), 'Check again')), checksBox),
      queueBox, advanced, actionsBox);
    paintChecks();
    return frame(form);
  }

  async function runProbe() {
    const mine = ++probeToken;
    s.probing = true;
    paintChecks();
    const res = await api('/printers/probe', { method: 'POST', body: { ...draft } });
    if (mine !== probeToken) return;
    s.probing = false;
    if (res.ok && res.data.errors) {  // the address itself is malformed
      s.probe = null;
      for (const [k, m] of Object.entries(res.data.errors)) showError(k, m);
    } else if (res.ok) {
      s.probe = res.data;
      const p = res.data;
      if (!draft.name && p.suggested_name) { draft.name = p.suggested_name; inputs.name.value = draft.name; }
      if (!draft.cups_queue && p.cups.suggested) draft.cups_queue = p.cups.suggested;
      if (!draft.ipp_url && p.ipp_url && p.ipp_url !== `ipp://${draft.ip}/ipp/print`) {
        draft.ipp_url = p.ipp_url; inputs.ipp_url.value = p.ipp_url;
      }
    } else {
      s.probe = null;
      toast(res.error.message, 'bad');
    }
    paintChecks();
  }

  function paintChecks() {
    clear(checksBox).append(...probeRows(s.probe, s.probing));

    // queue picker: a list when CUPS answered, otherwise a plain text box
    clear(queueBox);
    const queues = s.probe?.cups.queues || [];
    queueBox.append(h('label', { for: 'f-cups_queue' }, 'Print queue (CUPS)'));
    if (queues.length) {
      const select = h('select', { id: 'f-cups_queue', class: 'input', onchange: (e) => { draft.cups_queue = e.target.value; showError('cups_queue', ''); } },
        h('option', { value: '' }, 'None (status only, no jobs or printing)'),
        queues.map((q) => h('option', { value: q.name, selected: q.name === draft.cups_queue }, q.info && q.info !== q.name ? `${q.name} – ${q.info}` : q.name)));
      inputs.cups_queue = select; queueBox.append(select);
    } else {
      const input = h('input', { id: 'f-cups_queue', class: 'input', value: draft.cups_queue, placeholder: 'T730', autocomplete: 'off',
        spellcheck: 'false', oninput: (e) => { draft.cups_queue = e.target.value; showError('cups_queue', ''); } });
      inputs.cups_queue = input; queueBox.append(input, h('p', { class: 'hint' }, 'Optional. Needed for recent jobs and printing.'));
    }
    errEls.cups_queue = h('p', { class: 'msg', role: 'alert' });
    queueBox.append(errEls.cups_queue);

    const allOk = s.probe && s.probe.ipp.ok && s.probe.snmp.ok && s.probe.cups.ok;
    fill(actionsBox,
      mode !== 'edit'
        ? h('button', { class: 'btn', type: 'button', onclick: () => { probeToken++; s.step = 'find'; paint(); if (!s.found && !s.scanning) scan(); } }, 'Back')
        : null,
      mode !== 'first' && h('button', { class: 'btn', type: 'button', onclick: onCancel }, 'Cancel'),
      h('button', { class: `btn primary${allOk ? '' : ' warn'}`, type: 'button', disabled: s.saving || s.probing, onclick: save },
        s.saving ? 'Saving…' : mode === 'edit' ? 'Save changes' : allOk ? 'Add printer' : 'Add anyway'));
    if (!allOk && s.probe && !s.probing) {
      actionsBox.prepend(h('p', { class: 'sub grow' }, 'Some checks failed. You can still save and fix the details later.'));
    }
  }

  async function save() {
    s.saving = true; paintChecks();
    const res = mode === 'edit'
      ? await api(`/printers/${printer.id}`, { method: 'PUT', body: { ...draft } })
      : await api('/printers', { method: 'POST', body: { ...draft } });
    s.saving = false;
    if (!res.ok) {
      const fields = res.error?.fields;
      if (fields) {
        for (const [k, m] of Object.entries(fields)) showError(k, m);
        const first = Object.keys(fields)[0];
        if (['ipp_url', 'snmp_community', 'snmp_port', 'counter_oid', 'cups_url', 'host_state_file'].includes(first)) {
          root.querySelector('details.more')?.setAttribute('open', '');
        }
        inputs[first]?.focus();
      } else toast(res.error?.message || 'Could not save.', 'bad');
      paintChecks();
      return;
    }
    toast(mode === 'edit' ? 'Printer updated.' : `${res.data.name} added.`);
    onDone?.(res.data);
  }

  paint();
  if (s.step === 'find') scan(); else runProbe();
}

// ---------------------------------------------------------------- admin page
export function admin(root, { onChanged }) {
  const results = new Map();  // printer id -> probe result | 'running'
  let inList = true;  // a background test finishing must not repaint over the wizard

  function list() {
    inList = true;
    const cards = store.list.map((p) => printerCard(p));
    clear(root).append(
      h('div', { class: 'tool-head' }, h('h2', {}, 'Printers'),
        h('p', {}, 'The printers this dashboard keeps an eye on.'),
        h('button', { class: 'btn primary push', type: 'button', onclick: add }, icon('printer', 16), 'Add printer')),
      h('div', { class: 'grid' }, cards));
  }

  function summary(p) {
    const r = results.get(p.id);
    if (!r || r === 'running') return { badge: ui.badge('Checking…'), rows: r ? probeRows(null, true) : [] };
    if (!r.ipp) return { badge: ui.badge('Unknown', 'warn'), rows: [] };
    const ok = [r.ipp, r.snmp, r.cups].filter((c) => c.ok).length;
    const badge = !r.ipp.ok ? ui.badge('Can’t reach', 'bad') : ok === 3 ? ui.badge('Online', 'ok') : ui.badge('Partly working', 'warn');
    return { badge, rows: probeRows(r, false) };
  }

  function printerCard(p) {
    const { badge, rows } = summary(p);
    const model = results.get(p.id)?.ipp?.model;
    return h('section', { class: 'card', 'aria-label': p.name },
      h('div', { class: 'card-head' }, h('h3', {}, icon('printer', 18), p.name), badge),
      h('div', { class: 'card-body' },
        h('dl', { class: 'kv' },
          h('dt', {}, 'Address'), h('dd', {}, p.ip),
          model && h('dt', {}, 'Model'), model && h('dd', {}, model),
          h('dt', {}, 'Print queue'), h('dd', {}, p.cups_queue || 'None'),
          p.host_state_file && h('dt', {}, 'Timer state'), p.host_state_file && h('dd', {}, p.host_state_file)),
        rows.length > 0 && h('ul', { class: 'checks compact' }, rows),
        h('div', { class: 'actions left' },
          h('button', { class: 'btn small', type: 'button', onclick: () => edit(p) }, 'Edit'),
          h('button', { class: 'btn small', type: 'button', onclick: () => test(p) }, icon('refresh', 15), 'Test'),
          h('button', { class: 'btn small danger', type: 'button', onclick: () => remove(p) }, 'Remove'))));
  }

  async function test(p) {
    results.set(p.id, 'running');
    if (inList) list();
    const res = await api(`/printers/${p.id}/test`, { method: 'POST' });
    results.set(p.id, res.ok ? res.data : { error: res.error });
    if (root.isConnected && inList) list();
  }

  const done = async () => { await onChanged(); if (root.isConnected && !root.hidden) list(); };
  const add = () => { inList = false; wizard(clear(root), { mode: 'add', onCancel: list, onDone: done }); };
  const edit = (p) => { inList = false; wizard(clear(root), { mode: 'edit', printer: p, onCancel: list, onDone: async () => { results.delete(p.id); await done(); } }); };

  function remove(p) {
    const dialog = h('dialog', { 'aria-labelledby': 'rm-title' },
      h('h3', { id: 'rm-title' }, `Remove ${p.name}?`),
      h('p', {}, 'panda-print will stop watching this printer and delete its nozzle-check history. The printer and its CUPS queue are not touched.'),
      h('div', { class: 'actions' },
        h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Keep it'),
        h('button', { class: 'btn primary danger-fill', type: 'button', onclick: async () => {
          dialog.close();
          const res = await api(`/printers/${p.id}`, { method: 'DELETE' });
          if (!res.ok) { toast(res.error.message, 'bad'); return; }
          toast(`${p.name} removed.`);
          dialog.remove();
          await done();
        } }, 'Remove')));
    dialog.addEventListener('close', () => dialog.remove());
    document.body.append(dialog);
    dialog.showModal();
  }

  list();
  store.list.forEach(test);
}
