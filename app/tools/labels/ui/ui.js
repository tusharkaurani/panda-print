// Label Sheet Maker: pick a sheet of labels, say what goes on them (typed, or from a spreadsheet), see the sheet,
// then download the PDF or print it at actual size. The server lays the text out (the same numbers make the PDF);
// the page draws that as a live preview.
import { drawPdfBytes } from '/tools/maintenance/preview.js';

const UNITS = { mm: { f: 1, dp: 1 }, cm: { f: 10, dp: 2 }, in: { f: 25.4, dp: 3 } };
const KEY = 'pp-labels';
const SAMPLE = 'Ada Lovelace\n12 Analytical Way\nLondon\n\nGrace Hopper\n1 Cobol Court\nArlington';
const SAMPLE_TEMPLATE = '{Name}\n{Street}\n{City}';

export async function mount(root, kit) {
  const { h, icon, api, toast, ui } = kit;
  root.classList.add('lb');

  // ------------------------------------------------------------------ state
  const S = {
    unit: 'mm', template: 'l7160', paper: 'a4',
    cols: 3, rows: 7, width: 63.5, height: 38.1, left: 7.2, top: 15.1, gapX: 2.5, gapY: 0, radius: 2,
    mode: 'text', text: SAMPLE, perLine: false, csv: '', csvName: '', csvTemplate: '',
    auto: false, size: 11, align: 'left', valign: 'middle', bold: false, firstBold: false, pad: 3,
    copies: 1, fill: false, start: 1, outline: false, page: 1,
    mediaType: '', quality: '', resolution: '', sheetCopies: 1,
  };
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch { /* storage is optional */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch { /* storage is optional */ } };
  let config = null;     // GET /labels/config
  let printer = null;    // this printer's choices, if CUPS is set up
  let layout = null;     // the last good answer of POST /labels/layout
  let columns = [];      // the spreadsheet's column names
  let token = 0;

  const fmt = (v) => String(+(v / UNITS[S.unit].f).toFixed(UNITS[S.unit].dp));
  const templates = () => config?.templates || [];
  const current = () => templates().find((t) => t.id === S.template) || null;

  // ------------------------------------------------------------------ the paper
  const papers = () => (printer?.media?.length ? printer.media.map((m) => ({ ...m, id: m.keyword })) : (config?.papers || []).map((m) => ({ ...m, id: m.key })));
  const PAPER_MM = { a4: [210, 297], letter: [215.9, 279.4] };
  function paperSize() {
    const t = current();
    if (t) return [t.paper_width, t.paper_height];
    if (S.paper === 'custom') return [Math.min(S.customW || 210, S.customH || 297), Math.max(S.customW || 210, S.customH || 297)];
    const p = papers().find((x) => x.id === S.paper);
    return p ? [Math.min(p.width, p.height), Math.max(p.width, p.height)] : PAPER_MM[S.paper] || PAPER_MM.a4;
  }
  // the printer's own names for the paper this sheet is on (more than one when it also offers a borderless version)
  const matchingMedia = () => {
    const [w, hgt] = paperSize();
    return papers().filter((m) => m.keyword && Math.abs(Math.min(m.width, m.height) - w) <= 1 && Math.abs(Math.max(m.width, m.height) - hgt) <= 1);
  };
  const minMargin = () => (printer?.min_margin ?? config?.fallback_min_margin ?? 3);

  // ------------------------------------------------------------------ small form helpers
  const fields = {};
  const fillSelect = (el, options, value) => el.replaceChildren(...options.map(([v, text]) => h('option', { value: v, selected: String(v) === String(value) ? true : null }, text)));
  function select(key, label, options, { hint, onchange } = {}) {
    const el = h('select', { class: 'input', id: `lb-${key}`, onchange: () => { S[key] = el.value; onchange?.(el.value); changed(); } });
    fields[key] = el;
    fillSelect(el, options, S[key]);
    return h('div', { class: 'field' }, h('label', { for: `lb-${key}` }, label), el, hint && h('p', { class: 'hint' }, hint));
  }
  function whole(key, label, { min = 1, max = 99, hint, custom = false } = {}) {
    const el = h('input', { class: 'input', type: 'number', min, max, step: 1, id: `lb-${key}`, value: S[key], autocomplete: 'off',
      oninput: () => { const v = parseInt(el.value, 10); if (Number.isFinite(v)) { S[key] = v; if (custom) S.template = ''; syncSheet(); changed(); } } });
    fields[key] = el;
    return h('div', { class: 'field' }, h('label', { for: `lb-${key}` }, label), el, hint && h('p', { class: 'hint' }, hint));
  }
  function length(key, label) {  // a measurement kept in mm, shown in the chosen unit
    const el = h('input', { class: 'input', type: 'number', min: 0, step: 'any', id: `lb-${key}`, autocomplete: 'off',
      oninput: () => { const v = parseFloat(el.value); if (Number.isFinite(v)) { S[key] = v * UNITS[S.unit].f; S.template = ''; syncSheet(); changed(); } } });
    fields[key] = el;
    return h('div', { class: 'field' }, h('label', { for: `lb-${key}` }, label, ' (', h('span', { class: 'unit-name' }, S.unit), ')'), el);
  }
  const check = (key, label, hint) => {
    const el = h('input', { type: 'checkbox', id: `lb-${key}`, checked: S[key] ? true : null, onchange: () => { S[key] = el.checked; changed(); } });
    fields[key] = el;
    return h('label', { class: 'check-row', for: `lb-${key}` }, el, h('span', {}, h('strong', {}, label), hint && h('span', { class: 'hint' }, hint)));
  };
  const head = (name, text) => h('h3', { class: 'lb-head' }, icon(name, 18), text);

  // ------------------------------------------------------------------ 1. the sheet of labels
  const sheetNote = h('p', { class: 'hint' });
  const templateSelect = h('select', { class: 'input', id: 'lb-template', onchange: () => { pickTemplate(templateSelect.value); changed(); } });
  const sheetBox = h('section', { class: 'card lb-card' }, head('list', 'The label sheet'),
    h('div', { class: 'field' }, h('label', { for: 'lb-template' }, 'Which sheet are you printing on?'), templateSelect, sheetNote),
    h('details', { class: 'adjust', id: 'lb-measure' }, h('summary', {}, icon('chevron', 16), 'Measurements'),
      h('div', { class: 'adjust-body' },
        h('div', { class: 'two' },
          select('unit', 'Show measurements in', [['mm', 'Millimetres'], ['cm', 'Centimetres'], ['in', 'Inches']], { onchange: () => syncSheet() }),
          select('paper', 'Paper', [['a4', 'A4']], { onchange: () => { S.template = ''; syncSheet(); } })),
        h('div', { class: 'two' }, whole('cols', 'Labels across', { max: 30, custom: true }), whole('rows', 'Labels down', { max: 30, custom: true })),
        h('div', { class: 'two' }, length('width', 'Label width'), length('height', 'Label height')),
        h('div', { class: 'two' }, length('left', 'Left edge to first label'), length('top', 'Top edge to first label')),
        h('div', { class: 'two' }, length('gapX', 'Space between, across'), length('gapY', 'Space between, down')),
        length('radius', 'Corner rounding'),
        h('p', { class: 'hint' }, 'Measure the first label on your sheet with a ruler if the printout doesn’t line up. Editing anything here switches to a custom sheet.'))));

  function pickTemplate(id) {
    const t = templates().find((x) => x.id === id);
    S.template = t ? t.id : '';
    if (t) Object.assign(S, { paper: t.paper, cols: t.cols, rows: t.rows, width: t.width, height: t.height, left: t.left, top: t.top, gapX: t.gap_x, gapY: t.gap_y, radius: t.radius });
    S.start = 1; S.page = 1;
    syncSheet();
  }
  function syncSheet() {
    const t = current();
    const opts = [...templates().map((x) => [x.id, x.name]), ['', 'My own grid (enter the measurements)']];
    fillSelect(templateSelect, opts, S.template);
    templateSelect.value = S.template;
    const list = (config?.papers || []).map((p) => [p.key, p.label]);
    if (fields.paper) { fillSelect(fields.paper, list.length ? list : [['a4', 'A4']], S.paper); fields.paper.disabled = Boolean(t); }
    for (const k of ['width', 'height', 'left', 'top', 'gapX', 'gapY', 'radius']) if (fields[k] && document.activeElement !== fields[k]) fields[k].value = fmt(S[k]);
    for (const k of ['cols', 'rows']) if (fields[k] && document.activeElement !== fields[k]) fields[k].value = S[k];
    if (fields.unit) fields.unit.value = S.unit;
    for (const el of root.querySelectorAll('.unit-name')) el.textContent = S.unit;
    const [pw, ph] = paperSize();
    sheetNote.textContent = `${S.cols * S.rows} labels on each sheet, ${fmt(S.width)} × ${fmt(S.height)} ${S.unit} each, on ${t ? (t.paper === 'a4' ? 'A4' : 'Letter') : `${fmt(pw)} × ${fmt(ph)} ${S.unit} paper`}.`;
    if (fields.start) fields.start.max = S.cols * S.rows;
  }

  // ------------------------------------------------------------------ 2. what goes on the labels
  const textArea = h('textarea', { class: 'input lb-text', id: 'lb-text', rows: 8, spellcheck: 'false', 'aria-label': 'The labels',
    placeholder: 'Ada Lovelace\n12 Analytical Way\nLondon\n\nGrace Hopper\n…', oninput: () => { S.text = textArea.value; S.start = Math.min(S.start, S.cols * S.rows); changed(); } });
  textArea.value = S.text;
  const csvArea = h('textarea', { class: 'input lb-text', id: 'lb-csv', rows: 6, spellcheck: 'false', 'aria-label': 'The spreadsheet',
    placeholder: 'Name,Street,City\nAda Lovelace,12 Analytical Way,London', oninput: () => { S.csv = csvArea.value; S.csvName = ''; changed(); } });
  csvArea.value = S.csv;
  const tplArea = h('textarea', { class: 'input lb-text', id: 'lb-csvtemplate', rows: 4, spellcheck: 'false', 'aria-label': 'What each label says',
    placeholder: SAMPLE_TEMPLATE, oninput: () => { S.csvTemplate = tplArea.value; changed(); } });
  tplArea.value = S.csvTemplate;
  const fileInput = h('input', { type: 'file', accept: '.csv,.tsv,.txt,text/csv,text/plain', hidden: true, onchange: async () => {
    const f = fileInput.files[0]; fileInput.value = '';
    if (!f) return;
    if (f.size > 400_000) { toast('That file is too large. Use fewer rows.', 'bad'); return; }
    S.csv = await f.text(); S.csvName = f.name; csvArea.value = S.csv; changed();
  } });
  const colChips = h('div', { class: 'chips' });
  const csvNote = h('p', { class: 'hint' });
  const textPane = h('div', { class: 'lb-pane' },
    textArea,
    select('perLine', 'Each label is', [['false', 'A block of lines (a blank line between labels)'], ['true', 'One line']], { hint: 'Use blocks for addresses. A long line wraps by itself.', onchange: (v) => { S.perLine = v === 'true'; } }));
  const csvPane = h('div', { class: 'lb-pane' },
    h('div', { class: 'actions left' },
      h('button', { class: 'btn small', type: 'button', title: 'Choose a CSV file saved from Excel, Numbers or Google Sheets', onclick: () => fileInput.click() }, icon('file', 15), 'Choose a CSV file…'), fileInput,
      h('span', { class: 'hint', id: 'lb-csvname' })),
    csvArea, csvNote,
    h('div', { class: 'field' }, h('label', { for: 'lb-csvtemplate' }, 'What each label says'), tplArea,
      h('p', { class: 'hint' }, 'Put a column name in { } where its value goes. Leave this empty to print every column, one per line. A line whose values are all empty is left out.'), colChips));
  const modeSelect = h('select', { class: 'input', id: 'lb-mode', 'aria-label': 'Where the label text comes from', onchange: () => { S.mode = modeSelect.value; syncMode(); changed(); } });
  fillSelect(modeSelect, [['text', 'Type or paste the labels'], ['csv', 'From a spreadsheet (CSV)']], S.mode);
  const contentBox = h('section', { class: 'card lb-card' }, head('file', 'What goes on the labels'),
    h('div', { class: 'field' }, h('label', { for: 'lb-mode' }, 'Labels from'), modeSelect), textPane, csvPane);
  function syncMode() {
    textPane.hidden = S.mode !== 'text'; csvPane.hidden = S.mode !== 'csv';
    const name = root.querySelector('#lb-csvname'); if (name) name.textContent = S.csvName;
    const per = fields.perLine; if (per) per.value = String(S.perLine);
  }
  function paintColumns() {
    colChips.replaceChildren(...columns.map((c) => h('button', { class: 'chip', type: 'button', title: `Add {${c}} to the label`, onclick: () => {
      const at = tplArea.selectionStart ?? tplArea.value.length;
      tplArea.value = `${tplArea.value.slice(0, at)}{${c}}${tplArea.value.slice(at)}`; S.csvTemplate = tplArea.value; tplArea.focus(); changed();
    } }, `{${c}}`)));
  }

  // ------------------------------------------------------------------ 3. the text
  const sizeBox = h('div', { class: 'field' });
  const sizeInput = h('input', { class: 'input', type: 'number', min: 5, max: 72, step: 0.5, id: 'lb-size', value: S.size, 'aria-label': 'Text size in points',
    oninput: () => { const v = parseFloat(sizeInput.value); if (Number.isFinite(v)) { S.size = v; changed(); } } });
  const autoCheck = h('input', { type: 'checkbox', id: 'lb-auto', checked: S.auto ? true : null, onchange: () => { S.auto = autoCheck.checked; sizeInput.disabled = S.auto; changed(); } });
  sizeInput.disabled = Boolean(S.auto);
  sizeBox.append(h('label', { for: 'lb-size' }, 'Text size (points)'), sizeInput,
    h('label', { class: 'check-row slim', for: 'lb-auto' }, autoCheck, h('span', {}, 'Make the text as large as fits every label')));
  const textBox = h('section', { class: 'card lb-card' }, head('eye', 'The text'),
    h('div', { class: 'two' }, sizeBox,
      h('div', { class: 'field' }, h('label', { for: 'lb-pad' }, 'Space inside labels (mm)'),
        h('input', { class: 'input', type: 'number', min: 0, max: 20, step: 0.5, id: 'lb-pad', value: S.pad, oninput: (e) => { const v = parseFloat(e.target.value); if (Number.isFinite(v)) { S.pad = v; changed(); } } }))),
    h('div', { class: 'two' },
      select('align', 'Across the label', [['left', 'Left'], ['center', 'Centre'], ['right', 'Right']]),
      select('valign', 'Up and down', [['middle', 'Middle'], ['top', 'Top']])),
    check('firstBold', 'First line in bold', 'Handy for names.'), check('bold', 'All text in bold'));

  // ------------------------------------------------------------------ 4. how many
  const countBox = h('section', { class: 'card lb-card' }, head('hash', 'How many'),
    h('div', { class: 'two' }, whole('copies', 'Copies of each label', { max: 100 }),
      whole('start', 'Start at label number', { max: 100, hint: 'For a part-used sheet. You can also click a label in the preview.' })),
    check('fill', 'Repeat the list to fill the sheet', 'Good for one return-address label ×30.'),
    check('outline', 'Show label borders', 'For a test on plain paper. Leave off when printing on real labels.'));

  // ------------------------------------------------------------------ 5. printing choices (only with CUPS)
  const printBox = h('section', { class: 'card lb-card' });
  function syncPrinter() {
    const p = kit.printer.current;
    if (!p) { printBox.replaceChildren(head('printer', 'Printing'), h('p', { class: 'hint' }, 'Add a printer to print from here. You can still download the PDF.')); return; }
    if (!printer || printer.configured === false) {
      printBox.replaceChildren(head('printer', 'Printing'),
        h('p', { class: 'hint' }, `${p.name} has no CUPS print queue, so it can’t print from here. Download the PDF and print it from your computer, or set up CUPS and choose the queue under Printers → Edit.`));
      return;
    }
    if (printer.error) { printBox.replaceChildren(head('printer', 'Printing'), ui.errorBox(printer.error, loadConfig)); return; }
    const types = [['', 'Printer’s own choice'], ...printer.media_types.map((t) => [t.value, t.label])];
    if (!S.mediaType && printer.media_types.some((t) => t.value === 'labels')) S.mediaType = 'labels';
    const qualities = [['', 'Printer’s own choice'], ...printer.qualities.map((q) => [q.value, q.label])];
    const match = matchingMedia();
    printBox.replaceChildren(head('printer', `Print on ${p.name}`),
      match.length
        ? h('p', { class: 'hint' }, `Printed on ${match[0].label}, the paper this label sheet is on.`)
        : h('p', { class: 'msg' }, `${p.name} doesn’t list this paper size, so printing is switched off. Download the PDF instead.`),
      h('div', { class: 'grid2' },
        select('mediaType', 'Paper type', types, { hint: 'Label sheets usually print best as “Labels”.' }),
        select('quality', 'Print quality', qualities),
        h('div', { class: 'field' }, h('label', { for: 'lb-sheetCopies' }, 'Copies of the sheet'),
          h('input', { class: 'input', type: 'number', min: 1, max: 20, id: 'lb-sheetCopies', value: S.sheetCopies, oninput: (e) => { const v = parseInt(e.target.value, 10); if (Number.isFinite(v)) { S.sheetCopies = v; changed(); } } }))),
      printer.actual_size ? h('p', { class: 'hint' }, 'Printed at actual size (100%), so the text lands on the labels. Turn off “fit to page” in any other print dialog.')
        : h('p', { class: 'msg' }, 'This print queue can’t print at actual size, so printing is switched off. Download the PDF instead.'));
  }
  const canPrint = () => Boolean(printer && printer.configured !== false && !printer.error && printer.actual_size && matchingMedia().length);

  // ------------------------------------------------------------------ the preview
  const canvas = h('canvas', { class: 'sheet-canvas', role: 'img', 'aria-label': 'Preview of the label sheet', title: 'Click a label to start printing from it' });
  const summary = h('p', { class: 'summary' });
  const warnings = h('div', { class: 'warns' });
  const pager = h('div', { class: 'pager', hidden: true });
  const downloadBtns = [], printBtns = [];
  const dlBtn = () => { const b = h('button', { class: 'btn', type: 'button', title: 'Save the labels as a PDF file', onclick: download }, icon('download', 16), 'Download PDF'); downloadBtns.push(b); return b; };
  const prBtn = () => { const b = h('button', { class: 'btn primary', type: 'button', title: 'Show the sheet, then print it on the selected printer', onclick: openPrint }, icon('printer', 16), 'Print…'); printBtns.push(b); return b; };
  const previewCard = h('section', { class: 'card ps-card preview-card' }, head('eye', 'Preview'), h('div', { class: 'sheet-wrap' }, canvas), pager, summary, warnings);
  const sideButtons = h('div', { class: 'actions center wide-only' }, dlBtn(), prBtn());
  const bottomButtons = h('div', { class: 'actions center narrow-only' }, dlBtn(), prBtn());

  function paintPager() {
    const n = layout?.sheets || 1;
    pager.hidden = n < 2;
    if (n < 2) return;
    const go = (d) => { S.page = Math.min(Math.max(S.page + d, 1), n); changed(); };
    pager.replaceChildren(
      h('button', { class: 'btn small', type: 'button', disabled: S.page <= 1 ? true : null, title: 'Show the previous sheet', 'aria-label': 'Previous sheet', onclick: () => go(-1) }, icon('chevron', 14)),
      h('span', {}, `Sheet ${S.page} of ${n}`),
      h('button', { class: 'btn small', type: 'button', disabled: S.page >= n ? true : null, title: 'Show the next sheet', 'aria-label': 'Next sheet', onclick: () => go(1) }, icon('chevron', 14)));
  }

  function drawPreview() {
    const ratio = window.devicePixelRatio || 1;
    const L = layout;
    const [pw, ph] = L ? [L.page_w, L.page_h] : paperSize();
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
    const skip = L.page === 1 ? S.start - 1 : 0;
    ctx.lineWidth = 0.25;
    L.cells.forEach((c, n) => {
      const r = Math.min(L.radius || 0, c.w / 2, c.h / 2);
      ctx.beginPath();
      if (r > 0 && ctx.roundRect) ctx.roundRect(c.x, c.y, c.w, c.h, r); else ctx.rect(c.x, c.y, c.w, c.h);
      if (n < skip) { ctx.fillStyle = '#e5e7eb'; ctx.fill(); }  // places already used
      ctx.strokeStyle = S.outline ? '#888' : '#d3d8df'; ctx.setLineDash(S.outline ? [] : [1.2, 1]); ctx.stroke(); ctx.setLineDash([]);
    });
    ctx.fillStyle = '#111'; ctx.textBaseline = 'alphabetic';
    const px = L.size * 25.4 / 72;
    for (const c of L.cells) for (const ln of c.lines) {
      ctx.font = `${ln.bold ? 'bold ' : ''}${px}px Helvetica, Arial, sans-serif`;
      ctx.fillText(ln.t, ln.x, ln.y);
    }
  }
  canvas.addEventListener('click', (ev) => {
    if (!layout) return;
    const box = canvas.getBoundingClientRect();
    const x = ((ev.clientX - box.left) / box.width) * layout.page_w, y = ((ev.clientY - box.top) / box.height) * layout.page_h;
    const i = layout.cells.findIndex((c) => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
    if (i < 0) return;
    if (layout.page > 1) { toast('Choose the start on the first sheet.'); return; }
    S.start = i + 1; fields.start.value = S.start; changed();
  });

  // ------------------------------------------------------------------ settings -> server
  function request() {
    const [pw, ph] = paperSize();
    return {
      sheet: { paper: { width: pw, height: ph }, cols: S.cols, rows: S.rows, width: S.width, height: S.height, left: S.left, top: S.top, gap_x: S.gapX, gap_y: S.gapY, radius: S.radius },
      content: { mode: S.mode, text: S.text, per_line: S.perLine, csv: S.csv, template: S.csvTemplate },
      style: { size: S.auto ? null : S.size, align: S.align, valign: S.valign, bold: S.bold, first_bold: S.firstBold, pad: S.pad },
      copies: S.copies, fill: S.fill, start: S.start, outline: S.outline, page: S.page,
      min_margin: minMargin(), borderless: matchingMedia().some((m) => m.borderless),
    };
  }
  let timer = 0;
  function changed() {
    save();
    syncSheet();
    clearTimeout(timer);
    timer = setTimeout(refresh, 120);
    syncButtons();
  }
  function syncButtons() {
    const ok = Boolean(layout && layout.labels > 0);
    for (const b of downloadBtns) b.disabled = !ok;
    for (const b of printBtns) {
      b.disabled = !ok || !canPrint();
      b.title = canPrint() ? 'Show the sheet, then print it on the selected printer' : 'Needs a printer with a CUPS queue that offers this paper size. You can still download the PDF.';
    }
  }
  async function refresh() {
    const mine = ++token;
    const res = await api('/labels/layout', { method: 'POST', body: request() });
    if (mine !== token) return;
    warnings.replaceChildren();
    if (!res.ok) {
      layout = null; summary.textContent = ''; pager.hidden = true;
      warnings.append(h('div', { class: 'notice bad' }, icon('alert', 18), h('div', {}, res.error?.message || 'Those settings can’t be laid out.')));
      syncButtons(); drawPreview();
      return;
    }
    layout = res.data;
    if (S.page > layout.sheets) { S.page = layout.sheets; }
    columns = layout.columns || [];
    paintColumns();
    csvNote.textContent = S.mode === 'csv' && columns.length ? `${layout.labels} labels from the spreadsheet. Columns: ${columns.join(', ')}.` : '';
    summary.textContent = layout.labels
      ? `${layout.labels} label${layout.labels === 1 ? '' : 's'} on ${layout.sheets} sheet${layout.sheets === 1 ? '' : 's'}, text ${+layout.size.toFixed(1)} pt.`
      : 'Type something to see it on the labels.';
    warnings.append(...layout.warnings.map((w) => h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, w))));
    paintPager(); syncButtons(); drawPreview();
  }

  // ------------------------------------------------------------------ download and print
  async function download() {
    for (const b of downloadBtns) b.disabled = true;
    const res = await api('/labels/pdf?download=1', { method: 'POST', body: request() });
    syncButtons();
    if (!res.ok) { toast(res.error?.message || 'Could not make the PDF.', 'bad'); return; }
    const url = URL.createObjectURL(res.data);
    const a = h('a', { href: url, download: 'labels.pdf' });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
  const sheets = h('div', { class: 'sheets' });
  const dlgInfo = h('p', { class: 'sub' });
  const dlgWarn = h('div', {});
  const goBtn = h('button', { class: 'btn primary', type: 'button', title: 'Send the labels to the printer now', onclick: () => doPrint() }, icon('printer', 16), 'Print');
  const dialog = h('dialog', { class: 'wide', 'aria-labelledby': 'lb-dlg-title' }, h('h3', { id: 'lb-dlg-title' }, 'Print these labels?'), dlgInfo, dlgWarn, sheets,
    h('p', { class: 'hint' }, 'This is the first sheet of the PDF that will be sent. Load the label sheet the right way up; the printout is a normal page.'),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Close without printing', onclick: () => dialog.close() }, 'Cancel'), goBtn));
  let dlgToken = 0;
  dialog.addEventListener('close', () => { if (dialog.open) return; dlgToken += 1; sheets.replaceChildren(); });
  root.append(dialog);

  const printOptions = () => ({ media: matchingMedia()[0]?.keyword, media_type: S.mediaType || null, quality: S.quality ? Number(S.quality) : null,
    resolution: null, copies: Number(S.sheetCopies) || 1 });

  async function openPrint() {
    const p = kit.printer.current;
    const typeLabel = printer.media_types.find((t) => t.value === S.mediaType)?.label;
    dlgInfo.textContent = `${layout.labels} label${layout.labels === 1 ? '' : 's'} on ${layout.sheets} sheet${layout.sheets === 1 ? '' : 's'} of ${matchingMedia()[0].label}${typeLabel ? `, ${typeLabel.toLowerCase()}` : ''}, at actual size, to ${p.name}${S.sheetCopies > 1 ? ` (${S.sheetCopies} copies)` : ''}.`;
    dlgWarn.replaceChildren(...layout.warnings.map((w) => h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, w))));
    const mine = ++dlgToken;
    dialog.showModal();
    sheets.replaceChildren(h('p', { class: 'empty' }, 'Drawing the sheet…'));
    const res = await api('/labels/pdf', { method: 'POST', body: request() });
    if (mine !== dlgToken) return;
    if (!res.ok) { sheets.replaceChildren(h('p', { class: 'empty' }, res.error?.message || 'Could not draw the sheet.')); return; }
    try { await drawPdfBytes(kit, new Uint8Array(await res.data.arrayBuffer()), sheets, { alive: () => mine === dlgToken, width: Math.min(300, dialog.clientWidth - 80), maxPages: 1 }); }
    catch { sheets.replaceChildren(h('p', { class: 'empty' }, 'The preview could not be drawn, but the sheet is ready to print.')); }
  }
  async function doPrint() {
    const p = kit.printer.current;
    goBtn.disabled = true;
    const res = await api('/labels/print', { method: 'POST', body: { ...request(), printer: p.id, options: printOptions() } });
    goBtn.disabled = false;
    if (res.ok) { dialog.close(); toast(`Sent to ${p.name} (job #${res.data.job_id}).`); return; }
    toast(res.error?.message || 'Could not print.', 'bad');
  }

  // ------------------------------------------------------------------ layout of the page
  root.append(h('div', { class: 'ps-layout' },
    h('div', { class: 'ps-form' }, sheetBox, contentBox, textBox, countBox, printBox, bottomButtons),
    h('div', { class: 'ps-side' }, h('div', { class: 'sticky-col' }, previewCard, sideButtons))));

  async function loadConfig() {
    const id = kit.printer.current?.id;
    const res = await api(`/labels/config${id ? `?printer=${encodeURIComponent(id)}` : ''}`);
    if (!res.ok) { toast(res.error?.message || 'Could not load the label sheets.', 'bad'); return; }
    config = res.data;
    printer = config.printer;
    if (S.template && !current()) S.template = '';
    syncSheet(); syncPrinter(); syncMode();
    changed();
  }

  // The quick-links strip isn't useful here and would cut the pinned preview short at the end of the page.
  const view = root.parentElement;
  const hideLauncher = () => document.body.toggleAttribute('data-hide-launcher', !view.hidden);
  new MutationObserver(hideLauncher).observe(view, { attributes: true, attributeFilter: ['hidden'] });
  hideLauncher();

  syncMode();
  paintColumns();
  drawPreview();
  kit.printer.subscribe(() => loadConfig());
  window.addEventListener('resize', () => { if (!root.hidden) drawPreview(); });
  await loadConfig();
}
