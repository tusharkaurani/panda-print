// Nozzle Check tab: when the printer last printed, when the next automated check is due, the history of
// checks, and the "Print nozzle check now" button. This is the file to extend with more nozzle-check features.
import { cardLoader } from './shared.js';

const SOURCE = { manual: 'Manual check', auto: 'Automated check', host: 'Automated check (host timer)' };
const KIND = { external: 'A document was printed', direct: 'Printed straight to the printer', nozzle: 'A nozzle check', baseline: 'Earliest known activity' };

export function mount(root, kit) {
  const { h, icon, fmt, ui, api, toast } = kit;
  const { base, load } = cardLoader(kit);

  const nozzleCard = ui.card({ title: 'Nozzle check', iconName: 'droplet', wide: true, onRefresh: () => refresh() });
  root.append(h('div', { class: 'grid' }, nozzleCard.el));
  const refresh = () => load(nozzleCard, 'nozzle', renderNozzle);

  // ------------------------------------------------------------ nozzle check
  let retryAt = 0;
  const printBtn = h('button', { class: 'btn primary', type: 'button', onclick: () => dialog.showModal() }, icon('droplet', 17), 'Print nozzle check now');
  const printHint = h('span', { class: 'hint' });

  function refreshPrintButton() {
    const left = Math.ceil((retryAt - Date.now()) / 1000);
    if (left > 0) {
      printBtn.disabled = true;
      printHint.textContent = `You can print another in ${fmt.countdown(left)}.`;
    } else {
      printBtn.disabled = false;
      printHint.textContent = 'Prints one small test page (four colour bars). Uses a sheet of paper and a little ink.';
      if (retryAt) { retryAt = 0; refresh(); }
    }
  }
  setInterval(() => { if (retryAt) refreshPrintButton(); }, 1000);

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
    const lp = d.last_print, nx = d.next_check;
    nozzleCard.setBadge(nx ? (nx.overdue ? ui.badge('Check due', 'warn') : ui.badge(`Next in ${fmt.days(nx.days_until)}`, 'ok')) : null);

    retryAt = d.print.allowed ? 0 : Date.now() + d.print.retry_after * 1000;
    refreshPrintButton();

    const t = d.tracking;
    const problems = [t.counter_error && `Can’t read the page counter: ${t.counter_error}`, t.cups_error && `Can’t read CUPS: ${t.cups_error}`].filter(Boolean);

    nozzleCard.set(
      problems.length ? h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, problems.map((p) => h('div', {}, p)))) : null,
      h('div', { class: 'stats' },
        ui.stat('Last print (any device)', lp ? `${lp.approximate ? 'At least ' : ''}${fmt.ago(lp.ts)}` : '–',
          lp && `${fmt.abs(lp.ts)} · ${KIND[lp.kind] || ''}`),
        ui.stat('Days since last print', fmt.days(d.days_since_last_print), `Check fires after ${d.idle_days_threshold} idle days`),
        ui.stat('Next automated check', !nx ? '–' : nx.overdue ? 'Due now' : `In ${fmt.days(nx.days_until)}`,
          !nx ? null : nx.overdue ? 'Prints at the next daily run of the host timer' : `Around ${fmt.day(nx.due_at)}`),
        ui.stat('Last automated check', d.last_auto ? fmt.ago(d.last_auto.ts) : 'None yet', d.last_auto ? fmt.abs(d.last_auto.ts) : 'Not seen since tracking began'),
        ui.stat('Last manual run', d.last_manual ? fmt.ago(d.last_manual.ts) : 'Never',
          d.last_manual ? (d.last_manual.state === 'failed' ? 'Failed: ' + (d.last_manual.message || '') : fmt.abs(d.last_manual.ts)) : null)),
      h('div', { class: 'print-row' }, printBtn, printHint),
      h('div', {}, h('h4', {}, 'Recent checks'),
        d.history.length ? h('ul', { class: 'rows' }, d.history.map(checkRow)) : h('p', { class: 'empty' }, 'No nozzle checks recorded yet.')),
      h('details', { class: 'more' }, h('summary', {}, icon('chevron', 16), 'How this is tracked'),
        h('div', { class: 'footnote', style: { 'margin-top': '10px' } },
          h('p', {}, `The dashboard reads the printer’s lifetime page counter every ${Math.round(t.poll_interval / 60)} minutes and notes when it changes, so prints from any device are counted and times are accurate to about that interval.`),
          h('p', {}, 'Nozzle checks are told apart from other printing by matching them with the CUPS job list. Pages that appear on the counter with no CUPS job behind them are shown as printed straight to the printer.'),
          t.since && h('p', {}, `Tracking since ${fmt.abs(t.since)}; last counter check ${t.last_poll ? fmt.ago(t.last_poll) : 'not yet'}.`),
          d.host_state.configured && (d.host_state.error
            ? h('p', {}, `Host timer state file: ${d.host_state.error}`)
            : h('p', {}, `Host timer last noticed the counter at ${d.host_state.count} on ${fmt.abs(d.host_state.noticed_at)} (it only checks once a day).`)))));
  }

  // ------------------------------------------------------------ confirm + print
  const dialog = h('dialog', { 'aria-labelledby': 'mt-confirm-title' },
    h('h3', { id: 'mt-confirm-title' }, 'Print a nozzle check?'),
    h('p', {}, 'This prints one small test page with cyan, magenta, yellow and black bars. Use it to see whether any nozzle is clogged.'),
    h('div', { class: 'actions' },
      h('button', { class: 'btn', type: 'button', onclick: () => dialog.close() }, 'Cancel'),
      h('button', { class: 'btn primary', type: 'button', onclick: doPrint }, icon('droplet', 16), 'Print')));
  root.append(dialog);

  async function doPrint() {
    dialog.close();
    printBtn.disabled = true;
    const res = await api(`${base()}/nozzle/print`, { method: 'POST' });
    if (res.ok) {
      toast(`Nozzle check sent to the printer (job #${res.data.job_id}).`);
    } else {
      toast(res.error.message || 'Could not print.', 'bad');
      if (res.status === 429) retryAt = Date.now() + (res.retryAfter || 60) * 1000;
    }
    printBtn.disabled = false;
    refresh();
    // the page counter moves a few seconds after the job is accepted
    [4000, 15000, 40000].forEach((ms) => setTimeout(refresh, ms));
  }

  // another printer was chosen (or this one's settings were edited): start over
  kit.printer.subscribe(() => {
    retryAt = 0;
    nozzleCard.set(ui.skeleton()); nozzleCard.setBadge(null); nozzleCard.stamp('');
    refresh();
  });

  kit.poll(refresh, 60_000);
}
