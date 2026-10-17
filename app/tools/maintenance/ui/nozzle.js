// Nozzle Care tab: when the printer last printed, the history of checks, and the Automation settings that
// drive how checks are tracked. This is the file to extend with more nozzle-check features.
import { createPagesDialog } from './pages.js';
import { drawSheets } from './preview.js';
import { cardLoader, cupsPanel } from './shared.js';

// who sent a nozzle check: the Run now button, this app's schedule, or an older timer seen in the CUPS history
const SOURCE = { manual: 'Printed by you', auto: 'Printed automatically', host: 'Printed by another timer' };
const BY = { manual: 'by you', auto: 'by panda-print', host: 'by another timer' };
const KIND = { external: 'A document', direct: 'Sent straight to the printer', nozzle: 'A test page', baseline: 'Earliest we know of' };

export function mount(root, kit) {
  const { h, icon, fmt, ui, api, toast } = kit;
  const { base, load } = cardLoader(kit);

  // ------------------------------------------------------------ Print now (top right of the card)
  // The button and its dialog are created once; the card bodies are rebuilt on every refresh.
  let waitUntil = 0;  // the manual-print rate limit: when the next one is allowed
  let cupsOff = false;  // this printer has no CUPS queue, so nothing here can print
  let rotation = null;  // this printer's pages and what prints next (Service.rotation_status)
  const runLabel = h('span', {}, 'Print now');
  const runBtn = h('button', { class: 'btn small primary', type: 'button', title: 'Show the page that will print, then confirm', onclick: () => openRun() }, runLabel);

  const nozzleCard = ui.card({ title: 'Nozzle care', iconName: 'droplet', wide: true, onRefresh: () => refresh(), actions: [runBtn], quietStamp: true });
  const autoCard = ui.card({ title: 'Automation', iconName: 'clock', wide: true, onRefresh: () => refreshAuto() });
  root.append(h('div', { class: 'grid' }, nozzleCard.el, autoCard.el));
  const refresh = () => load(nozzleCard, 'nozzle', renderNozzle);
  async function refreshAuto() {
    const rot = await api(`${base()}/rotation`);
    rotation = rot.ok ? rot.data : null;
    return load(autoCard, 'automation', renderAutomation);
  }
  const pagesDialog = createPagesDialog(kit, { base, onChange: () => { refreshAuto(); refresh(); } });
  root.append(pagesDialog.dialog);

  function paintRun() {
    const left = Math.ceil((waitUntil - Date.now()) / 1000);
    runBtn.disabled = left > 0 || cupsOff;
    runLabel.textContent = left > 0 && !cupsOff ? `Print again in ${fmt.countdown(left)}` : 'Print now';
    runBtn.title = cupsOff ? 'Needs CUPS. The Automation card below says how to set it up.' : left > 0 ? 'Printing is limited so a page can’t be sent twice by accident. Try again shortly.'
      : 'Shows the page that will print, then asks you to confirm. The idle clock starts over.';
  }
  setInterval(() => { if (waitUntil > Date.now()) paintRun(); }, 1000);

  // what the next print will be: the next page in the rotation, or the standard page
  const STANDARD = { id: 'standard', standard: true, name: 'Standard test page', mime: 'application/postscript', pages: 1, thumb: false };
  const nextPage = () => (rotation?.rotate && rotation.items.find((i) => i.id === rotation.next.id)) || STANDARD;

  const runSheets = h('div', { class: 'sheets run-sheets' });
  const runName = h('p', { class: 'sub' });
  let runToken = 0;  // a newer open replaces a drawing that is still going
  const dialog = h('dialog', { class: 'wide run', 'aria-labelledby': 'mt-run-title' },
    h('h3', { id: 'mt-run-title' }, 'Print this page now?'),
    runName, runSheets,
    h('p', { class: 'hint' }, 'It uses a sheet of paper and a little ink.'),
    h('div', { class: 'actions' },
      h('button', { class: 'btn', type: 'button', title: 'Close without printing', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', title: 'Send this page to the printer now', onclick: runNow }, 'Print')));
  dialog.addEventListener('close', () => { if (dialog.open) return; runToken += 1; runSheets.replaceChildren(); });
  root.append(dialog);

  async function openRun() {
    const rot = await api(`${base()}/rotation`);  // the next page may have moved since the card was drawn
    if (rot.ok) rotation = rot.data;
    const item = nextPage();
    runToken += 1;
    const mine = runToken;
    runName.textContent = item.standard
      ? `${rotation?.rotate ? 'Next in the rotation: the' : 'The'} standard test page, four colour bars (cyan, magenta, yellow, black).`
      : `Next in the rotation: “${item.name}” · ${item.pages} page${item.pages === 1 ? '' : 's'}`;
    dialog.showModal();
    drawSheets(kit, item, runSheets, { alive: () => mine === runToken, width: Math.min(250, dialog.clientWidth - 80), maxPages: 3 });
  }

  async function runNow() {
    dialog.close();
    runBtn.disabled = true;
    const res = await api(`${base()}/nozzle/print`, { method: 'POST' });
    if (res.ok) {
      toast(`Sent to the printer (job #${res.data.job_id}).`);
    } else {
      toast(res.error?.message || 'Could not print.', 'bad');
      if (res.status === 429) waitUntil = Date.now() + (res.retryAfter || 60) * 1000;
    }
    paintRun();
    refresh();
    refreshAuto();  // "Next:" has moved on
    // the page counter moves a few seconds after the job is accepted
    [4000, 15000, 40000].forEach((ms) => setTimeout(refresh, ms));
  }

  // ------------------------------------------------------------ nozzle check
  function checkRow(c) {
    const state = c.state === 'failed' ? ['Failed', 'bad'] : c.state === 'submitted' ? ['Sent', 'info'] : ['Printed', 'ok'];
    return h('li', { class: 'row' },
      h('span', { class: 'ic' }, icon('droplet', 18)),
      h('div', { class: 'main' }, h('div', { class: 'title' }, SOURCE[c.source] || c.source),
        h('div', { class: 'meta' }, [c.page_name && `“${c.page_name}”${c.pages > 1 ? ` (${c.pages} pages)` : ''}`, fmt.abs(c.ts), c.job_id && `job #${c.job_id}`].filter(Boolean).join(' · ')),
        c.message && h('div', { class: 'msg' }, c.message)),
      h('div', { class: 'side' }, ui.badge(...state)));
  }

  function renderNozzle(d) {
    const lp = d.last_print, nx = d.next_check, a = d.automation;
    cupsOff = d.cups?.configured === false;
    // the next print is summarised by this status badge, which sits above the figures (not in the crowded header)
    const status = (cupsOff ? ui.badge('Printing needs CUPS')
      : !a.enabled
      ? (nx?.overdue ? ui.badge('Print due · auto print off', 'warn') : ui.badge('Auto print off'))
      : a.state === 'blocked' ? ui.badge('Auto print waiting', 'warn')
      : a.next_run_at ? ui.badge(a.next_run_at <= Date.now() / 1000 + 60 ? 'Printing soon' : `Next print ${a.next_run_label || fmt.when(a.next_run_at)}`, 'ok') : null);
    nozzleCard.setBadge(null);

    waitUntil = d.print.allowed ? 0 : Date.now() + d.print.retry_after * 1000;
    paintRun();

    const t = d.tracking;
    const problems = [t.counter_error && `Can’t read the page count: ${t.counter_error}`, t.cups_error && `Can’t reach CUPS: ${t.cups_error}`,
      a.enabled && a.state === 'blocked' && !cupsOff && `Auto print is waiting: ${a.reason}`].filter(Boolean);

    nozzleCard.set(
      status && h('div', { class: 'status-line' }, status),
      problems.length ? h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, problems.map((p) => h('div', {}, p)))) : null,
      h('div', { class: cupsOff ? 'stats two' : 'stats four' },
        ui.stat('Last print', lp ? `${lp.approximate ? 'At least ' : ''}${fmt.ago(lp.ts)}` : '–',
          lp && `${fmt.abs(lp.ts)} · ${KIND[lp.kind] || ''}`),
        ui.stat('Idle for', fmt.days(d.days_since_last_print),
          cupsOff ? 'Auto print needs CUPS' : a.enabled ? `Prints after ${d.idle_days_threshold} idle days` : 'Auto print is off'),
        !cupsOff && ui.stat('Last auto print', d.last_auto ? fmt.ago(d.last_auto.ts) : 'None yet',
          d.last_auto ? `${fmt.abs(d.last_auto.ts)} · ${BY[d.last_auto.source] || ''}` : 'Nothing yet'),
        !cupsOff && ui.stat('Last manual print', d.last_manual ? fmt.ago(d.last_manual.ts) : 'Never',
          d.last_manual ? (d.last_manual.state === 'failed' ? 'Failed: ' + (d.last_manual.message || '') : fmt.abs(d.last_manual.ts)) : null)),
      cupsOff ? null : h('div', {}, h('h4', {}, 'Recent prints'),
        d.history.length ? h('ul', { class: 'rows' }, d.history.map(checkRow)) : h('p', { class: 'empty' }, 'Nothing printed yet.')),
      h('details', { class: 'more' }, h('summary', {}, icon('chevron', 16), 'How it works'),
        h('div', { class: 'footnote', style: { 'margin-top': '10px' } },
          h('p', {}, `panda-print reads the printer’s page count every ${fmt.minutes(t.poll_interval / 60)}. When it goes up, something was printed, from any device. Reading the count prints nothing.`),
          h('p', {}, 'Pages with no matching CUPS print job were sent straight to the printer, for example from a phone.'),
          t.since && h('p', {}, `Watching since ${fmt.abs(t.since)}. Last read ${t.last_poll ? fmt.ago(t.last_poll) : 'not yet'}.`),
          d.host_state.configured && (d.host_state.error
            ? h('p', {}, `Timer file: ${d.host_state.error}`)
            : h('p', {}, `An older timer last saw the count at ${d.host_state.count} on ${fmt.abs(d.host_state.noticed_at)}.`)))));
  }

  // ------------------------------------------------------------ automation settings
  // [key, label, input type, unit text, hint]. Every value falls back to the default shown in its hint.
  const FIELDS = [
    ['idle_days', 'Print after', 'number', 'days idle', 'How long the printer can sit unused before it prints.'],
    ['run_at', 'Not before', 'time', '', 'Earliest time of day to print.'],
    ['check_minutes', 'Check the page count every', 'number', 'minutes', 'How often to look for new prints. It does not change when the auto print happens.'],
    ['attribution_minutes', 'Match our page to the count within', 'number', 'minutes', 'Time allowed for our page to show up in the count. A rise after that is treated as someone else’s print.'],
    ['job_pattern', 'Also recognise jobs named', 'text', '', 'Test pages from another timer with this in the job name are listed as automatic prints, not as someone else’s.'],
  ];

  function renderAutomation(d) {
    if (d.cups_configured === false) {  // CUPS is optional: show what it would unlock
      autoCard.setBadge(ui.badge('CUPS not set up'));
      autoCard.set(cupsPanel(kit, { title: 'Add CUPS to print automatically', ghost: 'form',
        unlocks: ['Automatic printing when the printer sits unused, so the ink keeps flowing', 'Print now, with a preview of the page', 'Your own pages (PDF, PNG, JPEG) to print instead of a test pattern', 'The print queue and recent jobs on the Overview tab'] }));
      return;
    }
    const custom = d.customised.length > 0;
    autoCard.setBadge(ui.badge(d.values.enabled ? 'On' : 'Off', d.values.enabled ? 'ok' : ''));
    const inputs = {}, errors = {};
    const saveBtn = h('button', { class: 'btn primary', type: 'submit', disabled: true, title: 'Save these automation settings for this printer' }, 'Save settings');
    // Save is only available when something differs from what is saved
    const same = (key, type) => (type === 'number'
      ? Number(inputs[key].value) === Number(d.values[key])
      : String(inputs[key].value).trim() === String(d.values[key]));
    const syncSave = () => { saveBtn.disabled = FIELDS.every(([key, , type]) => same(key, type)); };

    // The switch saves at once, like any switch; it only ever prints when a check is really due.
    const toggle = h('input', { id: 'auto-enabled', type: 'checkbox', role: 'switch', checked: d.values.enabled ? true : null,
      onchange: async (e) => {
        const res = await api(`${base()}/automation`, { method: 'PUT', body: { enabled: e.target.checked } });
        if (!res.ok) { e.target.checked = !e.target.checked; toast(res.error?.message || 'Could not save.', 'bad'); return; }
        toast(res.data.values.enabled ? 'Auto print is on.' : 'Auto print is off.');
        renderAutomation(res.data);
        refresh();
      } });

    const field = ([key, label, type, unit, hint]) => {
      const shown = d.defaults[key];
      inputs[key] = h('input', { id: `auto-${key}`, class: 'input', type, value: d.values[key], step: type === 'number' ? 'any' : null,
        autocomplete: 'off', spellcheck: 'false', oninput: () => { errors[key].textContent = ''; inputs[key].removeAttribute('aria-invalid'); syncSave(); } });
      errors[key] = h('p', { class: 'msg', role: 'alert' });
      const extra = key === 'run_at' ? ` Time zone: ${d.timezone} (change it with TZ).` : '';
      return h('div', { class: 'field' },
        h('label', { for: `auto-${key}` }, label),
        h('div', { class: 'input-unit' }, inputs[key], unit && h('span', { class: 'unit' }, unit)),
        h('p', { class: 'hint' }, `${hint}${extra} Default: ${shown}${unit ? ` ${unit}` : ''}.`),
        errors[key]);
    };

    async function save(event) {
      event.preventDefault();
      const body = Object.fromEntries(FIELDS.map(([key]) => [key, inputs[key].value]));
      const res = await api(`${base()}/automation`, { method: 'PUT', body });
      if (!res.ok) {
        const fields = res.error?.fields;
        if (!fields) { toast(res.error?.message || 'Could not save.', 'bad'); return; }
        for (const [key, message] of Object.entries(fields)) { errors[key].textContent = message; inputs[key].setAttribute('aria-invalid', 'true'); }
        inputs[Object.keys(fields)[0]].focus();
        return;
      }
      toast('Settings saved.');
      renderAutomation(res.data);
      refresh();  // the badge and the idle-day wording follow the new values
    }

    async function reset() {
      const res = await api(`${base()}/automation`, { method: 'DELETE' });
      if (!res.ok) { toast(res.error?.message || 'Could not reset.', 'bad'); return; }
      toast('Back to the defaults.');
      renderAutomation(res.data);
      refresh();
    }

    async function setRotate(on) {
      const res = await api(`${base()}/automation`, { method: 'PUT', body: { rotate: on } });
      if (!res.ok) { toast(res.error?.message || 'Could not save.', 'bad'); return; }
      await refreshAuto();
      paintRun();
    }
    const radio = (label, checked, value) => h('label', { class: 'radio' },
      h('input', { type: 'radio', name: 'auto-rotate', checked: checked ? true : null, onchange: () => setRotate(value) }), label);
    const mine = rotation ? rotation.items.filter((i) => !i.standard && i.enabled).length : 0;
    // one quiet line: what prints, and a way into the library
    const pagesRow = rotation && h('div', { class: 'pagesrow' },
      h('span', { class: 'pagesrow-label' }, 'Page to print'),
      radio('Standard test page', !rotation.rotate, false),
      radio('Rotate my pages', rotation.rotate, true),
      h('span', { class: 'sub pagesrow-note' }, rotation.rotate ? `Next: “${rotation.next.name}”` : mine ? `${mine} page${mine === 1 ? '' : 's'} added` : ''),
      h('button', { class: 'btn small', type: 'button', title: 'Add, preview, reorder or delete the pages that can be printed', onclick: () => pagesDialog.open() }, 'Manage pages…'));

    autoCard.set(
      h('label', { class: 'switch', for: 'auto-enabled' }, toggle, h('span', { class: 'track', 'aria-hidden': 'true' }),
        h('span', { class: 'switch-text' }, h('strong', {}, 'Print automatically'),
          h('span', { class: 'sub' }, `If the printer sits unused for ${d.values.idle_days} day${d.values.idle_days === 1 ? '' : 's'}, print one page to keep the ink flowing. Leave this off if another timer already does it.`))),
      pagesRow,
      h('form', { class: 'autoform', onsubmit: save },
        h('div', { class: 'autogrid' }, FIELDS.map(field)),
        h('div', { class: 'actions left' },
          saveBtn,
          h('button', { class: 'btn', type: 'button', disabled: !custom, title: 'Put every setting back to its default (the automatic-print switch stays as it is)', onclick: reset }, 'Reset to defaults'),
          custom && h('span', { class: 'sub' }, `${d.customised.length} setting${d.customised.length === 1 ? '' : 's'} changed from the defaults.`))));
    paintRun();
  }

  // another printer was chosen (or this one's settings were edited): start over
  kit.printer.subscribe(() => {
    waitUntil = 0;
    for (const c of [nozzleCard, autoCard]) { c.set(ui.skeleton()); c.setBadge(null); c.stamp(''); }
    refresh();
    refreshAuto();
  });

  refreshAuto();
  kit.poll(refresh, 60_000);
}
