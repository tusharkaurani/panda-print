// Overview tab: the printer itself (state, ink, page counter, specs) and its CUPS queue with recent prints.
import { JOB_BADGE, cardLoader, cupsPanel, reasonKind } from './shared.js';

export function mount(root, kit) {
  const { h, icon, fmt, ui } = kit;
  const { load } = cardLoader(kit);

  const printerCard = ui.card({ title: kit.printer.current?.name || 'Printer', iconName: 'printer', wide: true, onRefresh: () => load(printerCard, 'printer', renderPrinter) });
  const queueCard = ui.card({ title: 'Print queue', iconName: 'list', wide: true, onRefresh: () => load(queueCard, 'queue', renderQueue) });
  root.append(h('div', { class: 'grid' }, printerCard.el, queueCard.el));

  // ------------------------------------------------------------ printer
  // The four facts that sit beside the rings; everything else goes in the row below
  const BESIDE = ['model', 'firmware', 'uptime', 'web'];

  function renderPrinter(d) {
    printerCard.setTitle(kit.printer.current?.name || d.name);
    printerCard.setBadge(ui.badge(d.state.label, ui.healthKind(d.state.health)));
    const c = d.counter;
    const beside = BESIDE.map((k) => d.details.find((r) => r.key === k)).filter(Boolean);
    const rest = d.details.filter((r) => !BESIDE.includes(r.key));
    // lifetime page counter: a card like the rest, first in the row below
    const counter = h('div', { class: 'fact' }, h('dt', {}, 'Lifetime pages printed'),
      h('dd', {}, c.value != null ? fmt.num(c.value) : '\u2013'),
      // the card's own "Updated" time covers a normal read; the age only matters when the printer can't be reached
      c.error && h('p', { class: 'msg' }, `${c.value != null ? `Showing the last known value${c.as_of ? ` (read ${fmt.ago(c.as_of)})` : ''}. ` : ''}${c.error}`));
    // switched off or off the network: the last known values stay on screen, dimmed, with a plain note
    const offline = d.offline && h('div', { class: 'notice warn' }, icon('alert', 18),
      h('div', {}, h('strong', {}, 'The printer isn’t answering. '),
        `It may be switched off or off the network. Showing what was last known${d.offline.as_of ? `, from ${fmt.ago(d.offline.as_of)}` : ''}.`),
      h('button', { class: 'btn small', type: 'button', title: 'Ask the printer again', onclick: () => load(printerCard, 'printer', renderPrinter) }, icon('refresh', 15), 'Try again'));
    printerCard.set(
      offline,
      // The badge already says Idle/Printing; only add words when there is something to flag
      (d.state.reasons.length || (d.state.message && d.state.health !== 'ok'))
        ? h('div', { class: 'chips' }, d.state.reasons.map((r) => ui.badge(r.text, reasonKind(r.severity))),
            d.state.message && d.state.health !== 'ok' && h('span', { class: 'sub' }, d.state.message))
        : null,
      // one row: the four rings, then Model / Firmware / Uptime / Web interface beside them
      h('div', { class: 'printer-top' },
        d.inks.length
          ? h('div', { class: `gauges${d.offline ? ' stale' : ''}`, role: 'group', 'aria-label': d.offline ? 'Ink levels as last known' : 'Ink levels' }, d.inks.map(ui.gauge))
          : h('p', { class: 'empty' }, 'The printer did not report ink levels.'),
        beside.length ? h('dl', { class: 'facts beside' }, beside.map(fact)) : null),
      h('dl', { class: 'facts' }, counter, rest.map(fact)));
  }

  function fact(r) {
    return h('div', { class: 'fact' }, h('dt', {}, r.label), h('dd', {}, factValue(r.value)));
  }

  // Addresses become links, kept to one line (the full text is the tooltip)
  function factValue(v) {
    return /^https?:\/\//.test(v)
      ? h('a', { class: 'fact-link', href: v, target: '_blank', rel: 'noopener noreferrer', title: v }, v.replace(/^https?:\/\//, '').replace(/\/$/, ''))
      : v;
  }

  // Label-over-value facts in as many columns as fit, so wide screens aren't a long thin list
  function facts(title, rows) {
    return h('div', {}, h('h4', {}, title),
      h('dl', { class: 'facts' }, rows.map((r) => h('div', { class: 'fact' }, h('dt', {}, r.label), h('dd', {}, factValue(r.value))))));
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
    if (d.configured === false) {  // CUPS is optional: say what it adds instead of an error
      queueCard.setBadge(ui.badge('CUPS not set up'));
      queueCard.set(cupsPanel(kit, { title: 'Add CUPS to see the print queue',
        unlocks: ['The print queue and recent print jobs', 'Print now, with a preview', 'Automatic printing to keep the ink flowing', 'Your own pages (PDF, PNG, JPEG) to print instead of a test pattern'] }));
      return;
    }
    queueCard.setBadge(ui.badge(d.state.label, ui.healthKind(d.state.health)));
    const waiting = d.queued_jobs?.length ?? d.queued;
    // one timeline: CUPS jobs and direct prints, newest first
    const recent = [
      ...d.jobs.map((j) => ({ t: j.completed || j.created || 0, row: jobRow(j) })),
      ...(d.direct || []).map((r) => ({ t: r.ts, row: directRow(r) })),
    ].sort((a, b) => b.t - a.t).slice(0, 10);
    queueCard.set(
      facts('Queue', [
        { label: 'Queue', value: `${d.name}${d.info && d.info !== d.name ? ` \u2013 ${d.info}` : ''}` },
        { label: 'Accepting jobs', value: d.state.accepting == null ? 'Unknown' : d.state.accepting ? 'Yes' : 'No' },
        { label: 'Waiting', value: waiting ? `${waiting} job${waiting === 1 ? '' : 's'}` : 'Nothing waiting' },
        ...(d.device ? [{ label: 'Connection', value: d.device }] : []),
      ]),
      d.state.reasons.length ? h('div', { class: 'chips' }, d.state.reasons.map((r) => ui.badge(r.text, reasonKind(r.severity)))) : null,
      h('div', {}, h('h4', {}, 'Recent prints'),
        d.jobs_error && ui.errorBox(d.jobs_error),
        recent.length ? h('ul', { class: 'rows cards' }, recent.map((r) => r.row))
          : !d.jobs_error && h('p', { class: 'empty' }, 'Nothing printed yet.'),
        h('p', { class: 'list-note' }, 'CUPS only knows about prints that go through it. Prints sent straight to the printer (from a phone, or copies made on the printer) are spotted from its page counter, so they show a page count and an approximate time.')));
  }

  // another printer was chosen (or this one's settings were edited): start over
  kit.printer.subscribe(() => {
    printerCard.setTitle(kit.printer.current?.name || 'Printer');
    for (const c of [printerCard, queueCard]) { c.set(ui.skeleton()); c.setBadge(null); c.stamp(''); }
    load(printerCard, 'printer', renderPrinter);
    load(queueCard, 'queue', renderQueue);
  });

  kit.poll(() => load(printerCard, 'printer', renderPrinter), 30_000);
  kit.poll(() => load(queueCard, 'queue', renderQueue), 30_000);
}
