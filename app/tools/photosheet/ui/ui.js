// Photo Sheets: choose a photo, say how big each copy is and what paper it goes on, see the sheet, then download
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
    fit: 'fill', cropX: 0.5, cropY: 0.5, marks: true, outline: false, fillPage: true, paper: 'a4', customW: 210, customH: 297,
    mediaType: '', quality: '', resolution: '', copies: 1,
  };
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch { /* storage is optional */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch { /* storage is optional */ } };
  let images = [];        // the photos: {orig (ImageBitmap), width, height, name, copies, jpegs: {quality: Blob}}
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
  const fillBox = h('div', { class: 'fill-box', hidden: true });
  const addMore = h('button', { class: 'btn small', type: 'button', title: 'Add more photos to this sheet', hidden: true, onclick: () => picker.click() }, icon('image', 15), 'Add photos');
  const photoBox = h('div', { class: 'photo-box' }, dropzone, photoList, addMore, fillBox);
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
      images.push({ orig: bitmap, width, height, name: file.name, copies: 1, jpegs: {} });
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
  const resultText = (i) => (layout?.counts && layout.counts.length === images.length ? layout.counts[i] : null);
  function paintPhotos() {
    const some = images.length > 0;
    dropzone.hidden = some; photoList.hidden = !some; addMore.hidden = !some;
    fillBox.hidden = images.length < 2;
    photoList.replaceChildren(...images.map((img, i) => {
      const thumb = document.createElement('canvas');
      thumb.width = 48; thumb.height = Math.max(1, Math.round(48 * img.height / img.width));
      thumb.getContext('2d').drawImage(img.orig, 0, 0, thumb.width, thumb.height);
      const copies = h('input', { class: 'input mini', type: 'number', min: 1, max: 600, step: 1, value: img.copies, 'aria-label': `Copies of ${img.name}`, title: 'Copies of this photo, as a share of the sheet',
        oninput: () => { const v = parseInt(copies.value, 10); if (Number.isFinite(v) && v >= 1) { img.copies = v; changed(); } } });
      return h('li', { class: 'photo-row' }, thumb,
        h('div', { class: 'photo-meta' }, h('strong', { title: img.name }, img.name), h('span', { class: 'hint' }, `${img.width} × ${img.height} px`)),
        images.length > 1 ? h('label', { class: 'copies' }, h('span', { class: 'hint' }, 'Copies'), copies, h('span', { class: 'result', 'data-i': i }, '')) : null,
        h('button', { class: 'icon-mini', type: 'button', 'aria-label': `Remove ${img.name}`, title: 'Remove this photo', onclick: () => { images.splice(i, 1); paintPhotos(); changed(); } }, icon('x', 16)));
    }));
    fillBox.replaceChildren(check('fillPage', 'Fill the whole page', 'Copies are a ratio: 1, 1, 1 shares the page equally; 2, 1, 1 gives the first photo half of it. Switch off to print exactly the copies you typed.'));
    paintResults();
  }
  function paintResults() {
    for (const el of photoList.querySelectorAll('.result')) {
      const n = resultText(Number(el.dataset.i));
      el.textContent = n == null ? '' : n === 0 ? '→ not on the sheet' : `→ ${n} on the sheet`;
    }
  }

  // ------------------------------------------------------------------ 2. photo size
  const unitSelect = h('select', { class: 'input unit-pick', 'aria-label': 'Unit of length', title: 'Millimetres, centimetres or inches',
    onchange: () => { S.unit = unitSelect.value; syncFields(); changed(); } },
    ['mm', 'cm', 'in'].map((u) => h('option', { value: u, selected: u === S.unit ? true : null }, u)));
  const presetSelect = h('select', { class: 'input', id: 'ps-preset', 'aria-label': 'Common photo sizes', onchange: () => {
    const p = config?.presets?.[Number(presetSelect.value)];
    if (p) { S.photoW = p.width; S.photoH = p.height; syncFields(); changed(); }
  } });
  function fillPresets() {
    presetSelect.replaceChildren(h('option', { value: '' }, 'Choose a common size…'),
      ...(config?.presets || []).map((p, i) => h('option', { value: i }, p.label)));
  }

  // ------------------------------------------------------------------ 3. paper and layout
  const paperField = h('div', { class: 'field' });
  const paperSelect = h('select', { class: 'input', id: 'ps-paper', onchange: () => { S.paper = paperSelect.value; syncPaper(); changed(); } });
  const customRow = h('div', { class: 'two', hidden: true });
  const modeField = h('div', { class: 'field' });
  const gridRow = h('div', { class: 'two', hidden: true });
  const countRow = h('div', { class: 'two', hidden: true });
  const cropBox = h('div', { class: 'crop', hidden: true });

  function syncPaper() {
    const list = papers();
    paperSelect.replaceChildren(...list.map((p) => h('option', { value: p.id }, p.label)), h('option', { value: 'custom' }, 'Custom size…'));
    if (S.paper !== 'custom' && !list.some((p) => p.id === S.paper)) S.paper = list.find((p) => p.keyword === printer?.default_media)?.id || list[0]?.id || 'a4';
    paperSelect.value = S.paper;
    customRow.hidden = S.paper !== 'custom';
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

  // ------------------------------------------------------------------ assemble the form
  const form = h('div', { class: 'ps-form' },
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
      h('div', { class: 'two' }, numberField('margin', 'Margin around the photos'), numberField('gap', 'Space between photos')),
      select('mode', 'How many', [['fill', 'Fill the page'], ['grid', 'A grid (columns × rows)'], ['count', 'A number of photos']], { onchange: (v) => { S.mode = v; syncMode(); } }),
      gridRow, countRow),
    h('section', { class: 'card ps-card' }, sectionHead('check', 'Finishing'),
      check('marks', 'Cut marks', 'Small ticks beside the grid to guide the scissors or cutter.'),
      check('outline', 'Thin outline around each photo', 'Easier to cut along.'),
      select('fit', 'If the photo has a different shape', [['fill', 'Crop it to the size'], ['fit', 'Show all of it (white edges)']]),
      cropBox),
    printBox);

  // the grid, count and crop controls are built once and shown or hidden
  gridRow.append(plainNumber('cols', 'Columns'), plainNumber('rows', 'Rows'));
  countRow.append(plainNumber('count', 'Number of photos', { max: 600 }));
  const cropX = h('input', { type: 'range', min: 0, max: 100, value: Math.round(S.cropX * 100), id: 'ps-cropx', 'aria-label': 'Move the photo left or right in its frame', title: 'Move the photo left or right in its frame',
    oninput: () => { S.cropX = cropX.value / 100; changed(); } });
  const cropY = h('input', { type: 'range', min: 0, max: 100, value: Math.round(S.cropY * 100), id: 'ps-cropy', 'aria-label': 'Move the photo up or down in its frame', title: 'Move the photo up or down in its frame',
    oninput: () => { S.cropY = cropY.value / 100; changed(); } });
  cropBox.append(h('p', { class: 'hint' }, 'The photo is cropped to fit. Move it in its frame:'),
    h('label', { class: 'range' }, h('span', {}, 'Left / right'), cropX), h('label', { class: 'range' }, h('span', {}, 'Up / down'), cropY));
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
  const side = h('aside', { class: 'ps-side' }, h('div', { class: 'card ps-card sticky' }, h('div', { class: 'sheet-wrap' }, canvas), summary, warnings, actionRow('wide-only')));
  form.append(h('section', { class: 'card ps-card narrow-only' }, sectionHead('printer', 'Make the sheet'), actionRow()));
  root.append(h('div', { class: 'ps-layout' }, form, side));

  function cover(iw, ih, c, fit) {  // where the picture sits inside a cell (mm); the same rule as the PDF writer
    const s = fit === 'fit' ? Math.min(c.w / iw, c.h / ih) : Math.max(c.w / iw, c.h / ih);
    const dw = iw * s, dh = ih * s;
    return { dw, dh, x: c.x + (c.w - dw) * (dw > c.w ? S.cropX : 0.5), y: c.y + (c.h - dh) * (dh > c.h ? S.cropY : 0.5) };
  }
  function drawPreview() {
    const ratio = window.devicePixelRatio || 1;
    const L = layout;
    const pageW = L ? L.page_w : currentPaper().width, pageH = L ? L.page_h : currentPaper().height;
    const slim = window.matchMedia('(max-width: 900px)').matches;  // a phone: the preview is a slim strip above the settings
    const maxH = slim ? Math.max(150, window.innerHeight * 0.28) : Math.max(240, window.innerHeight - 400);
    let cssW = Math.min(canvas.parentElement.clientWidth || 360, 460);
    if ((pageH * cssW) / pageW > maxH) cssW = (maxH * pageW) / pageH;  // never taller than the screen allows
    const k = cssW / pageW;
    canvas.width = Math.round(cssW * ratio); canvas.height = Math.round(pageH * k * ratio);
    canvas.style.setProperty('width', `${cssW}px`); canvas.style.setProperty('height', `${pageH * k}px`);
    const ctx = canvas.getContext('2d');
    ctx.setTransform(ratio * k, 0, 0, ratio * k, 0, 0);
    ctx.fillStyle = '#fff'; ctx.fillRect(0, 0, pageW, pageH);
    if (!L) return;
    ctx.lineWidth = 0.25; ctx.strokeStyle = '#444';
    for (const [n, c] of L.cells.entries()) {
      ctx.save();
      ctx.beginPath(); ctx.rect(c.x, c.y, c.w, c.h); ctx.clip();
      const img = images[L.assign ? L.assign[n] : 0] || images[0];
      if (img) { const p = cover(img.width, img.height, c, S.fit); ctx.drawImage(img.orig, p.x, p.y, p.dw, p.dh); }
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
      align: S.align, fit: S.fit, crop_x: S.cropX, crop_y: S.cropY, marks: S.marks, outline: S.outline,
      min_margin: minMargin(), borderless: Boolean(paper.borderless),
      counts: images.length ? images.map((i) => i.copies) : undefined, fill_page: S.fillPage,
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
    updateCropBox();
    clearTimeout(timer);
    timer = setTimeout(refreshLayout, 120);
  }
  async function refreshLayout() {
    const mine = ++token;
    const res = await api('/photosheet/layout', { method: 'POST', body: { ...settings(), images: images.map((i) => ({ width: i.width, height: i.height })) } });
    if (mine !== token) return;
    warnings.replaceChildren();
    if (!res.ok) {
      layout = null;
      summary.textContent = '';
      warnings.append(h('div', { class: 'notice bad' }, icon('alert', 18), h('div', {}, res.error?.message || 'Those settings can’t be laid out.')));
    } else {
      layout = res.data;
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
    updateCropBox();
    paintResults();
    drawPreview();
  }
  const updateCropBox = () => { cropBox.hidden = !(S.fit === 'fill' && layout?.cells?.length && images.some((i) => Math.abs(i.width / i.height - S.photoW / S.photoH) > 0.01)); };
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
  root.append(dialog);

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
  syncFields();
  paintPhotos();
  drawPreview();
  kit.printer.subscribe(() => loadConfig());
  window.addEventListener('resize', () => { if (!root.hidden) drawPreview(); });
  await loadConfig();
}
