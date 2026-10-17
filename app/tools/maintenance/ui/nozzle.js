// Nozzle Check tab: when the printer last printed, the history of checks, and the Automation settings that
// drive how checks are tracked. This is the file to extend with more nozzle-check features.
import { cardLoader } from './shared.js';

const SOURCE = { manual: 'Manual check', auto: 'Automated check', host: 'Automated check (host timer)' };
const KIND = { external: 'A document was printed', direct: 'Printed straight to the printer', nozzle: 'A nozzle check', baseline: 'Earliest known activity' };

export function mount(root, kit) {
  const { h, icon, fmt, ui, api, toast } = kit;
  const { base, load } = cardLoader(kit);

  const nozzleCard = ui.card({ title: 'Nozzle check', iconName: 'droplet', wide: true, onRefresh: () => refresh() });
  const autoCard = ui.card({ title: 'Automation', iconName: 'clock', wide: true, onRefresh: () => refreshAuto() });
  root.append(h('div', { class: 'grid' }, nozzleCard.el, autoCard.el));
  const refresh = () => load(nozzleCard, 'nozzle', renderNozzle);
  const refreshAuto = () => load(autoCard, 'automation', renderAutomation);

  // ------------------------------------------------------------ nozzle check
  function checkRow(c) {
    const state = c.state === 'failed' ? ['Failed', 'bad'] : c.state === 'submitted' ? ['Sent', 'info'] : ['Printed', 'ok'];
    return h('li', { class: 'row' },
      h('span', { class: 'ic' }, icon('droplet', 18)),
      h('div', { class: 'main' }, h('div', { class: 'title' }, SOURCE[c.source] || c.source),
        h('div', { class: 'meta' }, [fmt.abs(c.ts), c.job_id && `job #${c.job_id}`].filter(Boolean).join(' · ')),
        c.message && h('div', { class: 'msg' }, c.message)),
      h('div', { class: 'side' }, ui.badge(...state)));
  }

  function renderNozzle(d) {
    const lp = d.last_print, nx = d.next_check, a = d.automation;
    // the next check is summarised by this badge, so it has no card of its own
    nozzleCard.setBadge(!a.enabled
      ? (nx?.overdue ? ui.badge('Check due · auto off', 'warn') : ui.badge('Auto check off'))
      : a.state === 'blocked' ? ui.badge('Auto check waiting', 'warn')
      : a.next_run_at ? ui.badge(a.next_run_at <= Date.now() / 1000 + 60 ? 'Check starting' : `Next check ${fmt.when(a.next_run_at)}`, 'ok') : null);

    const t = d.tracking;
    const problems = [t.counter_error && `Can’t read the page counter: ${t.counter_error}`, t.cups_error && `Can’t read CUPS: ${t.cups_error}`,
      a.enabled && a.state === 'blocked' && `The automatic check is waiting: ${a.reason}`].filter(Boolean);

    nozzleCard.set(
      problems.length ? h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, problems.map((p) => h('div', {}, p)))) : null,
      h('div', { class: 'stats four' },
        ui.stat('Last print (any device)', lp ? `${lp.approximate ? 'At least ' : ''}${fmt.ago(lp.ts)}` : '–',
          lp && `${fmt.abs(lp.ts)} · ${KIND[lp.kind] || ''}`),
        ui.stat('Days since last print', fmt.days(d.days_since_last_print),
          a.enabled ? `Automatic check after ${d.idle_days_threshold} idle days` : 'Automatic check is off'),
        ui.stat('Last automated check', d.last_auto ? fmt.ago(d.last_auto.ts) : 'None yet', d.last_auto ? fmt.abs(d.last_auto.ts) : 'Not seen since tracking began'),
        ui.stat('Last manual run', d.last_manual ? fmt.ago(d.last_manual.ts) : 'Never',
          d.last_manual ? (d.last_manual.state === 'failed' ? 'Failed: ' + (d.last_manual.message || '') : fmt.abs(d.last_manual.ts)) : null)),
      h('div', {}, h('h4', {}, 'Recent checks'),
        d.history.length ? h('ul', { class: 'rows' }, d.history.map(checkRow)) : h('p', { class: 'empty' }, 'No nozzle checks recorded yet.')),
      h('details', { class: 'more' }, h('summary', {}, icon('chevron', 16), 'How this is tracked'),
        h('div', { class: 'footnote', style: { 'margin-top': '10px' } },
          h('p', {}, `The dashboard reads the printer’s lifetime page counter every ${fmt.minutes(t.poll_interval / 60)} and notes when it changes, so prints from any device are counted and times are accurate to about that interval.`),
          h('p', {}, 'Nozzle checks are told apart from other printing by matching them with the CUPS job list. Pages that appear on the counter with no CUPS job behind them are shown as printed straight to the printer.'),
          t.since && h('p', {}, `Tracking since ${fmt.abs(t.since)}; last counter check ${t.last_poll ? fmt.ago(t.last_poll) : 'not yet'}.`),
          d.host_state.configured && (d.host_state.error
            ? h('p', {}, `Timer state file: ${d.host_state.error}`)
            : h('p', {}, `An older timer last noticed the counter at ${d.host_state.count} on ${fmt.abs(d.host_state.noticed_at)}.`)))));
  }

  // ------------------------------------------------------------ automation settings
  // [key, label, input type, unit text, hint]. Every value falls back to the default shown in its hint.
  const FIELDS = [
    ['idle_days', 'Idle days before a check is due', 'number', 'days', 'How long the printer can sit unused before a nozzle check is printed.'],
    ['run_at', 'Don’t print before', 'time', '', 'Quiet hours: a check that falls due earlier waits until this time of day.'],
    ['check_minutes', 'Read the page counter every', 'number', 'minutes', 'How often the dashboard looks for new prints.'],
    ['attribution_minutes', 'Match a check to the counter within', 'number', 'minutes', 'How long after a check is sent the page counter may rise and still count as that check.'],
    ['job_pattern', 'Recognise older checks by name', 'text', '', 'CUPS jobs whose name contains this text are nozzle-check pages printed by another timer.'],
  ];

  function renderAutomation(d) {
    const custom = d.customised.length > 0;
    autoCard.setBadge(ui.badge(d.values.enabled ? 'On' : 'Off', d.values.enabled ? 'ok' : ''));
    const inputs = {}, errors = {};

    // The switch saves at once, like any switch; it only ever prints when a check is really due.
    const toggle = h('input', { id: 'auto-enabled', type: 'checkbox', role: 'switch', checked: d.values.enabled ? true : null,
      onchange: async (e) => {
        const res = await api(`${base()}/automation`, { method: 'PUT', body: { enabled: e.target.checked } });
        if (!res.ok) { e.target.checked = !e.target.checked; toast(res.error?.message || 'Could not save.', 'bad'); return; }
        toast(res.data.values.enabled ? 'Automatic nozzle check is on.' : 'Automatic nozzle check is off.');
        renderAutomation(res.data);
        refresh();
      } });

    const field = ([key, label, type, unit, hint]) => {
      const shown = d.defaults[key];
      inputs[key] = h('input', { id: `auto-${key}`, class: 'input', type, value: d.values[key], step: type === 'number' ? 'any' : null,
        autocomplete: 'off', spellcheck: 'false', oninput: () => { errors[key].textContent = ''; inputs[key].removeAttribute('aria-invalid'); } });
      errors[key] = h('p', { class: 'msg', role: 'alert' });
      const extra = key === 'run_at' ? ` Clock: ${d.timezone}.` : '';
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

    autoCard.set(
      h('label', { class: 'switch', for: 'auto-enabled' }, toggle, h('span', { class: 'track', 'aria-hidden': 'true' }),
        h('span', { class: 'switch-text' }, h('strong', {}, 'Print the nozzle check automatically'),
          h('span', { class: 'sub' }, `When the printer has been idle for ${d.values.idle_days} day${d.values.idle_days === 1 ? '' : 's'}, a small test page is sent to its CUPS queue. Leave this off if another timer already does it. Default for new printers: ${d.defaults.enabled ? 'on' : 'off'}.`))),
      h('form', { class: 'autoform', onsubmit: save },
        h('div', { class: 'autogrid' }, FIELDS.map(field)),
        h('div', { class: 'actions left' },
          h('button', { class: 'btn primary', type: 'submit' }, 'Save settings'),
          h('button', { class: 'btn', type: 'button', disabled: !custom, onclick: reset }, 'Reset to defaults'),
          custom && h('span', { class: 'sub' }, `Changed from the defaults: ${d.customised.length} setting${d.customised.length === 1 ? '' : 's'}.`))));
  }

  // another printer was chosen (or this one's settings were edited): start over
  kit.printer.subscribe(() => {
    for (const c of [nozzleCard, autoCard]) { c.set(ui.skeleton()); c.setBadge(null); c.stamp(''); }
    refresh();
    refreshAuto();
  });

  refreshAuto();
  kit.poll(refresh, 60_000);
}
