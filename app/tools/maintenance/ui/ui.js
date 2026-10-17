// Printer Maintenance UI. mount(root, kit) is the whole contract between a tool and the shell.

const SOURCE = { manual: 'Manual check', auto: 'Automated check', host: 'Automated check (host timer)' };
const KIND = { external: 'A document was printed', direct: 'Printed straight to the printer', nozzle: 'A nozzle check', baseline: 'Earliest known activity' };
const JOB_BADGE = { completed: 'ok', printing: 'info', pending: 'warn', held: 'warn', stopped: 'warn', canceled: 'bad', aborted: 'bad' };

export function mount(root, kit) {
  const { h, icon, fmt, ui, api, toast } = kit;
  root.classList.add('mt');

  const printerCard = ui.card({ title: 'Printer', iconName: 'printer', wide: true, onRefresh: () => load(printerCard, 'printer', renderPrinter) });
  const nozzleCard = ui.card({ title: 'Nozzle check', iconName: 'droplet', wide: true, onRefresh: () => load(nozzleCard, 'nozzle', renderNozzle) });
  const queueCard = ui.card({ title: 'Print queue', iconName: 'list', wide: true, onRefresh: () => load(queueCard, 'queue', renderQueue) });
  root.append(h('div', { class: 'grid' }, printerCard.el, nozzleCard.el, queueCard.el));

  // ------------------------------------------------------------ generic loader
  const base = () => `/maintenance/${kit.printer.current?.id}`;

  async function load(card, what, render) {
    const id = kit.printer.current?.id;
    if (!id) return;
    card.busy(true);
    const res = await api(`${base()}/${what}`);
    if (id !== kit.printer.current?.id) return;  // the selection changed while we waited
    card.busy(false);
    if (!res.ok) {
      card.setBadge(ui.badge('Unavailable', 'bad'));
      card.set(ui.errorBox(res.error, () => load(card, what, render)));
      return;
    }
    render(res.data);
    card.stamp();
  }

  // ------------------------------------------------------------ printer
  function renderPrinter(d) {
    printerCard.setBadge(ui.badge(d.state.label, ui.healthKind(d.state.health)));
    const c = d.counter;
    printerCard.set(
      h('div', { class: 'sub' }, d.name),
      d.state.reasons.length || d.state.message
        ? h('div', { class: 'chips' }, d.state.reasons.map((r) => ui.badge(r.text, r.severity === 'error' ? 'bad' : r.severity === 'warning' ? 'warn' : 'info')),
            d.state.message && h('span', { class: 'sub' }, d.state.message))
        : null,
      d.inks.length
        ? h('div', {}, h('div', { class: 'gauges', role: 'group', 'aria-label': 'Ink levels' }, d.inks.map(ui.gauge)),
            h('p', { class: 'ink-note' }, 'Levels are the printer’s own estimate.'))
        : h('p', { class: 'empty' }, 'The printer did not report ink levels.'),
      h('div', { class: 'stat' },
        h('div', { class: 'k' }, 'Lifetime pages printed'),
        h('div', { class: 'counter' }, h('span', { class: 'big' }, fmt.num(c.value)),
          c.value != null && c.as_of && !c.error && h('span', { class: 's' }, `read ${fmt.ago(c.as_of)}`)),
        c.error && h('p', { class: 'msg' }, `${c.value != null ? 'Showing the last known value. ' : ''}${c.error}`)),
      d.details.length
        ? h('details', { class: 'more' }, h('summary', {}, icon('chevron', 16), 'More details'),
            h('dl', { class: 'kv' }, d.details.flatMap((r) => [h('dt', {}, r.label), h('dd', {}, r.value)])))
        : null);
  }

  // ------------------------------------------------------------ queue
  function jobRow(j) {
    const when = j.completed || j.created;
    const bits = [`#${j.id}`, j.user, j.pages && `${j.pages} page${j.pages === 1 ? '' : 's'}`, when && fmt.ago(when)].filter(Boolean);
    return h('li', { class: 'row' },
      h('span', { class: 'ic' }, icon(j.nozzle_check ? 'droplet' : 'file', 18)),
      h('div', { class: 'main' }, h('div', { class: 'title', title: j.name }, j.name), h('div', { class: 'meta' }, bits.join(' · '))),
      h('div', { class: 'side' }, ui.badge(j.state, JOB_BADGE[j.state] || '')));
  }

  // A print that never went through CUPS (a phone talking straight to the printer, or a copy),
  // known only from the page counter: a page count and an approximate time.
  function directRow(r) {
    const bits = [`${r.pages} page${r.pages === 1 ? '' : 's'}`, 'sent straight to the printer', fmt.ago(r.ts)];
    return h('li', { class: 'row' },
      h('span', { class: 'ic' }, icon('phone', 18)),
      h('div', { class: 'main' }, h('div', { class: 'title' }, 'Printed without CUPS'),
        h('div', { class: 'meta' }, bits.join(' \u00b7 '))),
      h('div', { class: 'side' }, ui.badge('direct', 'info')));
  }

  function renderQueue(d) {
    queueCard.setBadge(ui.badge(d.state.label, ui.healthKind(d.state.health)));
    const waiting = d.queued_jobs?.length ?? d.queued;
    // one timeline: CUPS jobs and direct prints, newest first
    const recent = [
      ...d.jobs.map((j) => ({ t: j.completed || j.created || 0, row: jobRow(j) })),
      ...(d.direct || []).map((r) => ({ t: r.ts, row: directRow(r) })),
    ].sort((a, b) => b.t - a.t).slice(0, 10);
    queueCard.set(
      h('dl', { class: 'kv' },
        h('dt', {}, 'Queue'), h('dd', {}, `${d.name}${d.info && d.info !== d.name ? ` \u2013 ${d.info}` : ''}`),
        h('dt', {}, 'Accepting jobs'), h('dd', {}, d.state.accepting == null ? 'Unknown' : d.state.accepting ? 'Yes' : 'No'),
        h('dt', {}, 'Waiting'), h('dd', {}, waiting ? `${waiting} job${waiting === 1 ? '' : 's'}` : 'Nothing waiting'),
        d.device && h('dt', {}, 'Connection'), d.device && h('dd', {}, d.device)),
      d.state.reasons.length ? h('div', { class: 'chips' }, d.state.reasons.map((r) => ui.badge(r.text, r.severity === 'error' ? 'bad' : r.severity === 'warning' ? 'warn' : 'info'))) : null,
      h('div', {}, h('h4', {}, 'Recent prints'),
        d.jobs_error && ui.errorBox(d.jobs_error),
        recent.length ? h('ul', { class: 'rows' }, recent.map((r) => r.row))
          : !d.jobs_error && h('p', { class: 'empty' }, 'Nothing printed yet.'),
        h('p', { class: 'ink-note' }, 'CUPS only knows about prints that go through it. Prints sent straight to the printer (from a phone, or copies made on the printer) are spotted from its page counter, so they show a page count and an approximate time.')));
  }

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
      if (retryAt) { retryAt = 0; load(nozzleCard, 'nozzle', renderNozzle); }
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
    refreshAll();
    // the page counter moves a few seconds after the job is accepted
    [4000, 15000, 40000].forEach((ms) => setTimeout(refreshAll, ms));
  }

  function refreshAll() {
    load(nozzleCard, 'nozzle', renderNozzle);
    load(printerCard, 'printer', renderPrinter);
    load(queueCard, 'queue', renderQueue);
  }

  // another printer was chosen (or this one's settings were edited): start over
  kit.printer.subscribe(() => {
    retryAt = 0;
    for (const c of [printerCard, nozzleCard, queueCard]) { c.set(ui.skeleton()); c.setBadge(null); c.stamp(''); }
    refreshAll();
  });

  kit.poll(() => load(printerCard, 'printer', renderPrinter), 30_000);
  kit.poll(() => load(queueCard, 'queue', renderQueue), 30_000);
  kit.poll(() => load(nozzleCard, 'nozzle', renderNozzle), 60_000);
}
