// Nozzle Check tab: when the printer last printed, the history of checks, and the Automation settings that
// drive how checks are tracked. This is the file to extend with more nozzle-check features.
import { createPagesDialog } from './pages.js';
import { cardLoader } from './shared.js';

// who sent a nozzle check: the Run now button, this app's schedule, or an older timer seen in the CUPS history
const SOURCE = { manual: 'Manual print', auto: 'Auto print · panda-print', host: 'Auto print · another timer' };
const BY = { manual: 'by you', auto: 'by panda-print', host: 'by another timer' };
const KIND = { external: 'A document', direct: 'Sent straight to the printer', nozzle: 'A nozzle check', baseline: 'Earliest known activity' };

export function mount(root, kit) {
  const { h, icon, fmt, ui, api, toast } = kit;
  const { base, load } = cardLoader(kit);

  const nozzleCard = ui.card({ title: 'Nozzle check', iconName: 'droplet', wide: true, onRefresh: () => refresh() });
  const autoCard = ui.card({ title: 'Automation', iconName: 'clock', wide: true, onRefresh: () => refreshAuto() });
  root.append(h('div', { class: 'grid' }, nozzleCard.el, autoCard.el));
  const refresh = () => load(nozzleCard, 'nozzle', renderNozzle);
  let rotation = null;  // this printer's pages and what prints next (Service.rotation_status)
  async function refreshAuto() {
    const rot = await api(`${base()}/rotation`);
    rotation = rot.ok ? rot.data : null;
    return load(autoCard, 'automation', renderAutomation);
  }
  const pagesDialog = createPagesDialog(kit, { base, onChange: () => { refreshAuto(); refresh(); } });
  root.append(pagesDialog.dialog);

  // ------------------------------------------------------------ run a check now (to test the real thing)
  // The button and its dialog are created once; the card body they sit in is rebuilt on every save.
  let waitUntil = 0;  // the manual-print rate limit: when the next one is allowed
  const runBtn = h('button', { class: 'btn', type: 'button', title: 'Print one page on this printer right now (asks you to confirm first)', onclick: () => dialog.showModal() }, icon('droplet', 16), 'Print now');
  const runHint = h('span', { class: 'hint' });

  function paintRun() {
    const left = Math.ceil((waitUntil - Date.now()) / 1000);
    runBtn.disabled = left > 0;
    runHint.textContent = left > 0
      ? `You can run another in ${fmt.countdown(left)}.`
      : rotation?.rotate
        ? `Prints the next page, “${rotation.next.name}”, right now (a manual print). It restarts the idle clock.`
        : 'Prints one nozzle check page right now (a manual print). It restarts the idle clock.';
  }
  setInterval(() => { if (waitUntil > Date.now()) paintRun(); }, 1000);

  const dialog = h('dialog', { 'aria-labelledby': 'mt-run-title' },
    h('h3', { id: 'mt-run-title' }, 'Print a nozzle check now?'),
    h('p', {}, 'This sends one small test page with cyan, magenta, yellow and black bars to the printer. It uses a sheet of paper and a little ink.'),
    h('div', { class: 'actions' },
      h('button', { class: 'btn', type: 'button', title: 'Close without printing', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', title: 'Send the page to the printer now', onclick: runNow }, icon('droplet', 16), 'Print')));
  root.append(dialog);

  async function runNow() {
    dialog.close();
    runBtn.disabled = true;
    const res = await api(`${base()}/nozzle/print`, { method: 'POST' });
    if (res.ok) {
      toast(`Nozzle check sent to the printer (job #${res.data.job_id}).`);
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
    // the next check is summarised by this badge, so it has no card of its own
    nozzleCard.setBadge(!a.enabled
      ? (nx?.overdue ? ui.badge('Print due · auto print off', 'warn') : ui.badge('Auto print off'))
      : a.state === 'blocked' ? ui.badge('Auto print waiting', 'warn')
      : a.next_run_at ? ui.badge(a.next_run_at <= Date.now() / 1000 + 60 ? 'Print starting' : `Next auto print ${a.next_run_label || fmt.when(a.next_run_at)}`, 'ok') : null);

    waitUntil = d.print.allowed ? 0 : Date.now() + d.print.retry_after * 1000;
    paintRun();

    const t = d.tracking;
    const problems = [t.counter_error && `Can’t read the page counter: ${t.counter_error}`, t.cups_error && `Can’t read CUPS: ${t.cups_error}`,
      a.enabled && a.state === 'blocked' && `The auto print is waiting: ${a.reason}`].filter(Boolean);

    nozzleCard.set(
      problems.length ? h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, problems.map((p) => h('div', {}, p)))) : null,
      h('div', { class: 'stats four' },
        ui.stat('Last print', lp ? `${lp.approximate ? 'At least ' : ''}${fmt.ago(lp.ts)}` : '–',
          lp && `${fmt.abs(lp.ts)} · ${KIND[lp.kind] || ''}`),
        ui.stat('Idle for', fmt.days(d.days_since_last_print),
          a.enabled ? `Auto print after ${d.idle_days_threshold} idle days` : 'Auto print is off'),
        ui.stat('Last auto print', d.last_auto ? fmt.ago(d.last_auto.ts) : 'None yet',
          d.last_auto ? `${fmt.abs(d.last_auto.ts)} · ${BY[d.last_auto.source] || ''}` : 'None seen since tracking began'),
        ui.stat('Last manual print', d.last_manual ? fmt.ago(d.last_manual.ts) : 'Never',
          d.last_manual ? (d.last_manual.state === 'failed' ? 'Failed: ' + (d.last_manual.message || '') : fmt.abs(d.last_manual.ts)) : null)),
      h('div', {}, h('h4', {}, 'Recent nozzle prints'),
        d.history.length ? h('ul', { class: 'rows' }, d.history.map(checkRow)) : h('p', { class: 'empty' }, 'No nozzle prints recorded yet.')),
      h('details', { class: 'more' }, h('summary', {}, icon('chevron', 16), 'How this is tracked'),
        h('div', { class: 'footnote', style: { 'margin-top': '10px' } },
          h('p', {}, 'A nozzle check is a test page that gets printed. Reading the printer’s page total is different: it prints nothing, it is only how the dashboard notices prints.'),
          h('p', {}, `The page total is read every ${fmt.minutes(t.poll_interval / 60)}. When it rises, that is a print, so prints from any device are counted and their times are accurate to about that interval.`),
          h('p', {}, 'Nozzle checks are told apart from other printing by matching them with the CUPS job list. Pages that appear on the counter with no CUPS job behind them are shown as printed straight to the printer.'),
          t.since && h('p', {}, `Tracking since ${fmt.abs(t.since)}; last counter read ${t.last_poll ? fmt.ago(t.last_poll) : 'not yet'}.`),
          d.host_state.configured && (d.host_state.error
            ? h('p', {}, `Timer state file: ${d.host_state.error}`)
            : h('p', {}, `An older timer last noticed the counter at ${d.host_state.count} on ${fmt.abs(d.host_state.noticed_at)}.`)))));
  }

  // ------------------------------------------------------------ automation settings
  // [key, label, input type, unit text, hint]. Every value falls back to the default shown in its hint.
  const FIELDS = [
    ['idle_days', 'Idle days before an auto print', 'number', 'days', 'How long the printer can sit unused before a nozzle check is printed.'],
    ['run_at', 'Don’t print before', 'time', '', 'Quiet hours: an auto print that falls due earlier waits until this time of day.'],
    ['check_minutes', 'Read the page counter every', 'number', 'minutes', 'How often the dashboard asks the printer for its page total, to notice new prints. It only sets how quickly prints are noticed, not when the check prints.'],
    ['attribution_minutes', 'A new page counts as the nozzle check if it prints within', 'number', 'minutes', 'After a check is sent, the printer’s page total goes up by one. A rise within this time is credited to the check; a later one is treated as someone else’s print.'],
    ['job_pattern', 'Also recognise nozzle checks whose job name has', 'text', '', 'Nozzle-check pages sent to CUPS by another timer carry this text in their job name, so they are listed here and not mistaken for someone printing.'],
  ];

  function renderAutomation(d) {
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
      const extra = key === 'run_at' ? ` Uses the container’s time zone (${d.timezone}); set TZ to change it.` : '';
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
      toast('Automation settings saved.');
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
      h('span', { class: 'sub pagesrow-note' }, rotation.rotate ? `Next: “${rotation.next.name}”` : mine ? `${mine} of your pages ready` : ''),
      h('button', { class: 'btn small', type: 'button', title: 'Add, preview, reorder or delete the pages that can be printed', onclick: () => pagesDialog.open() }, 'Manage pages…'));

    autoCard.set(
      h('label', { class: 'switch', for: 'auto-enabled' }, toggle, h('span', { class: 'track', 'aria-hidden': 'true' }),
        h('span', { class: 'switch-text' }, h('strong', {}, 'Print the nozzle check automatically'),
          h('span', { class: 'sub' }, `When the printer has been idle for ${d.values.idle_days} day${d.values.idle_days === 1 ? '' : 's'}, a small test page is sent to its CUPS queue. Leave this off if another timer already does it. Default for new printers: ${d.defaults.enabled ? 'on' : 'off'}.`))),
      pagesRow,
      h('form', { class: 'autoform', onsubmit: save },
        h('div', { class: 'autogrid' }, FIELDS.map(field)),
        h('div', { class: 'actions left' },
          saveBtn,
          h('button', { class: 'btn', type: 'button', disabled: !custom, title: 'Put every setting back to its default (the automatic-print switch stays as it is)', onclick: reset }, 'Reset to defaults'),
          custom && h('span', { class: 'sub' }, `Changed from the defaults: ${d.customised.length} setting${d.customised.length === 1 ? '' : 's'}.`))),
      h('div', { class: 'runnow' }, runBtn, runHint));
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
