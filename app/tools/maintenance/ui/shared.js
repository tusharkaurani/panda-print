// Helpers shared by the maintenance tool's tabs.

export const JOB_BADGE = { completed: 'ok', printing: 'info', pending: 'warn', held: 'warn', stopped: 'warn', canceled: 'bad', aborted: 'bad' };

// Badge kind for a printer-state-reason severity
export const reasonKind = (severity) => (severity === 'error' ? 'bad' : severity === 'warning' ? 'warn' : 'info');

// Loads one card from /api/maintenance/<printer id>/<what>, for the printer currently selected.
// Errors render inside the card (with a retry), so one unreachable source never blanks the tab.
export function cardLoader(kit) {
  const { api, ui } = kit;
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

  return { base, load };
}

// CUPS is optional. When a printer has no CUPS queue, the cards that need it show this instead: a faded sketch of
// what the card would hold (a skeleton), what CUPS adds, and how to set it up. `ghost` names the sketch.
export function cupsPanel(kit, { title, unlocks, ghost = 'rows' }) {
  const { h, icon } = kit;
  const bar = (w) => h('span', { class: `ghost-bar w${w}` });
  const sketch = ghost === 'form'
    ? [h('div', { class: 'ghost-switch' }, h('span', { class: 'ghost-knob' }), bar(40)),
       h('div', { class: 'ghost-fields' }, [60, 50, 70].map((w) => h('div', { class: 'ghost-field' }, bar(30), h('span', { class: 'ghost-input' }))))]
    : [1, 2, 3].map(() => h('div', { class: 'ghost-row' }, h('span', { class: 'ghost-dot' }), h('span', { class: 'ghost-lines' }, bar(60), bar(35)), h('span', { class: 'ghost-pill' })));
  return h('div', { class: 'cups-panel' },
    h('div', { class: 'ghost', 'aria-hidden': 'true' }, sketch),
    h('div', { class: 'cups-copy' },
      h('h4', {}, icon('info', 18), title),
      h('p', {}, 'panda-print still shows this printer’s status, ink levels and page count without CUPS. CUPS is the print service that handles print jobs. Add it to unlock:'),
      h('ul', { class: 'unlocks' }, unlocks.map((u) => h('li', {}, icon('check', 15), u))),
      h('details', { class: 'more' }, h('summary', {}, icon('chevron', 16), 'How to set it up'),
        h('ol', { class: 'steps' },
          h('li', {}, 'Install CUPS on a computer that can reach the printer, such as a Raspberry Pi or a home server. On Debian, Ubuntu or Raspberry Pi OS:', h('pre', {}, 'sudo apt install cups')),
          h('li', {}, 'Add the printer to CUPS (its web page is ', h('code', {}, 'http://<that computer>:631'), ', under Administration).'),
          h('li', {}, 'If panda-print runs on a different machine, let CUPS accept it, and set the CUPS address under Printers → Edit → Advanced settings.'),
          h('li', {}, 'Go to Printers → Edit and choose the print queue. Everything above switches on.'))),
      h('a', { class: 'btn small primary', href: '#printers', title: 'Open the Printers page to choose the print queue' }, 'Choose a print queue')));
}
