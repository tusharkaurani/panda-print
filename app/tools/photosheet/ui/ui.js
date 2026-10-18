// Passport Sheets: choose a photo, say how big each copy is and what paper it goes on, see the sheet, then download
// the PDF or print it at actual size. The layout is computed by the server (the same numbers make the PDF),
// and drawn here as a live preview.
import { drawPdfBytes } from '/tools/maintenance/preview.js';

const UNITS = { mm: { f: 1, dp: 1, step: 0.5 }, cm: { f: 10, dp: 2, step: 0.1 }, in: { f: 25.4, dp: 3, step: 0.05 } };
const KEY = 'pp-photosheet';
const MAX_SIDE = 7000;  // px: bigger pictures are scaled down before they are sent

export async function mount(root, kit) {
  const { h, icon, api, toast, ui } = kit;
  root.classList.add('ps');

  // ------------------------------------------------------------------ state
  const S = {
    unit: 'mm', photoW: 35, photoH: 45, margin: 5, gap: 2, orientation: 'auto', mode: 'fill', cols: 5, rows: 6, count: 12,
    fit: 'fill', area: 'full', marks: true, outline: false, paper: 'a4', customW: 210, customH: 297,
    mediaType: '', quality: '', resolution: '', copies: 1,
  };
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch { /* storage is optional */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch { /* storage is optional */ } };
  let images = [];        // the photos: {orig (ImageBitmap), width, height, name, share, jpegs: {quality: Blob}}; `share` is the ratio the person set
  let config = null;      // GET /photosheet/config
  let layout = null;      // the last good answer of POST /photosheet/layout
  let printer = null;     // this printer's choices, if CUPS is set up
  let token = 0;

  const mm = (v) => v * UNITS[S.unit].f;
  const shown = (v) => String(+(v / UNITS[S.unit].f).toFixed(UNITS[S.unit].dp));

  // ------------------------------------------------------------------ the paper list: the printer's, else ours
  const papers = () => (printer?.media?.length ? printer.media.map((m) => ({ ...m, id: m.keyword })) : (config?.papers || []).map((m) => ({ ...m, id: m.key })));
  function currentPaper() {
    if (S.paper === 'custom') return { id: 'custom', width: Math.min(S.customW, S.customH), height: Math.max(S.customW, S.customH), label: 'Custom size', borderless: false };
    const list = papers();
    return list.find((p) => p.id === S.paper) || list[0] || { id: 'a4', width: 210, height: 297, label: 'A4', borderless: false };
  }
  const minMargin = () => (printer?.min_margin ?? config?.fallback_min_margin ?? 3);

  // ------------------------------------------------------------------ small form helpers
  const inputs = {};
  function numberField(key, label, { min = 0, hint } = {}) {
    const el = h('input', { class: 'input', type: 'number', inputmode: 'decimal', min, step: 'any', id: `ps-${key}`, autocomplete: 'off',
      oninput: () => { const v = parseFloat(el.value); if (Number.isFinite(v)) { S[key] = mm(v); changed(); } } });
    inputs[key] = el;
    return h('div', { class: 'field' }, h('label', { for: `ps-${key}` }, label, ' (', h('span', { class: 'unit-name' }, S.unit), ')'), el, hint && h('p', { class: 'hint' }, hint));
  }
  function plainNumber(key, label, { min = 1, max = 99 } = {}) {
    const el = h('input', { class: 'input', type: 'number', min, max, step: 1, id: `ps-${key}`, value: S[key],
      oninput: () => { const v = parseInt(el.value, 10); if (Number.isFinite(v)) { S[key] = v; changed(); } } });
    inputs[key] = el;
    return h('div', { class: 'field' }, h('label', { for: `ps-${key}` }, label), el);
  }
  function select(key, label, options, { onchange, hint, id } = {}) {
    const el = h('select', { class: 'input', id: id || `ps-${key}`, onchange: () => { onchange ? onchange(el.value) : (S[key] = el.value); changed(); } });
    inputs[key] = el;
    fillSelect(el, options, S[key]);
    return h('div', { class: 'field' }, h('label', { for: id || `ps-${key}` }, label), el, hint && h('p', { class: 'hint' }, hint));
  }
  function fillSelect(el, options, value) {
    el.replaceChildren(...options.map(([v, text]) => h('option', { value: v, selected: String(v) === String(value) ? true : null }, text)));
  }
  const check = (key, label, hint) => {
    const el = h('input', { type: 'checkbox', id: `ps-${key}`, checked: S[key] ? true : null, onchange: () => { S[key] = el.checked; changed(); } });
    return h('label', { class: 'check-row', for: `ps-${key}` }, el, h('span', {}, h('strong', {}, label), hint && h('span', { class: 'hint' }, hint)));
  };

  // ------------------------------------------------------------------ 1. the photos
  const picker = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => { if (picker.files.length) loadImages([...picker.files]); picker.value = ''; } });
  const dropzone = h('button', { class: 'drop', type: 'button', title: 'Choose one or more edited photos (JPEG, PNG or WebP)', onclick: () => picker.click() },
    icon('image', 28), h('strong', {}, 'Choose your photos'), h('span', { class: 'hint' }, 'or drop them here. Use photos you already edited and cropped to your size. Pick several to share one sheet.'));
  const photoList = h('ul', { class: 'photo-list', hidden: true });
  const splitBox = h('div', { class: 'split-box', hidden: true });
  const addMore = h('button', { class: 'btn small', type: 'button', title: 'Add more photos to this sheet', hidden: true, onclick: () => picker.click() }, icon('image', 15), 'Add photos');
  const photoBox = h('div', { class: 'photo-box' }, dropzone, photoList, splitBox, addMore);
  for (const ev of ['dragover', 'dragenter']) photoBox.addEventListener(ev, (e) => { e.preventDefault(); photoBox.classList.add('over'); });
  for (const ev of ['dragleave', 'drop']) photoBox.addEventListener(ev, () => photoBox.classList.remove('over'));
  photoBox.addEventListener('drop', (e) => { e.preventDefault(); const f = [...(e.dataTransfer?.files || [])]; if (f.length) loadImages(f); });

  const MAX_PHOTOS = 20;
  async function loadImages(files) {
    for (const file of files) {
      if (images.length >= MAX_PHOTOS) { toast(`At most ${MAX_PHOTOS} different photos fit on one sheet.`, 'bad'); break; }
      if (!file.type.startsWith('image/')) { toast(`${file.name} is not a picture.`, 'bad'); continue; }
      let bitmap;
      try { bitmap = await createImageBitmap(file); } catch { toast(`This browser can’t read ${file.name}. Try a JPEG or PNG.`, 'bad'); continue; }
      const k = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
      const width = Math.round(bitmap.width * k), height = Math.round(bitmap.height * k);
      // a new photo starts with an average share, so it gets an average number of places
      const avg = images.length ? Math.max(1, Math.round(images.reduce((n, i) => n + i.share, 0) / images.length)) : 1;
      images.push({ orig: bitmap, width, height, name: file.name, share: avg, fit: S.fit, cropX: 0.5, cropY: 0.5, open: false, jpegs: {} });
    }
    paintPhotos();
    changed();
  }
  // Each photo is sent as a JPEG. All of them together must fit the upload limit, so quality steps down only if it must.
  async function encode(img, q) {
    if (!img.jpegs[q]) {
      const c = document.createElement('canvas');
      c.width = img.width; c.height = img.height;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, c.width, c.height);  // transparent areas print white
      ctx.drawImage(img.orig, 0, 0, c.width, c.height);
      img.jpegs[q] = await new Promise((res) => c.toBlob(res, 'image/jpeg', q));
    }
    return img.jpegs[q];
  }
  async function jpegs() {
    const limit = (config?.limits?.max_mb || 10) * 2 ** 20 - 96 * 1024;
    for (const q of [0.95, 0.9, 0.85, 0.8, 0.7, 0.6, 0.5]) {
      const blobs = await Promise.all(images.map((i) => encode(i, q)));
      if (blobs.every(Boolean) && blobs.reduce((n, b) => n + b.size, 0) <= limit) return blobs;
    }
    return null;
  }
  // The total on the sheet is fixed by the layout (how many photos fit). Each photo's number can be set; the others
  // give or take so the total stays the same and they keep the ratio they had between themselves.
  const counts = () => (layout?.counts && layout.counts.length === images.length ? layout.counts : null);
  let queue = Promise.resolve();  // edits are applied one after another, each from the answer to the one before
  function pin(index, count) {
    queue = queue.then(async () => { await refreshLayout({ pin: { index, count } }); });
  }
  function setCounts(next) {  // an explicit split, as when a handle is dragged
    queue = queue.then(async () => { await refreshLayout({ counts: next }); });
  }
  const palette = (i) => `c${i % 8}`;

  let paintedSig = '';
  const sigOf = () => JSON.stringify([counts(), images.length, S.photoW, S.photoH, images.map((i) => i.fit), layout?.count]);
  function paintPhotosIfNeeded() { if (sigOf() !== paintedSig) paintPhotos(); }
  function paintPhotos() {
    paintedSig = sigOf();
    const some = images.length > 0;
    dropzone.hidden = some; photoList.hidden = !some; addMore.hidden = !some;
    photoList.replaceChildren(...images.map((img, i) => {
      const thumb = document.createElement('canvas');
      thumb.width = 44; thumb.height = Math.max(1, Math.round(44 * img.height / img.width));
      thumb.getContext('2d').drawImage(img.orig, 0, 0, thumb.width, thumb.height);
      const main = h('div', { class: 'photo-main' }, thumb,
        h('div', { class: 'photo-meta' }, h('strong', { title: img.name }, img.name), h('span', { class: 'hint' }, `${img.width} × ${img.height} px`)),
        images.length > 1 ? stepper(i, img) : null,
        h('button', { class: 'icon-mini', type: 'button', 'aria-label': `Remove ${img.name}`, title: 'Remove this photo', onclick: () => { images.splice(i, 1); paintPhotos(); changed(); } }, icon('x', 16)));
      const row = h('li', { class: `photo-row ${images.length > 1 ? palette(i) : ''}` }, main, adjustPanel(img));
      return row;
    }));
    paintSplit();
  }

  // Crop and fit belong to each photo: one may need its sides trimmed while another already has the right shape.
  function adjustPanel(img) {
    const shape = Math.abs(img.width / img.height - S.photoW / S.photoH) > 0.01;  // does this photo have a different shape from the frame?
    const fitSel = h('select', { class: 'input', 'aria-label': `If ${img.name} has a different shape`, title: 'Crop the photo to the frame, or show all of it with white edges',
      onchange: () => { img.fit = fitSel.value; paintPhotos(); changed(); } },
      [['fill', 'Crop it to the frame'], ['fit', 'Show all of it (white edges)']].map(([v, t]) => h('option', { value: v, selected: img.fit === v ? true : null }, t)));
    const slider = (key, label, hint) => {
      const r = h('input', { type: 'range', min: 0, max: 100, value: Math.round(img[key] * 100), 'aria-label': `${hint} for ${img.name}`, title: hint,
        oninput: () => { img[key] = r.value / 100; changed(); } });
      return h('label', { class: 'range' }, h('span', {}, label), r);
    };
    const note = img.fit === 'fit' ? 'The whole photo is shown, with white edges where it doesn’t fill the frame.'
      : shape ? 'The photo is cropped to the frame. Move it in its frame:' : 'This photo already has the shape of the frame, so nothing is cropped.';
    const d = h('details', { class: 'adjust', open: img.open ? true : null, ontoggle: () => { img.open = d.open; } },
      h('summary', {}, icon('chevron', 16), 'Crop and fit', h('span', { class: 'hint' }, img.fit === 'fit' ? ' · shown whole' : shape ? ' · cropped' : ' · right shape')),
      h('div', { class: 'adjust-body' }, fitSel, h('p', { class: 'hint' }, note),
        img.fit === 'fill' && shape ? slider('cropX', 'Left / right', 'Move the photo left or right in its frame') : null,
        img.fit === 'fill' && shape ? slider('cropY', 'Up / down', 'Move the photo up or down in its frame') : null,
        images.length > 1 ? h('button', { class: 'btn small', type: 'button', title: 'Give every photo this one’s crop and fit',
          onclick: () => { for (const o of images) { o.fit = img.fit; o.cropX = img.cropX; o.cropY = img.cropY; } paintPhotos(); changed(); } }, 'Use for all photos') : null));
    return d;
  }

  function stepper(i, img) {
    const n = counts()?.[i];
    const total = layout?.count || 0;
    const low = total >= images.length ? 1 : 0;
    const input = h('input', { class: 'input mini', type: 'number', min: low, max: Math.max(low, total - low * (images.length - 1)), step: 1, value: n ?? '', 'data-i': i,
      'aria-label': `Copies of ${img.name}`, title: 'How many of this photo are on the sheet. The others change so the total stays the same.',
      onchange: () => { const v = parseInt(input.value, 10); if (Number.isFinite(v)) pin(i, v); else paintPhotos(); } });
    const btn = (text, delta, label) => h('button', { class: 'icon-mini', type: 'button', 'aria-label': `${label} ${img.name}`, title: `${label}; another photo gives one up so the total stays ${total}`,
      disabled: n == null || (delta < 0 ? n <= low : n >= total - low * (images.length - 1)), onclick: () => pin(i, n + delta) }, text);
    return h('div', { class: 'stepper' }, btn('−', -1, 'One fewer of'), input, btn('+', 1, 'One more of'));
  }

  // One bar for the whole sheet, a section per photo. Drag the handle between two sections to move places from one photo
  // to its neighbour (the total never changes); arrow keys do the same one place at a time.
  function paintSplit() {
    const c = counts();
    splitBox.hidden = images.length < 2 || !c;
    if (splitBox.hidden) return;
    const total = c.reduce((a, b) => a + b, 0);
    const low = total >= images.length ? 1 : 0;
    const bar = h('div', { class: 'splitbar', role: 'group', 'aria-label': 'How the sheet is shared between the photos' });
    c.forEach((n, i) => {
      bar.append(h('div', { class: `seg ${palette(i)}`, 'data-i': i, title: `${images[i].name}: ${n}` }, h('span', {}, n >= 2 || total <= 30 ? String(n) : '')));
      bar.lastChild.style.setProperty('flex-grow', String(Math.max(n, 0.0001)));
      if (i < c.length - 1) {
        const handle = h('button', { class: 'handle', type: 'button', title: 'Drag to move places from one photo to the next', role: 'slider', 'aria-label': `Move places between ${images[i].name} and ${images[i + 1].name}`,
          'aria-valuemin': 0, 'aria-valuemax': total, 'aria-valuenow': c.slice(0, i + 1).reduce((a, b) => a + b, 0) });
        const move = (next) => { const r = [...c]; const d = next - c.slice(0, i + 1).reduce((a, b) => a + b, 0); r[i] += d; r[i + 1] -= d;
          if (r[i] >= low && r[i + 1] >= low && d !== 0) setCounts(r); };
        handle.addEventListener('keydown', (e) => {
          const here = c.slice(0, i + 1).reduce((a, b) => a + b, 0);
          if (e.key === 'ArrowRight' || e.key === 'ArrowUp') { e.preventDefault(); move(here + 1); }
          if (e.key === 'ArrowLeft' || e.key === 'ArrowDown') { e.preventDefault(); move(here - 1); }
        });
        handle.addEventListener('pointerdown', (e) => {
          e.preventDefault(); try { handle.setPointerCapture(e.pointerId); } catch { /* the move listeners still work */ } handle.classList.add('drag');
          const rect = bar.getBoundingClientRect(), before = c.slice(0, i).reduce((a, b) => a + b, 0), after = before + c[i] + c[i + 1];
          const onMove = (ev) => {
            const edge = Math.round(((ev.clientX - rect.left) / rect.width) * total);
            const next = Math.max(before + low, Math.min(after - low, edge));
            const r = [...c]; r[i] = next - before; r[i + 1] = after - next;
            bar.querySelectorAll('.seg').forEach((el, k) => { el.style.setProperty('flex-grow', String(Math.max(r[k], 0.0001))); el.firstChild.textContent = r[k] >= 2 || total <= 30 ? String(r[k]) : ''; });
            handle.dataset.next = JSON.stringify(r);
          };
          const onUp = () => {
            handle.removeEventListener('pointermove', onMove); handle.removeEventListener('pointerup', onUp); handle.classList.remove('drag');
            if (handle.dataset.next) { const r = JSON.parse(handle.dataset.next); delete handle.dataset.next; if (r.join() !== c.join()) setCounts(r); }
          };
          handle.addEventListener('pointermove', onMove); handle.addEventListener('pointerup', onUp);
        });
        bar.append(handle);
      }
    });
    splitBox.replaceChildren(h('div', { class: 'hint' }, `${total} places on the sheet. Drag the dividers, or use + and −: the total stays ${total}.`), bar);
  }

  // ------------------------------------------------------------------ 2. photo size
  const unitSelect = h('select', { class: 'input unit-pick', 'aria-label': 'Unit of length', title: 'Millimetres, centimetres or inches',
    onchange: () => { S.unit = unitSelect.value; syncFields(); changed(); } },
    ['mm', 'cm', 'in'].map((u) => h('option', { value: u, selected: u === S.unit ? true : null }, u)));
  const presetSelect = h('select', { class: 'input', id: 'ps-preset', 'aria-label': 'Common photo sizes', onchange: () => {
    const p = config?.presets?.[Number(presetSelect.value)];
    if (p) { S.photoW = p.width; S.photoH = p.height; syncFields(); changed(); }
  } });
  // The "Size" list always shows the common size that matches the width and height, or the prompt when none does.
  function syncSizePick() {
    const list = config?.presets || [];
    const at = list.findIndex((p) => Math.abs(p.width - S.photoW) < 0.05 && Math.abs(p.height - S.photoH) < 0.05);
    presetSelect.value = at >= 0 ? String(at) : '';
  }
  function fillPresets() {
    presetSelect.replaceChildren(h('option', { value: '' }, 'Choose a common size…'),
      ...(config?.presets || []).map((p, i) => h('option', { value: i }, p.label)));
    syncSizePick();
  }

  // ------------------------------------------------------------------ 3. paper and layout
  const paperField = h('div', { class: 'field' });
  const paperSelect = h('select', { class: 'input', id: 'ps-paper', onchange: () => { S.paper = paperSelect.value; syncPaper(); changed(); } });
  const customRow = h('div', { class: 'two', hidden: true });
  const areaNote = h('p', { class: 'notice info', hidden: true });
  const modeField = h('div', { class: 'field' });
  const gridRow = h('div', { class: 'two', hidden: true });
  const countRow = h('div', { class: 'two', hidden: true });

  function syncPaper() {
    const list = papers();
    paperSelect.replaceChildren(...list.map((p) => h('option', { value: p.id }, p.label)), h('option', { value: 'custom' }, 'Custom size…'));
    if (S.paper !== 'custom' && !list.some((p) => p.id === S.paper)) S.paper = list.find((p) => p.keyword === printer?.default_media)?.id || list[0]?.id || 'a4';
    paperSelect.value = S.paper;
    customRow.hidden = S.paper !== 'custom';
  }
  function syncArea() {
    const part = S.area !== 'full';
    if (inputs.orientation) { inputs.orientation.disabled = part; inputs.orientation.value = S.orientation; }
    areaNote.hidden = !part;
    if (!part) { areaNote.replaceChildren(); return; }
    const other = { top: 'bottom', bottom: 'top' }[S.area];
    areaNote.replaceChildren(icon('info', 18), h('div', {}, `Only the ${S.area} half is printed; the rest stays blank. To use the ${other} half later, choose “${other[0].toUpperCase()}${other.slice(1)} half only” and put the sheet back in the tray the same way it went in (let the ink dry first). The sheet is not turned, so “Sheet direction” is fixed.`));
  }
  function syncMode() {
    gridRow.hidden = S.mode !== 'grid';
    countRow.hidden = S.mode !== 'count';
  }
  function syncFields() {
    for (const k of ['photoW', 'photoH', 'margin', 'gap', 'customW', 'customH']) if (inputs[k]) inputs[k].value = shown(S[k]);
    for (const el of document.querySelectorAll('.ps .unit-name')) el.textContent = S.unit;
  }

  // ------------------------------------------------------------------ 4. printing choices (only with CUPS)
  const printBox = h('section', { class: 'card ps-card' });
  function syncPrinter() {
    const p = kit.printer.current;
    if (!p) { printBox.replaceChildren(sectionHead('printer', 'Printing'), h('p', { class: 'hint' }, 'Add a printer to print from here. You can still download the PDF.')); return; }
    if (!printer || printer.configured === false) {
      printBox.replaceChildren(sectionHead('printer', 'Printing'),
        h('p', { class: 'hint' }, `${p.name} has no CUPS print queue, so it can’t print from here. Download the PDF and print it from your computer, or set up CUPS and choose the queue under Printers → Edit.`));
      return;
    }
    if (printer.error) {
      printBox.replaceChildren(sectionHead('printer', 'Printing'), ui.errorBox(printer.error, loadConfig));
      return;
    }
    const types = [['', 'Printer’s own choice'], ...printer.media_types.map((t) => [t.value, t.label])];
    const qualities = [['', 'Printer’s own choice'], ...printer.qualities.map((q) => [q.value, q.label])];
    const res = printer.resolutions;
    printBox.replaceChildren(sectionHead('printer', `Print on ${p.name}`),
      h('div', { class: 'grid2' },
        select('mediaType', 'Paper type', types, { hint: 'Set this to the paper in the tray.' }),
        select('quality', 'Print quality', qualities, { hint: 'High uses more ink and time.' }),
        res.length > 1 ? select('resolution', 'Resolution', [['', 'Printer’s own choice'], ...res.map((r) => [r.value.join('x'), r.label])])
          : h('div', { class: 'field' }, h('label', {}, 'Resolution'), h('p', { class: 'static' }, res[0]?.label || 'Set by the printer'),
              h('p', { class: 'hint' }, res.length ? 'The only resolution this printer offers.' : '')),
        plainNumber('copies', 'Copies of the sheet', { min: 1, max: 20 })),
      printer.actual_size ? h('p', { class: 'hint' }, 'Printed at actual size (100%), so each photo comes out at the size you chose. Turn off “fit to page” in any other print dialog.')
        : h('p', { class: 'msg' }, 'This print queue can’t print at actual size, so printing is switched off. Download the PDF instead.'));
  }
  const sectionHead = (name, text) => h('h3', { class: 'ps-head' }, icon(name, 18), text);

  // ------------------------------------------------------------------ presets: saved choices (not the photos)
  let presets = [];
  const presetChips = h('div', { class: 'chips' });
  const presetBox = h('section', { class: 'card ps-card' }, h('h3', { class: 'ps-head' }, icon('list', 18), 'Presets'),
    h('p', { class: 'hint' }, 'A preset fills in the settings below, as a quick start. Change anything afterwards; nothing is saved unless you save it as a new preset.'), presetChips,
    h('div', { class: 'actions left' }, h('button', { class: 'btn small', type: 'button', title: 'Save the photo size, paper and other choices below (not the photos) as a new preset to use again', onclick: () => openSave() }, icon('check', 15), 'Save these settings as a preset…')));

  function describe(p) {
    const x = p.settings;
    const u = (v) => `${+(v / UNITS[x.unit || 'mm'].f).toFixed(UNITS[x.unit || 'mm'].dp)}`;
    const how = x.mode === 'grid' ? `${x.cols} × ${x.rows}` : x.mode === 'count' ? `${x.count} photos` : 'fill the page';
    const part = x.area && x.area !== 'full' ? `${x.area} half only` : 'whole page';
    return `${u(x.photo.width)} × ${u(x.photo.height)} ${x.unit || 'mm'} · ${x.paper.label || `${x.paper.width} × ${x.paper.height} mm`} · ${part} · ${how}`;
  }
  function paintPresets() {
    presetChips.replaceChildren(...[...presets.map((p) => h('span', { class: `chip-wrap${p.builtin ? '' : ' mine'}` },
        h('button', { class: 'chip', type: 'button', title: describe(p), onclick: () => applyPreset(p) }, p.name),
        p.builtin ? null : h('button', { class: 'chip-x', type: 'button', 'aria-label': `Delete the preset ${p.name}`, title: 'Delete this preset (asks first)', onclick: () => confirmDelete(p) }, icon('x', 12)))),
      presets.length ? null : h('span', { class: 'hint' }, 'No presets yet. Set things up, then save them here.')].filter(Boolean));  // (a native replaceChildren would print "null")
  }
  async function loadPresets() {
    const res = await api('/photosheet/presets');
    presets = res.ok ? res.data.presets : [];
    paintPresets();
  }

  // the choices as a preset stores them (lengths in mm; print options only when chosen)
  function presetSettings() {
    const paper = currentPaper();
    const out = { photo: { width: S.photoW, height: S.photoH }, unit: S.unit,
      paper: { width: paper.width, height: paper.height, ...(paper.keyword ? { media: paper.keyword } : {}), label: paper.label },
      orientation: S.orientation, area: S.area, margin: S.margin, gap: S.gap, mode: S.mode, align: S.align, fit: images[0]?.fit || S.fit, marks: S.marks, outline: S.outline, copies: Number(S.copies) || 1 };
    if (S.mode === 'grid') { out.cols = S.cols; out.rows = S.rows; }
    if (S.mode === 'count') out.count = S.count;
    if (S.mediaType) out.media_type = S.mediaType;
    if (S.quality) out.quality = Number(S.quality);
    if (S.resolution) out.resolution = S.resolution.split('x').map(Number);
    return out;
  }
  function syncAll() {  // put every form control in step with the state
    unitSelect.value = S.unit;
    for (const k of ['orientation', 'align', 'fit', 'mode']) if (inputs[k]) inputs[k].value = S[k];
    for (const k of ['cols', 'rows', 'count']) if (inputs[k]) inputs[k].value = S[k];
    for (const k of ['marks', 'outline']) { const el = document.getElementById(`ps-${k}`); if (el) el.checked = Boolean(S[k]); }
    if (inputs.area) inputs.area.value = S.area;
    syncFields(); syncMode(); syncArea(); syncPaper(); syncPrinter();
  }
  const controlState = () => new Map([...root.querySelectorAll('input[id], select[id]')].map((el) => [el.id, el.type === 'checkbox' ? el.checked : el.value]));
  function applyPreset(p) {
    const x = p.settings;
    const before = controlState();
    Object.assign(S, { photoW: x.photo.width, photoH: x.photo.height, unit: x.unit || 'mm', orientation: x.orientation || 'auto', area: ['top', 'bottom'].includes(x.area) ? x.area : 'full', margin: x.margin ?? 5, gap: x.gap ?? 2,
      mode: x.mode || 'fill', align: x.align || 'center', fit: x.fit || 'fill', marks: x.marks !== false, outline: Boolean(x.outline), copies: x.copies || 1 });
    if (x.cols) S.cols = x.cols;
    if (x.rows) S.rows = x.rows;
    if (x.count) S.count = x.count;
    const list = papers();
    const found = list.find((m) => x.paper.media && m.keyword === x.paper.media) || list.find((m) => Math.abs(m.width - x.paper.width) < 0.6 && Math.abs(m.height - x.paper.height) < 0.6);
    if (found) S.paper = found.id; else { S.paper = 'custom'; S.customW = x.paper.width; S.customH = x.paper.height; }
    // print choices only if this printer offers them
    S.mediaType = printer?.media_types?.some((t) => t.value === x.media_type) ? x.media_type : '';
    S.quality = printer?.qualities?.some((q) => q.value === x.quality) ? String(x.quality) : '';
    S.resolution = x.resolution && printer?.resolutions?.some((r) => r.value.join() === x.resolution.join()) ? x.resolution.join('x') : '';
    for (const img of images) { img.fit = S.fit; img.cropX = 0.5; img.cropY = 0.5; }  // a preset's fit applies to every photo, centred
    paintPhotos();
    syncAll();
    changed();
    // show what the preset filled in: the controls whose value changed light up for a moment
    const after = controlState();
    for (const [id, value] of after) {
      if (before.get(id) !== value) { const f = document.getElementById(id)?.closest('.field, .check-row'); if (f) { f.classList.remove('flash'); void f.offsetWidth; f.classList.add('flash'); setTimeout(() => f.classList.remove('flash'), 1600); } }
    }
    toast(`Filled in the settings from “${p.name}”.`);
  }

  const saveName = h('input', { class: 'input', maxlength: 60, placeholder: 'For example: 35 × 45 mm on A4', id: 'ps-preset-name', 'aria-label': 'Preset name', oninput: () => { saveNote(); } });
  const saveMsg = h('p', { class: 'msg', role: 'alert' });
  const saveReplace = h('p', { class: 'hint' });
  const saveGo = h('button', { class: 'btn primary', type: 'button', title: 'Save the preset', onclick: () => saveAs(saveName.value, true) }, 'Save preset');
  const saveDialog = h('dialog', { 'aria-labelledby': 'ps-save-title' }, h('h3', { id: 'ps-save-title' }, 'Save as a preset'),
    h('p', {}, 'Keeps the photo size, paper, margins, layout, finishing and print choices. The photos themselves are not saved.'), saveName, saveReplace, saveMsg,
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Close without saving', onclick: () => saveDialog.close() }, 'Cancel'), saveGo));
  function saveNote() {
    saveMsg.textContent = '';
    const clash = presets.find((p) => !p.builtin && p.name.toLowerCase() === saveName.value.trim().toLowerCase());
    saveReplace.textContent = clash ? `A preset called “${clash.name}” already exists. Saving replaces it.` : '';
  }
  function openSave() {
    saveName.value = `${shown(S.photoW)} × ${shown(S.photoH)} ${S.unit} on ${currentPaper().label}`.slice(0, 60);
    saveNote();
    saveDialog.showModal();
    saveName.select();
  }
  async function saveAs(name, fromDialog = false) {
    const res = await api('/photosheet/presets', { method: 'POST', body: { name, settings: presetSettings() } });
    if (!res.ok) { if (fromDialog) saveMsg.textContent = res.error?.message || 'Could not save.'; else toast(res.error?.message || 'Could not save.', 'bad'); return; }
    if (fromDialog) saveDialog.close();
    toast(res.data.replaced ? `Preset “${res.data.name}” updated.` : `Preset “${res.data.name}” saved.`);
    await loadPresets();
  }
  const delDialog = h('dialog', { 'aria-labelledby': 'ps-del-title' }, h('h3', { id: 'ps-del-title' }, 'Delete this preset?'), h('p', { id: 'ps-del-text' }),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Keep the preset', onclick: () => delDialog.close() }, 'Keep it'),
      h('button', { class: 'btn primary danger-fill', type: 'button', id: 'ps-del-go', title: 'Delete the preset for good' }, 'Delete')));
  function confirmDelete(p) {
    document.getElementById('ps-del-text').textContent = `“${p.name}” will be removed for everyone who uses this panda-print. Your photos and other presets are not affected.`;
    document.getElementById('ps-del-go').onclick = async () => {
      delDialog.close();
      const res = await api(`/photosheet/presets/${p.id}`, { method: 'DELETE' });
      if (!res.ok) { toast(res.error?.message || 'Could not delete.', 'bad'); return; }
      toast('Preset deleted.');
      await loadPresets();
    };
    delDialog.showModal();
  }
  // ------------------------------------------------------------------ assemble the form
  const form = h('div', { class: 'ps-form' },
    presetBox,
    h('section', { class: 'card ps-card' }, sectionHead('image', 'Your photos'), photoBox, picker),
    h('section', { class: 'card ps-card' }, sectionHead('hash', 'Photo size'),
      h('div', { class: 'field' }, h('label', { for: 'ps-preset' }, 'Size'), presetSelect),
      h('div', { class: 'three' }, numberField('photoW', 'Width'), numberField('photoH', 'Height'),
        h('div', { class: 'field' }, h('label', { for: 'ps-unit' }, 'Unit'), unitSelect)),
      h('p', { class: 'hint' }, 'Check your country’s current rules for the size and background. These are common sizes, not official advice.')),
    h('section', { class: 'card ps-card' }, sectionHead('file', 'Paper and layout'),
      h('div', { class: 'field' }, h('label', { for: 'ps-paper' }, 'Paper'), paperSelect,
        h('p', { class: 'hint', id: 'ps-paper-hint' }, 'The sizes this printer offers, when a printer with CUPS is selected.')),
      customRow,
      h('div', { class: 'two' }, select('orientation', 'Sheet direction', [['auto', 'Automatic (fits the most)'], ['portrait', 'Portrait'], ['landscape', 'Landscape']]),
        select('align', 'Placed', [['center', 'Centred on the paper'], ['start', 'Top left']])),
      select('area', 'Print on', [['full', 'The whole page'], ['top', 'Top half only'], ['bottom', 'Bottom half only']],
        { onchange: (v) => { S.area = v; if (v !== 'full') S.orientation = 'auto'; syncArea(); }, id: 'ps-area',
          hint: 'A half leaves the rest of the sheet blank, so you can use it later (the top half of A4 is an A5 area).' }),
      areaNote,
      h('div', { class: 'two' }, numberField('margin', 'Margin around the photos'), numberField('gap', 'Space between photos')),
      select('mode', 'How many', [['fill', 'Fill the page'], ['grid', 'A grid (columns × rows)'], ['count', 'A number of photos']], { onchange: (v) => { S.mode = v; syncMode(); } }),
      gridRow, countRow),
    h('section', { class: 'card ps-card' }, sectionHead('check', 'Finishing'),
      check('marks', 'Cut marks', 'Small ticks beside the grid to guide the scissors or cutter.'),
      check('outline', 'Thin outline around each photo', 'Easier to cut along.'),
      h('p', { class: 'hint' }, 'Cropping and fitting are set for each photo, under “Crop and fit” in its row above.')),
    printBox);

  // the grid, count and crop controls are built once and shown or hidden
  gridRow.append(plainNumber('cols', 'Columns'), plainNumber('rows', 'Rows'));
  countRow.append(plainNumber('count', 'Number of photos', { max: 600 }));
  customRow.append(numberField('customW', 'Paper width'), numberField('customH', 'Paper height'));

  // ------------------------------------------------------------------ the preview
  const canvas = h('canvas', { class: 'sheet-canvas', role: 'img', 'aria-label': 'Preview of the sheet' });
  const summary = h('p', { class: 'summary', 'aria-live': 'polite' });
  const warnings = h('div', { class: 'warns' });
  const downloadBtns = [], printBtns = [];
  const actionRow = (extra = '') => {
    const d = h('button', { class: 'btn', type: 'button', title: 'Download the sheet as a PDF to print yourself', disabled: true, onclick: () => download() }, icon('download', 16), 'Download PDF');
    const p = h('button', { class: 'btn primary', type: 'button', title: 'Show the sheet, then print it on the selected printer', disabled: true, onclick: () => openPrint() }, icon('printer', 16), 'Preview and print…');
    downloadBtns.push(d); printBtns.push(p);
    return h('div', { class: `actions left ${extra}` }, p, d);
  };
  const side = h('aside', { class: 'ps-side' }, h('div', { class: 'sticky-col' },
    h('div', { class: 'card ps-card preview-card' }, h('div', { class: 'sheet-wrap' }, canvas), summary, warnings),
    actionRow('wide-only center')));  // the buttons: centred, below the preview card
  form.append(h('section', { class: 'card ps-card narrow-only' }, sectionHead('printer', 'Make the sheet'), actionRow()));
  root.append(h('div', { class: 'ps-layout' }, form, side));

  function cover(iw, ih, c, fit, cx, cy) {  // where the picture sits inside a cell (mm); the same rule as the PDF writer
    const s = fit === 'fit' ? Math.min(c.w / iw, c.h / ih) : Math.max(c.w / iw, c.h / ih);
    const dw = iw * s, dh = ih * s;
    return { dw, dh, x: c.x + (c.w - dw) * (dw > c.w ? cx : 0.5), y: c.y + (c.h - dh) * (dh > c.h ? cy : 0.5) };
  }
  function drawPreview() {
    const ratio = window.devicePixelRatio || 1;
    const L = layout;
    const pageW = L ? L.page_w : currentPaper().width, pageH = L ? L.page_h : currentPaper().height;
    const slim = window.matchMedia('(max-width: 900px)').matches;  // a phone: the preview is a slim strip above the settings
    const maxH = slim ? Math.max(150, window.innerHeight * 0.28) : Math.max(240, window.innerHeight - 400);
    // the width the canvas may use is the box's content width (its padding is not room for the sheet), or a wide sheet is squeezed
    const box = canvas.parentElement, pad = typeof getComputedStyle === 'function' ? getComputedStyle(box) : null;
    const inner = (box.clientWidth || 0) - (pad ? (parseFloat(pad.paddingLeft) || 0) + (parseFloat(pad.paddingRight) || 0) : 0);
    let cssW = Math.min(inner > 0 ? inner : 360, 460);
    if ((pageH * cssW) / pageW > maxH) cssW = (maxH * pageW) / pageH;  // never taller than the screen allows
    const k = cssW / pageW;
    canvas.width = Math.round(cssW * ratio); canvas.height = Math.round(pageH * k * ratio);
    canvas.style.setProperty('width', `${cssW}px`); canvas.style.setProperty('height', `${pageH * k}px`);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio * k, 0, 0, ratio * k, 0, 0);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, pageW, pageH);
    if (L && L.area !== 'full' && L.region) {  // the part left blank (only in this preview; the PDF has nothing there)
      const R = L.region;
      ctx.fillStyle = 'rgba(100, 116, 139, 0.10)';
      const rects = { top: [0, R.h, L.page_w, L.page_h - R.h], bottom: [0, 0, L.page_w, R.y] }[L.area];
      ctx.fillRect(...rects);
      ctx.save(); ctx.setLineDash([2, 1.5]); ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 0.4; ctx.strokeRect(R.x, R.y, R.w, R.h); ctx.restore();
      ctx.fillStyle = '#64748b'; ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('stays blank', rects[0] + rects[2] / 2, rects[1] + rects[3] / 2); ctx.textAlign = 'start';
    }
    if (!L) return;
    ctx.lineWidth = 0.25; ctx.strokeStyle = '#444';
    for (const [n, c] of L.cells.entries()) {
      ctx.save();
      ctx.beginPath(); ctx.rect(c.x, c.y, c.w, c.h); ctx.clip();
      const img = images[L.assign ? L.assign[n] : 0] || images[0];
      if (img) { const p = cover(img.width, img.height, c, img.fit, img.cropX, img.cropY); ctx.drawImage(img.orig, p.x, p.y, p.dw, p.dh); }
      else { ctx.fillStyle = '#e8ecf0'; ctx.fillRect(c.x, c.y, c.w, c.h); ctx.fillStyle = '#c4ccd6'; ctx.beginPath(); ctx.arc(c.x + c.w / 2, c.y + c.h * 0.4, c.w * 0.18, 0, 7); ctx.fill();
        ctx.beginPath(); ctx.ellipse(c.x + c.w / 2, c.y + c.h * 0.95, c.w * 0.36, c.h * 0.3, 0, 0, 7); ctx.fill(); }
      ctx.restore();
      if (S.outline) { ctx.strokeStyle = '#999'; ctx.strokeRect(c.x, c.y, c.w, c.h); }
    }
    if (S.marks && L.cells.length) {
      const g = L.grid, tick = 3, off = 1;
      const xs = [...new Set(L.cells.flatMap((c) => [c.x, c.x + c.w]))], ys = [...new Set(L.cells.flatMap((c) => [c.y, c.y + c.h]))];
      ctx.strokeStyle = '#333'; ctx.beginPath();
      for (const x of xs) {
        if (g.y - off - tick >= 0) { ctx.moveTo(x, g.y - off - tick); ctx.lineTo(x, g.y - off); }
        if (g.y + g.h + off + tick <= L.page_h) { ctx.moveTo(x, g.y + g.h + off); ctx.lineTo(x, g.y + g.h + off + tick); }
      }
      for (const y of ys) {
        if (g.x - off - tick >= 0) { ctx.moveTo(g.x - off - tick, y); ctx.lineTo(g.x - off, y); }
        if (g.x + g.w + off + tick <= L.page_w) { ctx.moveTo(g.x + g.w + off, y); ctx.lineTo(g.x + g.w + off + tick, y); }
      }
      ctx.stroke();
    }
  }

  // ------------------------------------------------------------------ settings -> server
  function settings() {
    const paper = currentPaper();
    return {
      paper: { width: paper.width, height: paper.height }, photo: { width: S.photoW, height: S.photoH },
      margin: S.margin, gap: S.gap, mode: S.mode, cols: S.cols, rows: S.rows, count: S.count, orientation: S.orientation,
      align: S.align, area: S.area, fit: S.fit, marks: S.marks, outline: S.outline,
      photos: images.length ? images.map((i) => ({ fit: i.fit, crop_x: i.cropX, crop_y: i.cropY })) : undefined,
      min_margin: minMargin(), borderless: Boolean(paper.borderless),
      counts: images.length ? images.map((i) => i.share) : undefined,
    };
  }
  const formData = async (extra = {}) => {
    const blobs = await jpegs();
    if (!blobs) { toast('Those photos are too large to send together. Remove one or use smaller pictures.', 'bad'); return null; }
    const f = new FormData();
    blobs.forEach((b, i) => f.append('image', b, `photo-${i + 1}.jpg`));
    f.append('settings', JSON.stringify(settings()));
    for (const [k, v] of Object.entries(extra)) f.append(k, typeof v === 'string' ? v : JSON.stringify(v));
    return f;
  };

  let timer = 0;
  function changed() {
    syncSizePick();
    save();
    clearTimeout(timer);
    timer = setTimeout(refreshLayout, 120);
  }
  async function refreshLayout(edit = null) {
    const mine = ++token;
    const body = { ...settings(), images: images.map((i) => ({ width: i.width, height: i.height })) };
    if (edit?.counts) body.counts = edit.counts;
    if (edit?.pin) { body.counts = counts() || body.counts; body.pin = edit.pin; }
    const res = await api('/photosheet/layout', { method: 'POST', body });
    if (mine !== token && !edit) return;
    warnings.replaceChildren();
    if (!res.ok) {
      layout = null;
      summary.textContent = '';
      warnings.append(h('div', { class: 'notice bad' }, icon('alert', 18), h('div', {}, res.error?.message || 'Those settings can’t be laid out.')));
    } else {
      layout = res.data;
      if (edit && layout.counts?.length === images.length) layout.counts.forEach((n, k) => { images[k].share = Math.max(n, 1); });  // what the person just set becomes the ratio
      const sheetDir = layout.orientation === 'landscape' ? 'landscape' : 'portrait';
      const dims = `${shown(S.photoW)} × ${shown(S.photoH)} ${S.unit}`;
      summary.replaceChildren(h('strong', {}, `${layout.count} photo${layout.count === 1 ? '' : 's'}`), ` (${layout.cols} × ${layout.rows}) of ${dims}, ${sheetDir}`);
      for (const w of layout.warnings) warnings.append(h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, w)));
      if (res.data.dpi) {
        const low = res.data.dpi < 250;
        warnings.append(h('div', { class: `notice ${low ? 'warn' : 'info'}` }, icon(low ? 'alert' : 'info', 18),
          h('div', {}, low ? `This photo only has about ${res.data.dpi} pixels per inch at this size, so prints may look soft. Use a larger original if you can.`
            : `The photo has about ${res.data.dpi} pixels per inch at this size, which is sharp.`)));
      }
    }
    const ok = Boolean(layout && images.length);
    for (const b of downloadBtns) b.disabled = !ok;
    for (const b of printBtns) {
      b.disabled = !ok || !canPrint();
      b.title = canPrint() ? 'Show the sheet, then print it on the selected printer' : 'Needs a printer with a CUPS queue and a paper size it offers. You can still download the PDF.';
    }
    paintPhotosIfNeeded();
    drawPreview();
  }
  const canPrint = () => Boolean(printer && printer.configured !== false && !printer.error && printer.actual_size && S.paper !== 'custom' && currentPaper().keyword);

  // ------------------------------------------------------------------ download and print
  async function download() {
    for (const b of downloadBtns) b.disabled = true;
    const body = await formData();
    const res = body ? await api('/photosheet/pdf?download=1', { method: 'POST', body }) : null;
    for (const b of downloadBtns) b.disabled = false;
    if (!res) return;
    if (!res.ok) { toast(res.error?.message || 'Could not make the PDF.', 'bad'); return; }
    const url = URL.createObjectURL(res.data);
    const a = h('a', { href: url, download: 'photo-sheet.pdf' });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }

  const sheets = h('div', { class: 'sheets' });
  const dlgInfo = h('p', { class: 'sub' });
  const dlgWarn = h('div', {});
  const goBtn = h('button', { class: 'btn primary', type: 'button', title: 'Send the sheet to the printer now', onclick: () => doPrint() }, icon('printer', 16), 'Print');
  const dialog = h('dialog', { class: 'wide', 'aria-labelledby': 'ps-dlg-title' }, h('h3', { id: 'ps-dlg-title' }, 'Print this sheet?'), dlgInfo, dlgWarn, sheets,
    h('p', { class: 'hint' }, 'This is the PDF that will be sent. A landscape sheet is turned to match how the paper feeds into the printer; the photos stay upright on it.'),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Close without printing', onclick: () => dialog.close() }, 'Cancel'), goBtn));
  let dlgToken = 0;
  dialog.addEventListener('close', () => { if (dialog.open) return; dlgToken += 1; sheets.replaceChildren(); });
  root.append(dialog, saveDialog, delDialog);

  const printOptions = () => ({ media: currentPaper().keyword, media_type: S.mediaType || null, quality: S.quality ? Number(S.quality) : null,
    resolution: S.resolution ? S.resolution.split('x').map(Number) : null, copies: Number(S.copies) || 1 });

  async function openPrint() {
    const p = kit.printer.current;
    const o = printOptions();
    const typeLabel = printer.media_types.find((t) => t.value === o.media_type)?.label;
    const qualityLabel = printer.qualities.find((q) => q.value === o.quality)?.label;
    dlgInfo.textContent = `${layout.count} photos of ${shown(S.photoW)} × ${shown(S.photoH)} ${S.unit} on ${currentPaper().label}${typeLabel ? `, ${typeLabel.toLowerCase()}` : ''}${qualityLabel ? `, ${qualityLabel.toLowerCase()} quality` : ''}, at actual size, to ${p.name}${o.copies > 1 ? ` (${o.copies} copies)` : ''}.`;
    dlgWarn.replaceChildren(...layout.warnings.map((w) => h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, w))));
    const mine = ++dlgToken;
    dialog.showModal();
    sheets.replaceChildren(h('p', { class: 'empty' }, 'Drawing the sheet…'));
    const body = await formData();
    if (mine !== dlgToken) return;
    if (!body) { dialog.close(); return; }
    const res = await api('/photosheet/pdf', { method: 'POST', body });
    if (mine !== dlgToken) return;
    if (!res.ok) { sheets.replaceChildren(h('p', { class: 'empty' }, res.error?.message || 'Could not draw the sheet.')); return; }
    try { await drawPdfBytes(kit, new Uint8Array(await res.data.arrayBuffer()), sheets, { alive: () => mine === dlgToken, width: Math.min(300, dialog.clientWidth - 80), maxPages: 1 }); }
    catch { sheets.replaceChildren(h('p', { class: 'empty' }, 'The preview could not be drawn, but the sheet is ready to print.')); }
  }
  async function doPrint() {
    const p = kit.printer.current;
    goBtn.disabled = true;
    const body = await formData({ printer: p.id, options: printOptions() });
    if (!body) { goBtn.disabled = false; return; }
    const res = await api('/photosheet/print', { method: 'POST', body });
    goBtn.disabled = false;
    if (res.ok) { dialog.close(); toast(`Sent to ${p.name} (job #${res.data.job_id}).`); return; }
    toast(res.error?.message || 'Could not print.', 'bad');
  }

  // ------------------------------------------------------------------ start up
  async function loadConfig() {
    const id = kit.printer.current?.id;
    const res = await api(`/photosheet/config${id ? `?printer=${encodeURIComponent(id)}` : ''}`);
    if (!res.ok) { toast(res.error?.message || 'Could not load the printer’s choices.', 'bad'); return; }
    config = res.data;
    printer = config.printer;
    fillPresets();
    syncPaper();
    syncPrinter();
    if (printer && printer.configured !== false && !printer.error) {
      if (!S.mediaType || !printer.media_types.some((t) => t.value === S.mediaType)) S.mediaType = '';
    }
    $('ps-paper-hint').textContent = printer?.media?.length ? `The paper sizes ${printer.name} offers.` : 'Common paper sizes. Select a printer with CUPS to see the sizes it offers.';
    changed();
  }
  const $ = (id) => document.getElementById(id);

  // The quick-links strip below isn't useful here and would cut the pinned preview short at the end of the page.
  const view = root.parentElement;
  const hideLauncher = () => document.body.toggleAttribute('data-hide-launcher', !view.hidden);
  new MutationObserver(hideLauncher).observe(view, { attributes: true, attributeFilter: ['hidden'] });
  hideLauncher();

  syncMode();
  syncArea();
  syncFields();
  loadPresets();
  paintPhotos();
  drawPreview();
  kit.printer.subscribe(() => loadConfig());
  window.addEventListener('resize', () => { if (!root.hidden) drawPreview(); });
  await loadConfig();
}
