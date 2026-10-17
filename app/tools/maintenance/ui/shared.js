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
