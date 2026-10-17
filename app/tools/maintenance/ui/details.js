// Details tab: the printer itself (state, ink, page counter) and its CUPS queue with recent prints.
import { JOB_BADGE, cardLoader, reasonKind } from './shared.js';

export function mount(root, kit) {
  const { h, icon, fmt, ui } = kit;
  const { load } = cardLoader(kit);

  const printerCard = ui.card({ title: 'Printer', iconName: 'printer', wide: true, onRefresh: () => load(printerCard, 'printer', renderPrinter) });
  const queueCard = ui.card({ title: 'Print queue', iconName: 'list', wide: true, onRefresh: () => load(queueCard, 'queue', renderQueue) });
  root.append(h('div', { class: 'grid' }, printerCard.el, queueCard.el));

  // ------------------------------------------------------------ printer
  function renderPrinter(d) {
    printerCard.setBadge(ui.badge(d.state.label, ui.healthKind(d.state.health)));
    const c = d.counter;
    printerCard.set(
      h('div', { class: 'sub' }, d.name),
      d.state.reasons.length || d.state.message
        ? h('div', { class: 'chips' }, d.state.reasons.map((r) => ui.badge(r.text, reasonKind(r.severity))),
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
      d.state.reasons.length ? h('div', { class: 'chips' }, d.state.reasons.map((r) => ui.badge(r.text, reasonKind(r.severity)))) : null,
      h('div', {}, h('h4', {}, 'Recent prints'),
        d.jobs_error && ui.errorBox(d.jobs_error),
        recent.length ? h('ul', { class: 'rows' }, recent.map((r) => r.row))
          : !d.jobs_error && h('p', { class: 'empty' }, 'Nothing printed yet.'),
        h('p', { class: 'ink-note' }, 'CUPS only knows about prints that go through it. Prints sent straight to the printer (from a phone, or copies made on the printer) are spotted from its page counter, so they show a page count and an approximate time.')));
  }

  // another printer was chosen (or this one's settings were edited): start over
  kit.printer.subscribe(() => {
    for (const c of [printerCard, queueCard]) { c.set(ui.skeleton()); c.setBadge(null); c.stamp(''); }
    load(printerCard, 'printer', renderPrinter);
    load(queueCard, 'queue', renderQueue);
  });

  kit.poll(() => load(printerCard, 'printer', renderPrinter), 30_000);
  kit.poll(() => load(queueCard, 'queue', renderQueue), 30_000);
}
