// "Manage pages" dialog: the shared library of pages (upload, delete, preview) and this printer's order,
// on/off switches and rotation. Everything saves as you go; closing the dialog just refreshes the card behind it.

import { createPreview, drawnThumb, makeThumb, standardSvg } from './preview.js';

const bytes = (n) => (n >= 2 ** 20 ? `${(n / 2 ** 20).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);
const KIND = { 'application/pdf': 'PDF', 'image/png': 'PNG', 'image/jpeg': 'JPEG' };

export function createPagesDialog(kit, { base, onChange }) {
  const { h, icon, ui, api, toast } = kit;
  let data = null;      // this printer's rotation status (see Service.rotation_status)
  let armed = null;     // {id, kind: 'print' | 'delete'}: a row waiting for its "are you sure"
  let problems = [];    // upload messages to show under the Add button

  const preview = createPreview(kit);  // opens on top of this dialog
  const body = h('div', { class: 'dlg-body' });
  const dialog = h('dialog', { class: 'wide pages-dialog', 'aria-labelledby': 'pg-title' },
    h('h3', { id: 'pg-title' }, 'Pages to print'), body,
    h('div', { class: 'actions' }, h('button', { class: 'btn primary', type: 'button', title: 'Close this window', onclick: () => dialog.close() }, 'Done')),
    preview.dialog);
  dialog.addEventListener('close', () => onChange());
  dialog.addEventListener('dragover', (e) => { e.preventDefault(); dialog.classList.add('drag'); });
  dialog.addEventListener('dragleave', () => dialog.classList.remove('drag'));
  dialog.addEventListener('drop', (e) => { e.preventDefault(); dialog.classList.remove('drag'); upload([...e.dataTransfer.files]); });

  const picker = h('input', { type: 'file', multiple: true, hidden: true,
    accept: '.pdf,.png,.jpg,.jpeg,application/pdf,image/png,image/jpeg', onchange: () => { upload([...picker.files]); picker.value = ''; } });

  async function load() {
    const res = await api(`${base()}/rotation`);
    if (res.ok) data = res.data; else toast(res.error?.message || 'Could not load the pages.', 'bad');
    render();
  }

  async function saveOrder(items) {
    const res = await api(`${base()}/rotation`, { method: 'PUT', body: { items: items.map((i) => ({ id: i.id, enabled: i.enabled })) } });
    if (res.ok) data = res.data; else toast(res.error?.message || 'Could not save.', 'bad');
    render();
  }

  const move = (index, delta) => {
    const items = data.items.map((i) => ({ ...i }));
    [items[index], items[index + delta]] = [items[index + delta], items[index]];
    return saveOrder(items);
  };
  const toggle = (item, on) => saveOrder(data.items.map((i) => ({ ...i, enabled: i.id === item.id ? on : i.enabled })));

  async function upload(files) {
    problems = [];
    let added = 0;
    const limit = data.limits.max_file_mb * 2 ** 20;
    for (const file of files) {
      if (file.size > limit) { problems.push(`${file.name}: larger than ${data.limits.max_file_mb} MB.`); continue; }  // no point sending it
      const form = new FormData();
      form.append('file', file);
      const picture = await makeThumb(file);  // a small picture of the page, drawn here, so the list shows what will print
      if (picture) form.append('thumb', picture, 'thumb.png');
      const res = await api('/maintenance/pages', { method: 'POST', body: form });
      if (res.ok) added += 1; else problems.push(`${file.name}: ${res.error?.message || 'could not be added.'}`);
    }
    if (added) toast(`${added} page${added === 1 ? '' : 's'} added.`);
    await load();
  }

  async function printPage(item) {
    armed = null;
    const res = await api(`${base()}/nozzle/print`, { method: 'POST', body: { page: item.id } });
    if (res.ok) toast(`“${item.name}” sent to the printer (job #${res.data.job_id}).`);
    else toast(res.error?.message || 'Could not print.', 'bad');
    render();
    onChange();
  }

  async function deletePage(item) {
    armed = null;
    const res = await api(`/maintenance/pages/${item.id}`, { method: 'DELETE' });
    if (!res.ok) toast(res.error?.message || 'Could not delete.', 'bad');
    await load();
  }

  async function setPlainEvery(input, message) {
    const res = await api(`${base()}/automation`, { method: 'PUT', body: { plain_every: input.value } });
    if (!res.ok) { message.textContent = res.error?.fields?.plain_every || res.error?.message || 'Could not save.'; return; }
    message.textContent = '';
    await load();
  }

  // A real picture of the page on a sheet of paper: the standard page replica, the thumbnail made at upload time,
  // or (for a picture uploaded without one) the picture itself. Click it to preview.
  function thumb(item) {
    const file = `/api/maintenance/pages/${item.id}`;
    const inner = item.standard ? standardSvg(kit)
      : item.thumb ? h('img', { src: `${file}/thumb`, alt: '', loading: 'lazy', decoding: 'async' })
      : item.mime.startsWith('image/') ? h('img', { src: `${file}/file`, alt: '', loading: 'lazy', decoding: 'async' })
      : h('span', { class: 'docicon' }, icon('file', 22), h('b', {}, 'PDF'));
    const button = h('button', { class: 'paper', type: 'button', title: 'Preview', 'aria-label': `Preview ${item.name}`, onclick: () => preview.open(item) }, inner);
    if (!item.standard && !item.thumb && item.mime === 'application/pdf') {  // stored without a picture: draw it here
      drawnThumb(item).then((url) => { if (url) button.replaceChildren(h('img', { src: url, alt: '' })); });
    }
    return button;
  }

  function row(item, index) {
    const last = index === data.items.length - 1;
    const meta = item.standard ? 'Four colour bars, so every ink gets used'
      : `${KIND[item.mime] || ''} · ${item.pages} page${item.pages === 1 ? '' : 's'} · ${bytes(item.size)}`;
    const ask = armed?.id === item.id ? armed.kind : null;
    const confirm = (kind, text, go) => h('span', { class: 'confirm' }, h('span', {}, text),
      h('button', { class: `btn small${kind === 'delete' ? ' danger' : ' primary'}`, type: 'button', title: kind === 'delete' ? 'Delete this page for good' : 'Send this page to the printer now', onclick: go }, kind === 'delete' ? 'Delete' : 'Print'),
      h('button', { class: 'btn small', type: 'button', title: 'Do nothing', onclick: () => { armed = null; render(); } }, 'Cancel'));
    return h('li', { class: `pagecard${item.enabled ? '' : ' off'}` },
      thumb(item),
      h('div', { class: 'pagemain' },
        h('div', { class: 'pagename' }, item.name, data.rotate && data.next.id === item.id && ui.badge(data.next.plain ? 'Next up (plain turn)' : 'Next up', 'ok')),
        h('div', { class: 'pagemeta' }, meta)),
      ask === 'print' ? confirm('print', 'Print it now?', () => printPage(item))
        : ask === 'delete' ? confirm('delete', 'Delete this page?', () => deletePage(item))
        : h('div', { class: 'pageactions' },
          h('button', { class: 'icon-mini', type: 'button', 'aria-label': `Preview ${item.name}`, title: 'Preview', onclick: () => preview.open(item) }, icon('eye', 16)),
          item.standard
            ? h('button', { class: 'icon-mini', type: 'button', disabled: true, 'aria-label': 'The standard test page is built in, so there is no file to download', title: 'Built in: nothing to download' }, icon('download', 16))
            : h('a', { class: 'icon-mini', 'aria-label': `Download ${item.name}`, title: 'Download', download: '', href: `/api/maintenance/pages/${item.id}/file?download=1` }, icon('download', 16)),
          h('button', { class: 'icon-mini up', type: 'button', 'aria-label': `Move ${item.name} up`, title: 'Move up in the rotation', disabled: index === 0, onclick: () => move(index, -1) }, icon('chevron', 16)),
          h('button', { class: 'icon-mini', type: 'button', 'aria-label': `Move ${item.name} down`, title: 'Move down in the rotation', disabled: last, onclick: () => move(index, 1) }, icon('chevron', 16)),
          h('label', { class: 'switch compact', title: item.enabled ? 'In the rotation' : 'Left out of the rotation' },
            h('input', { type: 'checkbox', role: 'switch', 'aria-label': `Include ${item.name}`, checked: item.enabled ? true : null, onchange: (e) => toggle(item, e.target.checked) }),
            h('span', { class: 'track', 'aria-hidden': 'true' })),
          h('button', { class: 'btn small', type: 'button', title: 'Print just this page now (asks to confirm; the rotation order is not changed)', onclick: () => { armed = { id: item.id, kind: 'print' }; render(); } }, 'Print'),
          h('button', { class: 'btn small danger', type: 'button', disabled: item.standard ? true : null, title: item.standard ? 'Built in: it can’t be deleted' : 'Delete this page from the library (asks first)',
            onclick: () => { armed = { id: item.id, kind: 'delete' }; render(); } }, 'Delete')));
  }

  function render() {
    if (!data) { body.replaceChildren(ui.skeleton()); return; }
    const message = h('p', { class: 'msg', role: 'alert' });
    const every = h('input', { class: 'input mini', type: 'number', min: 0, max: 50, step: 1, value: data.plain_every, 'aria-label': 'Standard test page every N prints',
      onchange: (e) => setPlainEvery(e.target, message) });
    body.replaceChildren(...[
      h('p', { class: 'sub' }, 'With “Rotate my pages” on, each print takes the next page in this list, then loops. Pages are shared by every printer; the order and the switches here are for this printer only.'),
      !data.rotate && h('div', { class: 'notice info' }, icon('info', 18), h('div', {}, 'Rotation is off, so only the standard test page prints. Choose “Rotate my pages” on the Automation card to use this list.')),
      h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {},
        'A page only moves the inks it uses. A black-and-white page won’t exercise cyan, magenta or yellow, so keep a colourful page in the list, or print the standard test page every few prints (below).')),
      h('div', { class: 'addrow' },
        h('button', { class: 'btn primary', type: 'button', title: 'Add PDF, PNG or JPEG files to the library', onclick: () => picker.click() }, icon('file', 16), 'Add pages…'), picker,
        h('span', { class: 'hint' }, `PDF, PNG or JPEG, up to ${data.limits.max_file_mb} MB each (or drop files here).`)),
      problems.length > 0 && h('ul', { class: 'problems' }, problems.map((p) => h('li', { class: 'msg' }, p))),
      h('ul', { class: 'pagelist' }, data.items.map(row)),
      h('div', { class: 'plainrow' }, h('label', { for: undefined }, 'Also print the standard test page every'), every,
        h('span', { class: 'unit' }, 'prints (0 = never)'), message),
    ].filter(Boolean));  // (the native replaceChildren would print a stray "false")
  }

  return {
    dialog,
    async open() { armed = null; problems = []; data = null; render(); dialog.showModal(); await load(); },
  };
}
