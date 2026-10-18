// Photo Collage Sheet: choose any number of photos and they are fitted onto one sheet by halving the paper (A4 is two A5,
// A5 is two A6, ...). The server finds the arrangements and matches photos to places (the same numbers make the PDF); this page
// draws the preview, lets you try another arrangement or swap two photos, and downloads or prints the sheet.
// The settings follow the Passport Sheet Maker's.
import { drawPdfBytes } from '/tools/maintenance/preview.js';

const UNITS = { mm: { f: 1, dp: 1 }, cm: { f: 10, dp: 2 }, in: { f: 25.4, dp: 3 } };
const KEY = 'pp-collage';
const MAX_SIDE = 7000;  // px: bigger pictures are scaled down before they are sent
const MAX_PHOTOS = 24;

export async function mount(root, kit) {
  const { h, icon, api, toast, ui } = kit;
  root.classList.add('cl');

  // ------------------------------------------------------------------ state (lengths in mm)
  const S = {
    unit: 'mm', paper: 'a4', customW: 210, customH: 297, orientation: 'auto', area: 'full', margin: 5, gap: 3, fit: 'fill', outline: false,
    mediaType: '', quality: '', resolution: '', copies: 1, variant: 0,
  };
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch { /* storage is optional */ }
  S.variant = 0;  // an arrangement belongs to the photos in front of you, so each visit starts at the best one
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch { /* storage is optional */ } };
  let images = [];       // {orig (ImageBitmap), width, height, name, fit, cropX, cropY, open, jpegs: {quality: Blob}}
  let config = null;     // GET /collage/config
  let printer = null;    // this printer's choices, if CUPS is set up
  let layout = null;     // the last good answer of POST /collage/layout
  let order = null;      // the photo in each place, once two were swapped by hand
  let picked = -1;       // the place chosen first when swapping
  let presets = [];
  let token = 0;

  const shown = (v) => String(+(v / UNITS[S.unit].f).toFixed(UNITS[S.unit].dp));
  const papers = () => (printer?.media?.length ? printer.media.map((m) => ({ ...m, id: m.keyword })) : (config?.papers || []).map((m) => ({ ...m, id: m.key })));
  function currentPaper() {
    if (S.paper === 'custom') return { id: 'custom', width: Math.min(S.customW, S.customH), height: Math.max(S.customW, S.customH), label: 'Custom size', borderless: false };
    const list = papers();
    return list.find((p) => p.id === S.paper) || list[0] || { id: 'a4', width: 210, height: 297, label: 'A4', borderless: false };
  }
  const minMargin = () => (printer?.min_margin ?? config?.fallback_min_margin ?? 3);

  // ------------------------------------------------------------------ controls bound to the state
  const ctl = {};
  const bind = (key, el, type) => { ctl[key] = { el, type }; return el; };
  function select(key, label, options, { hint, onchange, id } = {}) {
    const el = bind(key, h('select', { class: 'input', id: id || `cl-${key}`, onchange: () => { S[key] = el.value; onchange?.(el.value); changed(); } }), 'select');
    el.replaceChildren(...options.map(([v, text]) => h('option', { value: v }, text)));
    return h('div', { class: 'field' }, h('label', { for: id || `cl-${key}` }, label), el, hint && h('p', { class: 'hint' }, hint));
  }
  function length(key, label, { min = 0 } = {}) {
    const el = bind(key, h('input', { class: 'input', type: 'number', min, step: 'any', id: `cl-${key}`, autocomplete: 'off',
      oninput: () => { const v = parseFloat(el.value); if (Number.isFinite(v)) { S[key] = v * UNITS[S.unit].f; resetPick(); changed(); } } }), 'len');
    return h('div', { class: 'field' }, h('label', { for: `cl-${key}` }, label, ' (', h('span', { class: 'unit-name' }, S.unit), ')'), el);
  }
  function plain(key, label, { min = 1, max = 99 } = {}) {
    const el = bind(key, h('input', { class: 'input', type: 'number', min, max, step: 1, id: `cl-${key}`, autocomplete: 'off',
      oninput: () => { const v = parseInt(el.value, 10); if (Number.isFinite(v)) { S[key] = v; changed(); } } }), 'plain');
    return h('div', { class: 'field' }, h('label', { for: `cl-${key}` }, label), el);
  }
  const check = (key, label, hint) => {
    const el = bind(key, h('input', { type: 'checkbox', id: `cl-${key}`, onchange: () => { S[key] = el.checked; changed(); } }), 'check');
    return h('label', { class: 'check-row', for: `cl-${key}` }, el, h('span', {}, h('strong', {}, label), hint && h('span', { class: 'hint' }, hint)));
  };
  const head = (name, text) => h('h3', { class: 'cl-head' }, icon(name, 18), text);
  function paintControls() {
    for (const [key, { el, type }] of Object.entries(ctl)) {
      if (document.activeElement === el) continue;
      if (type === 'check') el.checked = Boolean(S[key]);
      else if (type === 'len') el.value = shown(S[key]);
      else el.value = String(S[key]);
    }
    for (const el of root.querySelectorAll('.unit-name')) el.textContent = S.unit;
  }

  // ------------------------------------------------------------------ 1. presets (quick starts, never saved back)
  const presetChips = h('div', { class: 'chips' });
  const presetBox = h('section', { class: 'card cl-card' }, head('list', 'Presets'),
    h('p', { class: 'hint' }, 'A preset fills in the settings below, as a quick start. Change anything afterwards; nothing is saved unless you save it as a new preset.'), presetChips,
    h('div', { class: 'actions left' }, h('button', { class: 'btn small', type: 'button', title: 'Save the paper, layout, finishing and print choices below (not the photos) as a new preset to use again', onclick: () => openSave() }, icon('check', 15), 'Save these settings as a preset…')));

  // ------------------------------------------------------------------ 2. the photos
  const picker = h('input', { type: 'file', accept: 'image/*', multiple: true, hidden: true, onchange: () => { if (picker.files.length) loadImages([...picker.files]); picker.value = ''; } });
  const dropzone = h('button', { class: 'drop', type: 'button', title: 'Choose your photos (JPEG, PNG or WebP)', onclick: () => picker.click() },
    icon('image', 28), h('strong', {}, 'Choose your photos'), h('span', { class: 'hint' }, `or drop them here. Add as many as you like, up to ${MAX_PHOTOS}: they are all fitted onto one sheet.`));
  const photoList = h('ul', { class: 'photo-list', hidden: true });
  const addMore = h('button', { class: 'btn small', type: 'button', title: 'Add more photos to this sheet', hidden: true, onclick: () => picker.click() }, icon('image', 15), 'Add photos');
  const photoCount = h('p', { class: 'hint', hidden: true });
  const photoBox = h('div', { class: 'photo-box' }, dropzone, photoCount, photoList, addMore);
  for (const ev of ['dragover', 'dragenter']) photoBox.addEventListener(ev, (e) => { e.preventDefault(); photoBox.classList.add('over'); });
  for (const ev of ['dragleave', 'drop']) photoBox.addEventListener(ev, () => photoBox.classList.remove('over'));
  photoBox.addEventListener('drop', (e) => { e.preventDefault(); const f = [...(e.dataTransfer?.files || [])]; if (f.length) loadImages(f); });
  const photosCard = h('section', { class: 'card cl-card' }, head('image', '1 · Your photos'), photoBox, picker);

  async function loadImages(files) {
    for (const file of files) {
      if (images.length >= MAX_PHOTOS) { toast(`At most ${MAX_PHOTOS} photos fit on one sheet.`, 'bad'); break; }
      if (!file.type.startsWith('image/')) { toast(`${file.name} is not a picture.`, 'bad'); continue; }
      let bitmap;
      try { bitmap = await createImageBitmap(file); } catch { toast(`This browser can’t read ${file.name}. Try a JPEG or PNG.`, 'bad'); continue; }
      const k = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
      images.push({ orig: bitmap, width: Math.round(bitmap.width * k), height: Math.round(bitmap.height * k), name: file.name, fit: S.fit, cropX: 0.5, cropY: 0.5, open: false, jpegs: {} });
    }
    resetPick(true);
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
  const cellOf = (i) => layout?.cells?.find((c) => c.photo === i) || null;
  function paintPhotos() {
    const some = images.length > 0;
    dropzone.hidden = some; photoList.hidden = !some; addMore.hidden = !some; photoCount.hidden = !some;
    photoCount.textContent = `${images.length} photo${images.length === 1 ? '' : 's'}, all on one sheet. Each is cropped (or shown whole) to the place it gets: change that for a photo under “Crop and fit”.`;
    photoList.replaceChildren(...images.map((img, i) => {
      const thumb = document.createElement('canvas');
      thumb.width = 44; thumb.height = Math.max(1, Math.round(44 * img.height / img.width));
      thumb.getContext('2d').drawImage(img.orig, 0, 0, thumb.width, thumb.height);
      const main = h('div', { class: 'photo-main' }, thumb,
        h('div', { class: 'photo-meta' }, h('strong', { title: img.name }, img.name), h('span', { class: 'hint' }, `${img.width} × ${img.height} px`)),
        h('button', { class: 'icon-mini', type: 'button', 'aria-label': `Remove ${img.name}`, title: 'Remove this photo', onclick: () => { images.splice(i, 1); resetPick(true); paintPhotos(); changed(); } }, icon('x', 16)));
      return h('li', { class: 'photo-row' }, main, adjustPanel(img, i));
    }));
  }
  // Crop and fit belong to each photo: one may need its sides trimmed while another already has the right shape.
  function adjustPanel(img, i) {
    const cell = cellOf(i);
    const shape = cell ? Math.abs(img.width / img.height - cell.w / cell.h) > 0.01 : true;
    const fitSel = h('select', { class: 'input', 'aria-label': `If ${img.name} has a different shape`, title: 'Crop the photo to its place, or show all of it with white edges',
      onchange: () => { img.fit = fitSel.value; paintPhotos(); changed(); } },
      [['fill', 'Crop it to its place'], ['fit', 'Show all of it (white edges)']].map(([v, t]) => h('option', { value: v, selected: img.fit === v ? true : null }, t)));
    const slider = (key, label, hint) => {
      const r = h('input', { type: 'range', min: 0, max: 100, value: Math.round(img[key] * 100), 'aria-label': `${hint} for ${img.name}`, title: hint, oninput: () => { img[key] = r.value / 100; changed(); } });
      return h('label', { class: 'range' }, h('span', {}, label), r);
    };
    const note = img.fit === 'fit' ? 'The whole photo is shown, with white edges where it doesn’t fill its place.'
      : shape ? 'The photo is cropped to its place. Move it in its place:' : 'This photo already has the shape of its place, so nothing is cropped.';
    const d = h('details', { class: 'adjust', open: img.open ? true : null, ontoggle: () => { img.open = d.open; } },
      h('summary', {}, icon('chevron', 16), 'Crop and fit', h('span', { class: 'hint' }, img.fit === 'fit' ? ' · shown whole' : shape ? ' · cropped' : ' · right shape')),
      h('div', { class: 'adjust-body' }, fitSel, h('p', { class: 'hint' }, note),
        img.fit === 'fill' && shape ? slider('cropX', 'Left / right', 'Move the photo left or right in its place') : null,
        img.fit === 'fill' && shape ? slider('cropY', 'Up / down', 'Move the photo up or down in its place') : null,
        images.length > 1 ? h('button', { class: 'btn small', type: 'button', title: 'Give every photo this one’s crop and fit',
          onclick: () => { for (const o of images) { o.fit = img.fit; o.cropX = img.cropX; o.cropY = img.cropY; } paintPhotos(); changed(); } }, 'Use for all photos') : null));
    return d;
  }

  // ------------------------------------------------------------------ 3. the arrangement (instead of a photo size)
  const groupsNote = h('p', { class: 'summary' });
  const variantLabel = h('span', { class: 'variant-label' });
  const prevBtn = h('button', { class: 'btn small', type: 'button', title: 'Show the previous arrangement', 'aria-label': 'Previous arrangement', onclick: () => stepVariant(-1) }, icon('chevron', 14), 'Previous');
  const nextBtn = h('button', { class: 'btn small', type: 'button', title: 'Try another arrangement of the same photos', 'aria-label': 'Next arrangement', onclick: () => stepVariant(1) }, 'Next', icon('chevron', 14));
  const resetBtn = h('button', { class: 'btn small', type: 'button', title: 'Put the photos back where they match best', hidden: true, onclick: () => { order = null; picked = -1; changed(); } }, 'Reset the swaps');
  const arrangeBox = h('section', { class: 'card cl-card' }, head('hash', '2 · Arrangement'),
    h('p', { class: 'hint' }, 'The sheet is halved, the way A4 is two A5 and A5 is two A6, and each photo goes where its shape fits best, so as little as possible is cropped.'),
    groupsNote, h('div', { class: 'variant-row' }, prevBtn, variantLabel, nextBtn),
    h('p', { class: 'hint' }, 'To swap two photos, tap one in the preview, then the other.'), resetBtn);
  function stepVariant(d) {
    if (!layout) return;
    S.variant = Math.min(Math.max(S.variant + d, 0), layout.variants - 1);
    resetPick();
    changed();
  }
  function resetPick(photosChanged = false) {
    order = null; picked = -1;
    if (photosChanged) S.variant = 0;
  }
  function paintArrangement() {
    const some = Boolean(layout);
    arrangeBox.hidden = !some && !images.length;
    if (!some) { groupsNote.textContent = images.length ? '' : 'Choose some photos to see how they fit.'; variantLabel.textContent = ''; prevBtn.disabled = nextBtn.disabled = true; return; }
    const unit = S.unit;
    groupsNote.textContent = layout.groups.map((g) => `${g.count} × ${shown(g.w)} × ${shown(g.h)} ${unit}`).join(' + ') + ` (${layout.orientation === 'landscape' ? 'sheet turned sideways' : 'sheet upright'})`;
    variantLabel.textContent = `Arrangement ${layout.variant + 1} of ${layout.variants}`;
    prevBtn.disabled = layout.variant <= 0; nextBtn.disabled = layout.variant >= layout.variants - 1;
    resetBtn.hidden = !order;
  }

  // ------------------------------------------------------------------ 4. paper and layout
  const unitSelect = select('unit', 'Unit', [['mm', 'mm'], ['cm', 'cm'], ['in', 'in']], { id: 'cl-unit' });
  const paperSelect = bind('paper', h('select', { class: 'input', id: 'cl-paper', onchange: () => { S.paper = paperSelect.value; resetPick(); syncPaper(); changed(); } }), 'select');
  const paperHint = h('p', { class: 'hint', id: 'cl-paper-hint' }, 'The sizes this printer offers, when a printer with CUPS is selected.');
  const customRow = h('div', { class: 'two', hidden: true }, length('customW', 'Paper width', { min: 40 }), length('customH', 'Paper height', { min: 40 }));
  const areaNote = h('p', { class: 'notice info', hidden: true });
  const layoutBox = h('section', { class: 'card cl-card' }, head('file', '3 · Paper and layout'),
    h('div', { class: 'field' }, h('label', { for: 'cl-paper' }, 'Paper'), paperSelect, paperHint), customRow,
    h('div', { class: 'two' }, select('orientation', 'Sheet direction', [['auto', 'Automatic (fits best)'], ['portrait', 'Portrait'], ['landscape', 'Landscape']], { onchange: () => resetPick() }), unitSelect),
    select('area', 'Print on', [['full', 'The whole page'], ['top', 'Top half only'], ['bottom', 'Bottom half only']],
      { onchange: (v) => { if (v !== 'full') S.orientation = 'auto'; resetPick(); syncVisibility(); }, hint: 'A half leaves the rest of the sheet blank, so you can use it later (the top half of A4 is an A5 area).' }),
    areaNote,
    h('div', { class: 'two' }, length('margin', 'Margin around the photos'), length('gap', 'Space between photos')));
  function syncPaper() {
    const list = papers();
    paperSelect.replaceChildren(...list.map((p) => h('option', { value: p.id }, p.label)), h('option', { value: 'custom' }, 'Custom size…'));
    if (S.paper !== 'custom' && !list.some((p) => p.id === S.paper)) S.paper = list.find((p) => p.keyword === printer?.default_media)?.id || list[0]?.id || 'a4';
    paperSelect.value = S.paper;
    syncVisibility();
  }
  function syncVisibility() {
    customRow.hidden = S.paper !== 'custom';
    paperHint.textContent = printer?.media?.length ? `The paper sizes ${printer.name} offers.` : 'Common paper sizes. Select a printer with CUPS to see the sizes it offers.';
    const part = S.area !== 'full';
    if (ctl.orientation) ctl.orientation.el.disabled = part;
    areaNote.hidden = !part;
    if (part) {
      const other = { top: 'bottom', bottom: 'top' }[S.area];
      areaNote.replaceChildren(icon('info', 18), h('div', {}, `Only the ${S.area} half is printed; the rest stays blank. To use the ${other} half later, choose “${other[0].toUpperCase()}${other.slice(1)} half only” and put the sheet back in the tray the same way it went in (let the ink dry first). The sheet is not turned, so “Sheet direction” is fixed.`));
    } else areaNote.replaceChildren();
  }

  // ------------------------------------------------------------------ 5. finishing, 6. printing choices
  const finishBox = h('section', { class: 'card cl-card' }, head('check', '4 · Finishing'),
    check('outline', 'Thin outline around each photo', 'Easier to cut along.'),
    select('fit', 'Photos that are a different shape from their place', [['fill', 'Crop them to fit the place'], ['fit', 'Show all of each (white edges)']],
      { hint: 'This is the starting choice for photos you add; you can change it for each photo under “Crop and fit”.', onchange: (v) => { for (const i of images) i.fit = v; paintPhotos(); } }));
  const printBox = h('section', { class: 'card cl-card' });
  function syncPrinter() {
    const p = kit.printer.current;
    if (!p) { printBox.replaceChildren(head('printer', '5 · Printing'), h('p', { class: 'hint' }, 'Add a printer to print from here. You can still download the PDF.')); return; }
    if (!printer || printer.configured === false) {
      printBox.replaceChildren(head('printer', '5 · Printing'),
        h('p', { class: 'hint' }, `${p.name} has no CUPS print queue, so it can’t print from here. Download the PDF and print it from your computer, or set up CUPS and choose the queue under Printers → Edit.`));
      return;
    }
    if (printer.error) { printBox.replaceChildren(head('printer', '5 · Printing'), ui.errorBox(printer.error, loadConfig)); return; }
    const types = [['', 'Printer’s own choice'], ...printer.media_types.map((t) => [t.value, t.label])];
    const qualities = [['', 'Printer’s own choice'], ...printer.qualities.map((q) => [q.value, q.label])];
    const res = printer.resolutions;
    printBox.replaceChildren(head('printer', `5 · Print on ${p.name}`),
      h('div', { class: 'grid2' }, select('mediaType', 'Paper type', types, { hint: 'Set this to the paper in the tray.' }), select('quality', 'Print quality', qualities, { hint: 'High uses more ink and time.' }),
        res.length > 1 ? select('resolution', 'Resolution', [['', 'Printer’s own choice'], ...res.map((r) => [r.value.join('x'), r.label])])
          : h('div', { class: 'field' }, h('label', {}, 'Resolution'), h('p', { class: 'static' }, res[0]?.label || 'Set by the printer')),
        plain('copies', 'Copies of the sheet', { max: 20 })),
      printer.actual_size ? h('p', { class: 'hint' }, 'Printed at actual size (100%), so each photo comes out at the size shown. Turn off “fit to page” in any other print dialog.')
        : h('p', { class: 'msg' }, 'This print queue can’t print at actual size, so printing is switched off. Download the PDF instead.'));
    paintControls();
  }
  const canPrint = () => Boolean(printer && printer.configured !== false && !printer.error && printer.actual_size && S.paper !== 'custom' && currentPaper().keyword);

  // ------------------------------------------------------------------ the preview
  const canvas = h('canvas', { class: 'sheet-canvas', role: 'img', 'aria-label': 'Preview of the sheet', title: 'Tap a photo, then another, to swap them' });
  const summary = h('p', { class: 'summary', 'aria-live': 'polite' });
  const warnings = h('div', { class: 'warns' });
  const downloadBtns = [], printBtns = [];
  const actionRow = (extra = '') => {
    const d = h('button', { class: 'btn', type: 'button', title: 'Download the sheet as a PDF to print yourself', disabled: true, onclick: () => download() }, icon('download', 16), 'Download PDF');
    const p = h('button', { class: 'btn primary', type: 'button', title: 'Show the sheet, then print it on the selected printer', disabled: true, onclick: () => openPrint() }, icon('printer', 16), 'Preview and print…');
    downloadBtns.push(d); printBtns.push(p);
    return h('div', { class: `actions left ${extra}` }, p, d);
  };
  function cover(iw, ih, c, fit, cx, cy) {  // where the picture sits inside a place (mm); the same rule as the PDF writer
    const s = fit === 'fit' ? Math.min(c.w / iw, c.h / ih) : Math.max(c.w / iw, c.h / ih);
    const dw = iw * s, dh = ih * s;
    return { dw, dh, x: c.x + (c.w - dw) * (dw > c.w ? cx : 0.5), y: c.y + (c.h - dh) * (dh > c.h ? cy : 0.5) };
  }
  function drawPreview() {
    const ratio = window.devicePixelRatio || 1;
    const L = layout;
    const p = currentPaper();
    const pw = L ? L.page_w : Math.min(p.width, p.height), ph = L ? L.page_h : Math.max(p.width, p.height);
    const slim = window.matchMedia('(max-width: 900px)').matches;
    const maxH = slim ? Math.max(150, window.innerHeight * 0.28) : Math.max(240, window.innerHeight - 400);
    let cssW = Math.min(canvas.parentElement.clientWidth || 360, 460);
    if ((ph * cssW) / pw > maxH) cssW = (maxH * pw) / ph;
    const k = cssW / pw;
    canvas.width = Math.round(cssW * ratio); canvas.height = Math.round(ph * k * ratio);
    canvas.style.setProperty('width', `${cssW}px`); canvas.style.setProperty('height', `${ph * k}px`);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio * k, 0, 0, ratio * k, 0, 0);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, pw, ph);
    if (!L) return;
    if (L.area && L.area !== 'full' && L.region) {  // the part left blank (only in this preview; the PDF has nothing there)
      const R = L.region;
      const rect = { top: [0, R.h, L.page_w, L.page_h - R.h], bottom: [0, 0, L.page_w, R.y] }[L.area];
      ctx.fillStyle = 'rgba(100, 116, 139, 0.10)'; ctx.fillRect(...rect);
      ctx.save(); ctx.setLineDash([2, 1.5]); ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 0.4; ctx.strokeRect(R.x, R.y, R.w, R.h); ctx.restore();
      ctx.fillStyle = '#64748b'; ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('stays blank', rect[0] + rect[2] / 2, rect[1] + rect[3] / 2); ctx.textAlign = 'start';
    }
    L.cells.forEach((c, n) => {
      const img = images[c.photo];
      ctx.save();
      ctx.beginPath(); ctx.rect(c.x, c.y, c.w, c.h); ctx.clip();
      if (img) { const q = cover(img.width, img.height, c, img.fit, img.cropX, img.cropY); ctx.drawImage(img.orig, q.x, q.y, q.dw, q.dh); }
      ctx.restore();
      ctx.lineWidth = n === picked ? 0.9 : 0.25; ctx.strokeStyle = n === picked ? '#0c9f6c' : S.outline ? '#444' : 'rgba(0,0,0,0)';
      if (n === picked || S.outline) ctx.strokeRect(c.x, c.y, c.w, c.h);
    });
  }
  canvas.addEventListener('click', (ev) => {
    if (!layout) return;
    const box = canvas.getBoundingClientRect();
    const x = ((ev.clientX - box.left) / box.width) * layout.page_w, y = ((ev.clientY - box.top) / box.height) * layout.page_h;
    const i = layout.cells.findIndex((c) => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
    if (i < 0) return;
    if (picked < 0) { picked = i; drawPreview(); return; }
    if (picked === i) { picked = -1; drawPreview(); return; }
    const next = layout.cells.map((c) => c.photo);
    [next[picked], next[i]] = [next[i], next[picked]];
    order = next; picked = -1;
    changed();
  });

  // ------------------------------------------------------------------ settings -> server
  function settings() {
    const paper = currentPaper();
    return {
      paper: { width: paper.width, height: paper.height }, margin: S.margin, gap: S.gap, orientation: S.orientation, area: S.area, fit: S.fit, outline: S.outline,
      photos: images.map((i) => ({ fit: i.fit, crop_x: i.cropX, crop_y: i.cropY })), variant: S.variant, order,
      min_margin: minMargin(), borderless: Boolean(paper.borderless),
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
    save();
    paintControls();
    syncVisibility();
    clearTimeout(timer);
    timer = setTimeout(refresh, 120);
    syncButtons();
  }
  function syncButtons() {
    const ok = Boolean(layout && images.length);
    for (const b of downloadBtns) b.disabled = !ok;
    for (const b of printBtns) {
      b.disabled = !ok || !canPrint();
      b.title = canPrint() ? 'Show the sheet, then print it on the selected printer' : 'Needs a printer with a CUPS queue and a paper size it offers. You can still download the PDF.';
    }
  }
  async function refresh() {
    const mine = ++token;
    if (!images.length) { layout = null; summary.textContent = ''; warnings.replaceChildren(); paintArrangement(); syncButtons(); drawPreview(); return; }
    const res = await api('/collage/layout', { method: 'POST', body: { ...settings(), images: images.map((i) => ({ width: i.width, height: i.height })) } });
    if (mine !== token) return;
    warnings.replaceChildren();
    if (!res.ok) {
      layout = null; summary.textContent = '';
      warnings.append(h('div', { class: 'notice bad' }, icon('alert', 18), h('div', {}, res.error?.message || 'Those settings can’t be laid out.')));
      paintArrangement(); syncButtons(); drawPreview();
      return;
    }
    layout = res.data;
    S.variant = layout.variant;
    summary.textContent = `${layout.count} photo${layout.count === 1 ? '' : 's'} on one sheet. The least sharp is about ${layout.dpi} pixels per inch.`;
    warnings.append(...layout.warnings.map((w) => h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, w))));
    if (layout.dpi < 150) warnings.append(h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, `One of the photos would print at only ${layout.dpi} pixels per inch, so it may look soft. A bigger photo or a smaller place helps.`)));
    paintArrangement(); paintPhotosIfChanged(); syncButtons(); drawPreview();
  }
  let paintedSig = '';
  function paintPhotosIfChanged() {  // the "Crop and fit" notes depend on the place each photo got
    const sig = JSON.stringify(layout?.cells?.map((c) => [c.photo, Math.round(c.w), Math.round(c.h)]));
    if (sig !== paintedSig) { paintedSig = sig; paintPhotos(); }
  }

  // ------------------------------------------------------------------ presets: saved choices (not the photos)
  function describe(p) {
    const x = p.settings;
    const part = x.area && x.area !== 'full' ? `${x.area} half only` : 'whole page';
    return `${x.paper.label || `${x.paper.width} × ${x.paper.height} mm`} · ${part} · margin ${x.margin} mm, gap ${x.gap} mm`;
  }
  function paintPresets() {
    presetChips.replaceChildren(...[...presets.map((p) => h('span', { class: `chip-wrap${p.builtin ? '' : ' mine'}` },
        h('button', { class: 'chip', type: 'button', title: describe(p), onclick: () => applyPreset(p) }, p.name),
        p.builtin ? null : h('button', { class: 'chip-x', type: 'button', 'aria-label': `Delete the preset ${p.name}`, title: 'Delete this preset (asks first)', onclick: () => confirmDelete(p) }, icon('x', 12)))),
      presets.length ? null : h('span', { class: 'hint' }, 'No presets yet. Set things up, then save them here.')].filter(Boolean));  // (a native replaceChildren would print "null")
  }
  async function loadPresets() {
    const res = await api('/collage/presets');
    presets = res.ok ? res.data.presets : [];
    paintPresets();
  }
  function presetSettings() {
    const paper = currentPaper();
    const out = { unit: S.unit, paper: { width: paper.width, height: paper.height, ...(paper.keyword ? { media: paper.keyword } : {}), label: paper.label },
      orientation: S.orientation, area: S.area, margin: S.margin, gap: S.gap, fit: S.fit, outline: S.outline, copies: Number(S.copies) || 1 };
    if (S.mediaType) out.media_type = S.mediaType;
    if (S.quality) out.quality = Number(S.quality);
    if (S.resolution) out.resolution = S.resolution.split('x').map(Number);
    return out;
  }
  const controlState = () => new Map([...root.querySelectorAll('input[id], select[id]')].map((el) => [el.id, el.type === 'checkbox' ? el.checked : el.value]));
  function applyPreset(p) {
    const x = p.settings;
    const before = controlState();
    Object.assign(S, { unit: x.unit || 'mm', orientation: x.orientation || 'auto', area: ['top', 'bottom'].includes(x.area) ? x.area : 'full', margin: x.margin ?? 5, gap: x.gap ?? 3,
      fit: x.fit || 'fill', outline: Boolean(x.outline), copies: x.copies || 1, variant: 0 });
    order = null; picked = -1;
    const list = papers();
    const found = list.find((m) => x.paper.media && m.keyword === x.paper.media) || list.find((m) => Math.abs(m.width - x.paper.width) < 0.6 && Math.abs(m.height - x.paper.height) < 0.6);
    if (found) S.paper = found.id; else { S.paper = 'custom'; S.customW = x.paper.width; S.customH = x.paper.height; }
    S.mediaType = printer?.media_types?.some((t) => t.value === x.media_type) ? x.media_type : '';
    S.quality = printer?.qualities?.some((q) => q.value === x.quality) ? String(x.quality) : '';
    S.resolution = x.resolution && printer?.resolutions?.some((r) => r.value.join() === x.resolution.join()) ? x.resolution.join('x') : '';
    for (const img of images) { img.fit = S.fit; img.cropX = 0.5; img.cropY = 0.5; }
    paintPhotos(); paintControls(); syncPaper(); syncPrinter(); changed();
    const after = controlState();  // what the preset changed lights up for a moment
    for (const [id, value] of after) {
      if (before.get(id) !== value) { const f = document.getElementById(id)?.closest('.field, .check-row'); if (f) { f.classList.remove('flash'); void f.offsetWidth; f.classList.add('flash'); setTimeout(() => f.classList.remove('flash'), 1600); } }
    }
    toast(`Filled in the settings from “${p.name}”.`);
  }
  const saveName = h('input', { class: 'input', maxlength: 60, placeholder: 'For example: Family sheet on A4', id: 'cl-preset-name', 'aria-label': 'Preset name', oninput: () => saveNote() });
  const saveMsg = h('p', { class: 'msg', role: 'alert' });
  const saveReplace = h('p', { class: 'hint' });
  const saveGo = h('button', { class: 'btn primary', type: 'button', title: 'Save the preset', onclick: () => saveAs(saveName.value) }, 'Save preset');
  const saveDialog = h('dialog', { 'aria-labelledby': 'cl-save-title' }, h('h3', { id: 'cl-save-title' }, 'Save as a preset'),
    h('p', {}, 'Keeps the paper, layout, finishing and print choices. The photos themselves are not saved.'), saveName, saveReplace, saveMsg,
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Close without saving', onclick: () => saveDialog.close() }, 'Cancel'), saveGo));
  function saveNote() {
    saveMsg.textContent = '';
    const clash = presets.find((p) => !p.builtin && p.name.toLowerCase() === saveName.value.trim().toLowerCase());
    saveReplace.textContent = clash ? `A preset called “${clash.name}” already exists. Saving replaces it.` : '';
  }
  function openSave() { saveName.value = `Collage on ${currentPaper().label}`.slice(0, 60); saveNote(); saveDialog.showModal(); saveName.select(); }
  async function saveAs(name) {
    const res = await api('/collage/presets', { method: 'POST', body: { name, settings: presetSettings() } });
    if (!res.ok) { saveMsg.textContent = res.error?.message || 'Could not save.'; return; }
    saveDialog.close();
    toast(res.data.replaced ? `Preset “${res.data.name}” updated.` : `Preset “${res.data.name}” saved.`);
    await loadPresets();
  }
  const delDialog = h('dialog', { 'aria-labelledby': 'cl-del-title' }, h('h3', { id: 'cl-del-title' }, 'Delete this preset?'), h('p', { id: 'cl-del-text' }),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Keep the preset', onclick: () => delDialog.close() }, 'Keep it'),
      h('button', { class: 'btn primary danger-fill', type: 'button', id: 'cl-del-go', title: 'Delete the preset for good' }, 'Delete')));
  function confirmDelete(p) {
    document.getElementById('cl-del-text').textContent = `“${p.name}” will be removed for everyone who uses this panda-print. Your photos and other presets are not affected.`;
    document.getElementById('cl-del-go').onclick = async () => {
      delDialog.close();
      const res = await api(`/collage/presets/${p.id}`, { method: 'DELETE' });
      if (!res.ok) { toast(res.error?.message || 'Could not delete.', 'bad'); return; }
      toast('Preset deleted.');
      await loadPresets();
    };
    delDialog.showModal();
  }

  // ------------------------------------------------------------------ download and print
  async function download() {
    for (const b of downloadBtns) b.disabled = true;
    const body = await formData();
    const res = body ? await api('/collage/pdf?download=1', { method: 'POST', body }) : null;
    syncButtons();
    if (!res) return;
    if (!res.ok) { toast(res.error?.message || 'Could not make the PDF.', 'bad'); return; }
    const url = URL.createObjectURL(res.data);
    const a = h('a', { href: url, download: 'photo-collage.pdf' });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  const sheets = h('div', { class: 'sheets' });
  const dlgInfo = h('p', { class: 'sub' });
  const dlgWarn = h('div', {});
  const goBtn = h('button', { class: 'btn primary', type: 'button', title: 'Send the sheet to the printer now', onclick: () => doPrint() }, icon('printer', 16), 'Print');
  const dialog = h('dialog', { class: 'wide', 'aria-labelledby': 'cl-dlg-title' }, h('h3', { id: 'cl-dlg-title' }, 'Print this sheet?'), dlgInfo, dlgWarn, sheets,
    h('p', { class: 'hint' }, 'This is the PDF that will be sent. A sideways sheet is turned to match how the paper feeds into the printer; the photos stay upright on it.'),
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
    dlgInfo.textContent = `${layout.count} photo${layout.count === 1 ? '' : 's'} on ${currentPaper().label}${typeLabel ? `, ${typeLabel.toLowerCase()}` : ''}, at actual size, to ${p.name}${o.copies > 1 ? ` (${o.copies} copies)` : ''}.`;
    dlgWarn.replaceChildren(...layout.warnings.map((w) => h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, w))));
    const mine = ++dlgToken;
    dialog.showModal();
    sheets.replaceChildren(h('p', { class: 'empty' }, 'Drawing the sheet…'));
    const body = await formData();
    if (mine !== dlgToken) return;
    if (!body) { dialog.close(); return; }
    const res = await api('/collage/pdf', { method: 'POST', body });
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
    const res = await api('/collage/print', { method: 'POST', body });
    goBtn.disabled = false;
    if (res.ok) { dialog.close(); toast(`Sent to ${p.name} (job #${res.data.job_id}).`); return; }
    toast(res.error?.message || 'Could not print.', 'bad');
  }

  // ------------------------------------------------------------------ assemble the page
  const form = h('div', { class: 'ps-form' }, presetBox, photosCard, arrangeBox, layoutBox, finishBox, printBox,
    h('section', { class: 'card cl-card narrow-only' }, head('printer', 'Make the sheet'), actionRow()));
  const side = h('aside', { class: 'ps-side' }, h('div', { class: 'sticky-col' },
    h('div', { class: 'card cl-card preview-card' }, h('div', { class: 'sheet-wrap' }, canvas), summary, warnings), actionRow('wide-only center')));
  root.append(h('div', { class: 'ps-layout' }, form, side));

  async function loadConfig() {
    const id = kit.printer.current?.id;
    const res = await api(`/collage/config${id ? `?printer=${encodeURIComponent(id)}` : ''}`);
    if (!res.ok) { toast(res.error?.message || 'Could not load the printer’s choices.', 'bad'); return; }
    config = res.data;
    printer = config.printer;
    syncPaper(); syncPrinter(); paintControls();
    changed();
  }

  // The quick-links strip isn't useful here and would cut the pinned preview short at the end of the page.
  const view = root.parentElement;
  const hideLauncher = () => document.body.toggleAttribute('data-hide-launcher', !view.hidden);
  new MutationObserver(hideLauncher).observe(view, { attributes: true, attributeFilter: ['hidden'] });
  hideLauncher();

  paintControls(); syncVisibility(); paintArrangement(); loadPresets(); drawPreview();
  kit.printer.subscribe(() => loadConfig());
  window.addEventListener('resize', () => { if (!root.hidden) drawPreview(); });
  await loadConfig();
}
