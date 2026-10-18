// Label Sheet Maker: pick a label pack or a plain sheet, say what goes on the labels (typed, or from a spreadsheet), style
// them, see the sheet, then download the PDF or print it at actual size. The server lays the text out (the same numbers
// make the PDF); the page draws that as a live preview. The page follows the Passport Sheet Maker's layout.
import { drawPdfBytes } from '/tools/maintenance/preview.js';

const UNITS = { mm: { f: 1, dp: 1 }, cm: { f: 10, dp: 2 }, in: { f: 25.4, dp: 3 } };
const KEY = 'pp-labels';
const MAX_LOGO_SIDE = 1400;  // px: a bigger logo is scaled down before it is sent
const SAMPLE = 'Ada Lovelace\n12 Analytical Way\nLondon\n\nGrace Hopper\n1 Cobol Court\nArlington';
const FONT_CSS = { sans: 'Helvetica, Arial, sans-serif', serif: '"Times New Roman", Times, serif', mono: '"Courier New", Courier, monospace' };

export async function mount(root, kit) {
  const { h, icon, api, toast, ui } = kit;
  root.classList.add('lb');

  // ------------------------------------------------------------------ state (lengths in mm)
  const S = {
    unit: 'mm', kind: 'pack', pack: 'l7160', paper: 'a4', customW: 210, customH: 297,
    packCols: 3, packRows: 7, width: 63.5, height: 38.1, left: 7.2, top: 15.1, gapX: 2.5, gapY: 0,
    orientation: 'auto', align: 'center', area: 'full', margin: 5, gap: 2, mode: 'fill', cols: 4, rows: 6, count: 12, blanks: 'none', blankCount: 5,
    shape: 'rounded', radius: 2, fill: '', border: '', borderWidth: 0.3, color: '#000000', family: 'sans', bold: false, italic: false,
    auto: false, size: 11, textAlign: 'left', valign: 'middle', pad: 3, lineSpacing: 1.2, firstBold: false, firstScale: 1, firstColor: '',
    logoPosition: 'top', logoPct: 30,
    inputMode: 'text', text: SAMPLE, perLine: false, csv: '', csvName: '', csvTemplate: '',
    copies: 1, repeat: false, start: 1, cut: 'none', inset: 0, marks: false, page: 1,
    mediaType: '', quality: '', resolution: '', sheetCopies: 1,
  };
  try { Object.assign(S, JSON.parse(localStorage.getItem(KEY) || '{}')); } catch { /* storage is optional */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch { /* storage is optional */ } };
  let config = null;     // GET /labels/config
  let printer = null;    // this printer's choices, if CUPS is set up
  let layout = null;     // the last good answer of POST /labels/layout
  let columns = [];      // the spreadsheet's column names
  let logo = null;       // {bitmap, blob, width, height, name}: kept in memory only
  let presets = [];
  let token = 0;

  const mm = (v) => v * UNITS[S.unit].f;
  const shown = (v) => String(+(v / UNITS[S.unit].f).toFixed(UNITS[S.unit].dp));
  const templates = () => config?.templates || [];
  const pack = () => (S.kind === 'pack' ? templates().find((t) => t.id === S.pack) || null : null);

  // ------------------------------------------------------------------ the paper list: the printer's, else ours
  const papers = () => (printer?.media?.length ? printer.media.map((m) => ({ ...m, id: m.keyword })) : (config?.papers || []).map((m) => ({ ...m, id: m.key })));
  function currentPaper() {
    if (S.paper === 'custom') return { id: 'custom', width: Math.min(S.customW, S.customH), height: Math.max(S.customW, S.customH), label: 'Custom size', borderless: false };
    const list = papers();
    return list.find((p) => p.id === S.paper) || list[0] || { id: 'a4', width: 210, height: 297, label: 'A4', borderless: false };
  }
  function paperSize() {  // portrait, mm: a pack fixes it; otherwise it is the chosen paper
    const t = pack();
    if (t) return [t.paper_width, t.paper_height];
    const p = currentPaper();
    return [Math.min(p.width, p.height), Math.max(p.width, p.height)];
  }
  const matchingMedia = () => {
    const [w, hgt] = paperSize();
    return papers().filter((m) => m.keyword && Math.abs(Math.min(m.width, m.height) - w) <= 1 && Math.abs(Math.max(m.width, m.height) - hgt) <= 1);
  };
  const minMargin = () => (printer?.min_margin ?? config?.fallback_min_margin ?? 3);

  // ------------------------------------------------------------------ controls: each is bound to a state key
  const ctl = {};  // key -> { el, type }
  const bind = (key, el, type) => { ctl[key] = { el, type }; return el; };
  const wrapField = (id, label, el, hint) => h('div', { class: 'field' }, h('label', { for: id }, label), el, hint && h('p', { class: 'hint' }, hint));
  function select(key, label, options, { hint, onchange, id } = {}) {
    const el = bind(key, h('select', { class: 'input', id: id || `lb-${key}`, onchange: () => { S[key] = el.value; onchange?.(el.value); changed(); } }), 'select');
    el.replaceChildren(...options.map(([v, text]) => h('option', { value: v }, text)));
    return wrapField(id || `lb-${key}`, label, el, hint);
  }
  function length(key, label, { min = 0 } = {}) {  // a measurement kept in mm, shown in the chosen unit
    const el = bind(key, h('input', { class: 'input', type: 'number', min, step: 'any', id: `lb-${key}`, autocomplete: 'off',
      oninput: () => { const v = parseFloat(el.value); if (Number.isFinite(v)) { S[key] = v * UNITS[S.unit].f; touched(key); changed(); } } }), 'len');
    return h('div', { class: 'field' }, h('label', { for: `lb-${key}` }, label, ' (', h('span', { class: 'unit-name' }, S.unit), ')'), el);
  }
  function plain(key, label, { min = 1, max = 99, hint, step = 1, float = false } = {}) {
    const el = bind(key, h('input', { class: 'input', type: 'number', min, max, step, id: `lb-${key}`, autocomplete: 'off',
      oninput: () => { const v = float ? parseFloat(el.value) : parseInt(el.value, 10); if (Number.isFinite(v)) { S[key] = v; touched(key); changed(); } } }), 'plain');
    return wrapField(`lb-${key}`, label, el, hint);
  }
  const check = (key, label, hint) => {
    const el = bind(key, h('input', { type: 'checkbox', id: `lb-${key}`, onchange: () => { S[key] = el.checked; changed(); } }), 'check');
    return h('label', { class: 'check-row', for: `lb-${key}` }, el, h('span', {}, h('strong', {}, label), hint && h('span', { class: 'hint' }, hint)));
  };
  function colour(key, label, { optional = false, none = 'None' } = {}) {  // '' means none (when optional)
    const el = bind(key, h('input', { class: 'colour', type: 'color', id: `lb-${key}`, 'aria-label': label,
      oninput: () => { S[key] = el.value; if (off) off.checked = false; changed(); } }), 'color');
    const off = optional ? h('input', { type: 'checkbox', id: `lb-${key}-none`, 'aria-label': `${label}: ${none}`,
      onchange: () => { S[key] = off.checked ? '' : (el.value || '#cccccc'); changed(); } }) : null;
    if (off) ctl[`${key}-none`] = { el: off, type: 'none', of: key };
    return h('div', { class: 'field' }, h('label', { for: `lb-${key}` }, label),
      h('div', { class: 'colour-row' }, el, off && h('label', { class: 'check-row slim', for: `lb-${key}-none` }, off, h('span', {}, none))));
  }
  const touched = (key) => { if (['width', 'height', 'left', 'top', 'gapX', 'gapY', 'packCols', 'packRows'].includes(key) && S.kind === 'pack') S.pack = ''; };
  const head = (name, text) => h('h3', { class: 'lb-head' }, icon(name, 18), text);
  const fillOptions = (el, options) => el.replaceChildren(...options.map(([v, text]) => h('option', { value: v }, text)));

  // put every control in step with the state (a control being typed in is left alone)
  function paintControls() {
    for (const [key, c] of Object.entries(ctl)) {
      const { el } = c;
      if (c.type === 'none') { el.checked = S[c.of] === ''; continue; }
      if (document.activeElement === el) continue;
      if (c.type === 'check') el.checked = Boolean(S[key]);
      else if (c.type === 'len') el.value = shown(S[key]);
      else if (c.type === 'color') el.value = S[key] || '#cccccc';
      else el.value = String(S[key]);
    }
    for (const el of root.querySelectorAll('.unit-name')) el.textContent = S.unit;
  }

  // ------------------------------------------------------------------ 1. presets (quick starts, never saved back)
  const presetChips = h('div', { class: 'chips' });
  const presetBox = h('section', { class: 'card lb-card' }, head('list', 'Presets'),
    h('p', { class: 'hint' }, 'A preset fills in the settings below, as a quick start. Change anything afterwards; nothing is saved unless you save it as a new preset.'), presetChips,
    h('div', { class: 'actions left' }, h('button', { class: 'btn small', type: 'button', title: 'Save the sheet, look, text and print choices below (not the label text or logo) as a new preset to use again', onclick: () => openSave() }, icon('check', 15), 'Save these settings as a preset…')));

  // ------------------------------------------------------------------ 2. what goes on the labels
  const textArea = h('textarea', { class: 'input lb-text', id: 'lb-text', rows: 8, spellcheck: 'false', 'aria-label': 'The labels',
    placeholder: 'Ada Lovelace\n12 Analytical Way\nLondon\n\nGrace Hopper\n…', oninput: () => { S.text = textArea.value; changed(); } });
  const csvArea = h('textarea', { class: 'input lb-text', id: 'lb-csv', rows: 6, spellcheck: 'false', 'aria-label': 'The spreadsheet',
    placeholder: 'Name,Street,City\nAda Lovelace,12 Analytical Way,London', oninput: () => { S.csv = csvArea.value; S.csvName = ''; changed(); } });
  const tplArea = h('textarea', { class: 'input lb-text', id: 'lb-csvtemplate', rows: 4, spellcheck: 'false', 'aria-label': 'What each label says',
    placeholder: '{Name}\n{Street}\n{City}', oninput: () => { S.csvTemplate = tplArea.value; changed(); } });
  const fileInput = h('input', { type: 'file', accept: '.csv,.tsv,.txt,text/csv,text/plain', hidden: true, onchange: async () => {
    const f = fileInput.files[0]; fileInput.value = '';
    if (!f) return;
    if (f.size > 400_000) { toast('That file is too large. Use fewer rows.', 'bad'); return; }
    S.csv = await f.text(); S.csvName = f.name; csvArea.value = S.csv; changed();
  } });
  const colChips = h('div', { class: 'chips' });
  const csvNote = h('p', { class: 'hint' });
  const csvName = h('span', { class: 'hint', id: 'lb-csvname' });
  const textPane = h('div', { class: 'lb-pane' }, textArea,
    select('perLine', 'Each label is', [['false', 'A block of lines (a blank line between labels)'], ['true', 'One line']], { hint: 'Use blocks for addresses. A long line wraps by itself.', onchange: (v) => { S.perLine = v === 'true'; } }));
  const csvPane = h('div', { class: 'lb-pane' },
    h('div', { class: 'actions left' }, h('button', { class: 'btn small', type: 'button', title: 'Choose a CSV file saved from Excel, Numbers or Google Sheets', onclick: () => fileInput.click() }, icon('file', 15), 'Choose a CSV file…'), fileInput, csvName),
    csvArea, csvNote,
    h('div', { class: 'field' }, h('label', { for: 'lb-csvtemplate' }, 'What each label says'), tplArea,
      h('p', { class: 'hint' }, 'Put a column name in { } where its value goes. Leave this empty to print every column, one per line. A line whose values are all empty is left out.'), colChips));
  const blankCountField = plain('blankCount', 'How many blank labels', { min: 1, max: 5000 });
  const blankBox = h('div', { class: 'lb-pane' },
    select('blanks', 'After these labels', [['none', 'Leave the rest of the sheet empty'], ['fill', 'Fill the rest of the sheet with blank labels'], ['count', 'Add a number of blank labels']],
      { hint: 'Blank labels share the sheet with the printed ones and have the same colour, border and picture, but no text: to write on by hand. Leave the list empty to print only blank labels.', onchange: () => syncVisibility() }),
    blankCountField);
  const contentBox = h('section', { class: 'card lb-card' }, head('file', '4 · Your labels'),
    select('inputMode', 'Labels from', [['text', 'Type or paste the labels'], ['csv', 'From a spreadsheet (CSV)']], { onchange: () => syncVisibility() }), textPane, csvPane, blankBox);

  // ------------------------------------------------------------------ 3. the label sheet and the paper
  const unitSelect = select('unit', 'Unit', [['mm', 'mm'], ['cm', 'cm'], ['in', 'in']], { id: 'lb-unit' });
  const kindField = select('kind', 'The labels are on', [['pack', 'A label pack (sheet with printed outlines)'], ['size', 'A plain sheet (set the label size)']],
    { hint: 'Packs such as Avery have fixed places. A plain sheet of stickers has none, so you give the label size and it fills the page.', onchange: () => { S.start = 1; S.page = 1; applyKind(); } });
  const packField = select('pack', 'Label pack', [['', 'My own pack…']], { onchange: (v) => { pickPack(v); } });
  const packNote = h('p', { class: 'hint' });
  const packCustom = h('div', { class: 'lb-pane' },
    h('div', { class: 'two' }, plain('packCols', 'Labels across', { max: 30 }), plain('packRows', 'Labels down', { max: 30 })),
    h('div', { class: 'two' }, length('left', 'Left edge to first label'), length('top', 'Top edge to first label')),
    h('div', { class: 'two' }, length('gapX', 'Space between, across'), length('gapY', 'Space between, down')),
    h('p', { class: 'hint' }, 'Measure the first label on your sheet with a ruler if the printout doesn’t line up. Editing anything here switches to your own pack.'));
  const sizePick = h('select', { class: 'input', id: 'lb-sizepick', 'aria-label': 'Common label sizes', onchange: () => {
    const p = config?.sizes?.[Number(sizePick.value)];
    if (p) { S.width = p.width; S.height = p.height; S.shape = p.shape; S.start = 1; changed(); }
  } });
  const sizeFields = h('div', { class: 'lb-pane' },
    h('div', { class: 'field' }, h('label', { for: 'lb-sizepick' }, 'Common sizes'), sizePick));
  // paper
  const paperSelect = bind('paper', h('select', { class: 'input', id: 'lb-paper', onchange: () => { S.paper = paperSelect.value; syncPaper(); changed(); } }), 'select');
  const paperHint = h('p', { class: 'hint', id: 'lb-paper-hint' }, 'The sizes this printer offers, when a printer with CUPS is selected.');
  const customRow = h('div', { class: 'two', hidden: true }, length('customW', 'Paper width', { min: 40 }), length('customH', 'Paper height', { min: 40 }));
  const radiusField = plain('radius', 'Corner rounding (mm)', { min: 0, max: 50, step: 0.5, float: true });
  const shapeRow = h('div', { class: 'two' },
    select('shape', 'Shape', [['rect', 'Square corners'], ['rounded', 'Rounded corners'], ['ellipse', 'Circle or oval']], { onchange: () => syncVisibility(), hint: 'Choosing a common size or a pack sets its usual shape.' }), radiusField);
  const sheetBox = h('section', { class: 'card lb-card' }, head('file', '1 · Paper and label size'),
    h('div', { class: 'field' }, h('label', { for: 'lb-paper' }, 'Paper'), paperSelect, paperHint), customRow,
    kindField, packField, packNote, packCustom, sizeFields,
    h('div', { class: 'three' }, length('width', 'Label width'), length('height', 'Label height'), unitSelect), shapeRow);
  // layout on the paper
  const areaNote = h('p', { class: 'notice info', hidden: true });
  const gridRow = h('div', { class: 'two', hidden: true }, plain('cols', 'Columns'), plain('rows', 'Rows'));
  const countRow = h('div', { class: 'two', hidden: true }, plain('count', 'Number of labels', { max: 600 }));
  const sizeLayout = h('div', { class: 'lb-pane' },
    h('div', { class: 'two' }, select('orientation', 'Sheet direction', [['auto', 'Automatic (fits the most)'], ['portrait', 'Portrait'], ['landscape', 'Landscape']]),
      select('align', 'Placed', [['center', 'Centred on the paper'], ['start', 'Top left']])),
    h('div', { class: 'two' }, length('margin', 'Margin around the labels'), length('gap', 'Space between labels')),
    select('mode', 'Labels per sheet', [['fill', 'As many as fit'], ['grid', 'A grid (columns × rows)'], ['count', 'A number of labels']], { onchange: () => syncVisibility() }),
    gridRow, countRow);
  const packLayoutNote = h('p', { class: 'hint' }, 'The places are fixed by the label pack. Choose a half to use only the labels in the top or the bottom of the sheet.');
  const paperBox = h('section', { class: 'card lb-card' }, head('hash', '2 · Layout on the sheet'),
    select('area', 'Print on', [['full', 'The whole page'], ['top', 'Top half only'], ['bottom', 'Bottom half only']],
      { onchange: (v) => { if (v !== 'full') S.orientation = 'auto'; S.start = 1; syncVisibility(); }, hint: 'A half leaves the rest of the sheet blank, so you can use it later (the top half of A4 is an A5 area).' }),
    areaNote, packLayoutNote, sizeLayout);

  // ------------------------------------------------------------------ 4. how the labels look
  const logoInput = h('input', { type: 'file', accept: 'image/*', hidden: true, onchange: () => { const f = logoInput.files[0]; logoInput.value = ''; if (f) loadLogo(f); } });
  const logoName = h('span', { class: 'hint', id: 'lb-logoname' });
  const logoRemove = h('button', { class: 'btn small', type: 'button', title: 'Take the picture off the labels', hidden: true, onclick: () => { logo = null; syncLogo(); changed(); } }, icon('x', 14), 'Remove');
  const logoOptions = h('div', { class: 'two', hidden: true },
    select('logoPosition', 'Picture goes', [['top', 'Above the text'], ['left', 'Left of the text'], ['background', 'Behind the text']]),
    plain('logoPct', 'Picture size (%)', { min: 10, max: 70 }));
  const lookBox = h('section', { class: 'card lb-card' }, head('eye', '3 · Label style'),
    h('div', { class: 'two' }, colour('fill', 'Background colour', { optional: true, none: 'No colour' }), colour('border', 'Border colour', { optional: true, none: 'No border' })),
    h('div', { class: 'two' }, plain('borderWidth', 'Border width (mm)', { min: 0, max: 5, step: 0.1, float: true }), colour('color', 'Text colour')),
    plain('inset', 'Space around each label (mm)', { min: 0, max: 20, step: 0.5, float: true, hint: 'Draws the label smaller than its place, leaving a margin for the cut lines to sit in.' }),
    h('div', { class: 'two' }, select('family', 'Font', [['sans', 'Sans-serif (Helvetica)'], ['serif', 'Serif (Times)'], ['mono', 'Typewriter (Courier)']])),
    h('div', { class: 'checks' }, check('bold', 'Bold text'), check('italic', 'Italic text')),
    h('div', { class: 'field' }, h('label', {}, 'Picture or logo'),
      h('div', { class: 'actions left' }, h('button', { class: 'btn small', type: 'button', title: 'Put a picture (a logo, say) on every label', onclick: () => logoInput.click() }, icon('image', 15), 'Choose a picture…'), logoInput, logoRemove, logoName),
      h('p', { class: 'hint' }, 'A JPEG or PNG. Transparent parts become white.')),
    logoOptions);

  // ------------------------------------------------------------------ 5. the text
  const sizeInput = bind('size', h('input', { class: 'input', type: 'number', min: 5, max: 72, step: 0.5, id: 'lb-size', 'aria-label': 'Text size in points',
    oninput: () => { const v = parseFloat(sizeInput.value); if (Number.isFinite(v)) { S.size = v; changed(); } } }), 'plain');
  const autoCheck = bind('auto', h('input', { type: 'checkbox', id: 'lb-auto', onchange: () => { S.auto = autoCheck.checked; syncVisibility(); changed(); } }), 'check');
  const textBox = h('section', { class: 'card lb-card' }, head('hash', '5 · The text'),
    h('div', { class: 'two' },
      h('div', { class: 'field' }, h('label', { for: 'lb-size' }, 'Text size (points)'), sizeInput,
        h('label', { class: 'check-row slim', for: 'lb-auto' }, autoCheck, h('span', {}, 'Make the text as large as fits every label'))),
      plain('pad', 'Space inside labels (mm)', { min: 0, max: 20, step: 0.5, float: true })),
    h('div', { class: 'two' },
      select('textAlign', 'Across the label', [['left', 'Left'], ['center', 'Centre'], ['right', 'Right']]), select('valign', 'Up and down', [['middle', 'Middle'], ['top', 'Top']])),
    h('div', { class: 'two' }, plain('lineSpacing', 'Line spacing', { min: 1, max: 2, step: 0.05, float: true }), plain('firstScale', 'First line size (× normal)', { min: 1, max: 3, step: 0.1, float: true })),
    h('div', { class: 'two' }, check('firstBold', 'First line in bold', 'Handy for names.'), colour('firstColor', 'First line colour', { optional: true, none: 'Same as the text' })));

  // ------------------------------------------------------------------ 6. how many, and finishing
  const copyFields = h('div', { class: 'lb-pane' }, plain('copies', 'Copies of each label', { max: 100 }),
    check('repeat', 'Repeat the list to fill the sheet', 'Good for one return-address label ×30.'));
  const countBox = h('section', { class: 'card lb-card' }, head('hash', '6 · How many'), copyFields,
    plain('start', 'Start at label number', { max: 600, hint: 'For a part-used sheet. You can also click a label in the preview.' }));
  const finishBox = h('section', { class: 'card lb-card' }, head('check', '7 · Cut lines'),
    select('cut', 'Cut lines', [['none', 'None'], ['grid', 'Straight dashed lines between the labels'], ['shape', 'A dashed line around each label, in its shape']],
      { hint: 'Straight lines suit a guillotine or a ruler: they run along the edge of every place, so leave some “space around each label” in step 3 and the cut falls in that margin. They are drawn only where a label is printed.' }),
    check('marks', 'Cut marks', 'Small ticks in the margins beside the labels, to guide a ruler or cutter.'),
    h('p', { class: 'hint' }, 'Leave these off when printing on a pack that is already cut, or test on plain paper first and hold it against a real sheet.'));

  // ------------------------------------------------------------------ 7. printing choices (only with CUPS)
  const printBox = h('section', { class: 'card lb-card' });
  function syncPrinter() {
    const p = kit.printer.current;
    if (!p) { printBox.replaceChildren(head('printer', '8 · Printing'), h('p', { class: 'hint' }, 'Add a printer to print from here. You can still download the PDF.')); return; }
    if (!printer || printer.configured === false) {
      printBox.replaceChildren(head('printer', '8 · Printing'),
        h('p', { class: 'hint' }, `${p.name} has no CUPS print queue, so it can’t print from here. Download the PDF and print it from your computer, or set up CUPS and choose the queue under Printers → Edit.`));
      return;
    }
    if (printer.error) { printBox.replaceChildren(head('printer', '8 · Printing'), ui.errorBox(printer.error, loadConfig)); return; }
    const types = [['', 'Printer’s own choice'], ...printer.media_types.map((t) => [t.value, t.label])];
    const qualities = [['', 'Printer’s own choice'], ...printer.qualities.map((q) => [q.value, q.label])];
    const match = matchingMedia();
    const typeField = select('mediaType', 'Paper type', types, { hint: 'Label sheets usually print best as “Labels”.' });
    const qualityField = select('quality', 'Print quality', qualities);
    printBox.replaceChildren(head('printer', `8 · Print on ${p.name}`),
      match.length ? h('p', { class: 'hint' }, `Printed on ${match[0].label}, the paper this sheet is on.`)
        : h('p', { class: 'msg' }, `${p.name} doesn’t list this paper size, so printing is switched off. Download the PDF instead.`),
      h('div', { class: 'grid2' }, typeField, qualityField, plain('sheetCopies', 'Copies of the sheet', { max: 20 })),
      printer.actual_size ? h('p', { class: 'hint' }, 'Printed at actual size (100%), so the text lands on the labels. Turn off “fit to page” in any other print dialog.')
        : h('p', { class: 'msg' }, 'This print queue can’t print at actual size, so printing is switched off. Download the PDF instead.'));
    paintControls();
  }
  const canPrint = () => Boolean(printer && printer.configured !== false && !printer.error && printer.actual_size && matchingMedia().length);

  // ------------------------------------------------------------------ the form, and showing what applies
  function syncPaper() {
    const list = papers();
    paperSelect.replaceChildren(...list.map((p) => h('option', { value: p.id }, p.label)), h('option', { value: 'custom' }, 'Custom size…'));
    if (S.paper !== 'custom' && !list.some((p) => p.id === S.paper)) S.paper = list.find((p) => p.keyword === printer?.default_media)?.id || list[0]?.id || 'a4';
    paperSelect.value = S.paper;
    syncVisibility();
  }
  function fillLists() {
    fillOptions(packField.querySelector('select'), [...templates().map((t) => [t.id, t.name]), ['', 'My own pack…']]);
    sizePick.replaceChildren(h('option', { value: '' }, 'Choose a common size…'), ...(config?.sizes || []).map((p, i) => h('option', { value: i }, p.label)));
  }
  function syncSizePick() {
    const at = (config?.sizes || []).findIndex((p) => Math.abs(p.width - S.width) < 0.05 && Math.abs(p.height - S.height) < 0.05);
    sizePick.value = at >= 0 ? String(at) : '';
  }
  function syncVisibility() {
    const isPack = S.kind === 'pack', t = pack();
    packField.hidden = !isPack; packCustom.hidden = !(isPack && !t); sizeFields.hidden = isPack; sizeLayout.hidden = isPack;
    packNote.hidden = !isPack; packLayoutNote.hidden = !isPack;
    packNote.textContent = isPack ? `${S.packCols * S.packRows} labels on each sheet, ${shown(S.width)} × ${shown(S.height)} ${S.unit} each${t ? `, on ${t.paper === 'a4' ? 'A4' : 'US Letter'}` : ''}.` : '';
    paperSelect.disabled = Boolean(t);
    paperHint.textContent = t ? 'A label pack comes on one paper size. Choose “My own pack…” to use another.' : printer?.media?.length ? `The paper sizes ${printer.name} offers.` : 'Common paper sizes. Select a printer with CUPS to see the sizes it offers.';
    customRow.hidden = S.paper !== 'custom' || Boolean(t);
    paperSelect.disabled = Boolean(t);
    textPane.hidden = S.inputMode !== 'text'; csvPane.hidden = S.inputMode !== 'csv'; blankCountField.hidden = S.blanks !== 'count';
    gridRow.hidden = S.mode !== 'grid'; countRow.hidden = S.mode !== 'count';
    const part = S.area !== 'full';
    if (ctl.orientation) ctl.orientation.el.disabled = part;
    areaNote.hidden = !part;
    if (part) {
      const other = { top: 'bottom', bottom: 'top' }[S.area];
      areaNote.replaceChildren(icon('info', 18), h('div', {}, `Only the ${S.area} half is printed; the rest stays blank. To use the ${other} half later, choose “${other[0].toUpperCase()}${other.slice(1)} half only” and put the sheet back in the tray the same way it went in (let the ink dry first). The sheet is not turned, so “Sheet direction” is fixed.`));
    } else areaNote.replaceChildren();
    radiusField.hidden = S.shape !== 'rounded';
    sizeInput.disabled = Boolean(S.auto);
    logoOptions.hidden = !logo;
    csvName.textContent = S.csvName;
    syncSizePick();
  }
  function applyKind() { paintControls(); syncPaper(); changed(); }
  function pickPack(id) {
    const t = templates().find((x) => x.id === id);
    S.pack = t ? t.id : '';
    if (t) {
      Object.assign(S, { packCols: t.cols, packRows: t.rows, width: t.width, height: t.height, left: t.left, top: t.top, gapX: t.gap_x, gapY: t.gap_y,
        shape: t.radius > 0 ? 'rounded' : 'rect', radius: t.radius || 2 });
      const p = papers().find((m) => Math.abs(Math.min(m.width, m.height) - t.paper_width) < 0.6 && Math.abs(Math.max(m.width, m.height) - t.paper_height) < 0.6);
      if (p) S.paper = p.id;
    }
    S.start = 1; S.page = 1;
    paintControls(); syncVisibility(); changed();
  }

  // ------------------------------------------------------------------ the logo
  async function loadLogo(file) {
    if (!file.type.startsWith('image/')) { toast(`${file.name} is not a picture.`, 'bad'); return; }
    let bitmap;
    try { bitmap = await createImageBitmap(file); } catch { toast(`This browser can’t read ${file.name}. Try a JPEG or PNG.`, 'bad'); return; }
    const k = Math.min(1, MAX_LOGO_SIDE / Math.max(bitmap.width, bitmap.height));
    const w = Math.max(1, Math.round(bitmap.width * k)), hh = Math.max(1, Math.round(bitmap.height * k));
    const c = document.createElement('canvas'); c.width = w; c.height = hh;
    const g = c.getContext('2d'); g.fillStyle = '#fff'; g.fillRect(0, 0, w, hh); g.drawImage(bitmap, 0, 0, w, hh);
    const blob = await new Promise((resolve) => c.toBlob(resolve, 'image/jpeg', 0.92));
    if (!blob) { toast('Could not use that picture.', 'bad'); return; }
    logo = { bitmap: c, blob, width: w, height: hh, name: file.name };
    syncLogo(); changed();
  }
  function syncLogo() {
    logoName.textContent = logo ? `${logo.name} (${logo.width} × ${logo.height} px)` : '';
    logoRemove.hidden = !logo;
    logoOptions.hidden = !logo;
  }

  // ------------------------------------------------------------------ the preview
  const canvas = h('canvas', { class: 'sheet-canvas', role: 'img', 'aria-label': 'Preview of the label sheet', title: 'Click a label to start printing from it' });
  const summary = h('p', { class: 'summary', 'aria-live': 'polite' });
  const warnings = h('div', { class: 'warns' });
  const pager = h('div', { class: 'pager', hidden: true });
  const downloadBtns = [], printBtns = [];
  const actionRow = (extra = '') => {
    const d = h('button', { class: 'btn', type: 'button', title: 'Download the labels as a PDF to print yourself', disabled: true, onclick: () => download() }, icon('download', 16), 'Download PDF');
    const p = h('button', { class: 'btn primary', type: 'button', title: 'Show the sheet, then print it on the selected printer', disabled: true, onclick: () => openPrint() }, icon('printer', 16), 'Preview and print…');
    downloadBtns.push(d); printBtns.push(p);
    return h('div', { class: `actions left ${extra}` }, p, d);
  };
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
  function shapePath(ctx, shape, x, y, w, hh, r) {
    ctx.beginPath();
    if (shape === 'ellipse') ctx.ellipse(x + w / 2, y + hh / 2, w / 2, hh / 2, 0, 0, Math.PI * 2);
    else if (shape === 'rounded' && ctx.roundRect) ctx.roundRect(x, y, w, hh, Math.min(r, w / 2, hh / 2));
    else ctx.rect(x, y, w, hh);
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
    if (L.area && L.area !== 'full' && L.region) {  // the part left blank (only in this preview; the PDF has nothing there)
      const R = L.region;
      const rect = { top: [0, R.h, L.page_w, L.page_h - R.h], bottom: [0, 0, L.page_w, R.y] }[L.area];
      ctx.fillStyle = 'rgba(100, 116, 139, 0.10)'; ctx.fillRect(...rect);
      ctx.save(); ctx.setLineDash([2, 1.5]); ctx.strokeStyle = '#94a3b8'; ctx.lineWidth = 0.4; ctx.strokeRect(R.x, R.y, R.w, R.h); ctx.restore();
      ctx.fillStyle = '#64748b'; ctx.font = '9px sans-serif'; ctx.textAlign = 'center';
      ctx.fillText('stays blank', rect[0] + rect[2] / 2, rect[1] + rect[3] / 2); ctx.textAlign = 'start';
    }
    const look = L.look;
    ctx.lineWidth = 0.25;
    for (const c of L.cells) {  // empty places: a faint outline
      if (c.used) continue;
      shapePath(ctx, look.shape, c.art.x, c.art.y, c.art.w, c.art.h, look.radius);
      if (S.start > 1 && L.page === 1 && !c.lines.length) { ctx.fillStyle = '#eceff3'; ctx.fill(); }
      ctx.strokeStyle = '#dde1e7'; ctx.setLineDash([1.2, 1]); ctx.stroke(); ctx.setLineDash([]);
    }
    for (const c of L.cells) {
      if (!c.used) continue;
      const A = c.art;
      shapePath(ctx, look.shape, A.x, A.y, A.w, A.h, look.radius);
      if (look.fill) { ctx.fillStyle = look.fill; ctx.fill(); }
      if (c.logo && logo) {
        ctx.save(); shapePath(ctx, look.shape, A.x, A.y, A.w, A.h, look.radius); ctx.clip();
        ctx.drawImage(logo.bitmap, c.logo.x, c.logo.y, c.logo.w, c.logo.h); ctx.restore();
      }
      shapePath(ctx, look.shape, A.x, A.y, A.w, A.h, look.radius);
      if (look.border && look.border_width > 0) { ctx.strokeStyle = look.border; ctx.lineWidth = look.border_width; ctx.stroke(); ctx.lineWidth = 0.25; }
      ctx.textBaseline = 'alphabetic';
      for (const ln of c.lines) {
        ctx.font = `${look.italic ? 'italic ' : ''}${ln.bold ? 'bold ' : ''}${ln.size * 25.4 / 72}px ${FONT_CSS[look.family]}`;
        ctx.fillStyle = ln.color; ctx.fillText(ln.t, ln.x, ln.y);
      }
    }
    drawCutLines(ctx, L);
    if (S.marks) drawMarks(ctx, L);
  }
  function drawCutLines(ctx, L) {  // dashed cut lines for the printed labels (the PDF draws the same lines)
    const used = L.cells.filter((c) => c.used);
    if (!used.length || S.cut === 'none') return;
    ctx.save(); ctx.strokeStyle = '#444'; ctx.lineWidth = 0.35; ctx.setLineDash([1.6, 1.2]);
    if (S.cut === 'shape') {
      for (const c of used) { shapePath(ctx, L.look.shape, c.art.x, c.art.y, c.art.w, c.art.h, L.look.radius); ctx.stroke(); }
    } else {
      const spans = new Map();
      const r2 = (v) => Math.round(v * 100) / 100;
      for (const c of used) {
        const x1 = r2(c.x), y1 = r2(c.y), x2 = r2(c.x + c.w), y2 = r2(c.y + c.h);
        for (const [key, lo, hi] of [[`v${x1}`, y1, y2], [`v${x2}`, y1, y2], [`h${y1}`, x1, x2], [`h${y2}`, x1, x2]]) { if (!spans.has(key)) spans.set(key, []); spans.get(key).push([lo, hi]); }
      }
      ctx.beginPath();
      for (const [key, ranges] of spans) {
        const at = Number(key.slice(1));
        const merged = [];
        for (const [lo, hi] of ranges.sort((a, b) => a[0] - b[0])) {
          if (merged.length && lo <= merged[merged.length - 1][1] + 0.01) merged[merged.length - 1][1] = Math.max(merged[merged.length - 1][1], hi); else merged.push([lo, hi]);
        }
        for (const [lo, hi] of merged) { if (key[0] === 'v') { ctx.moveTo(at, lo); ctx.lineTo(at, hi); } else { ctx.moveTo(lo, at); ctx.lineTo(hi, at); } }
      }
      ctx.stroke();
    }
    ctx.restore();
  }
  function drawMarks(ctx, L) {  // cut marks in the margins beside the printed labels (the PDF draws the same ticks)
    const used = L.cells.filter((c) => c.used);
    if (!used.length) return;
    const tick = 3, off = 1;
    const xs = [...new Set(used.flatMap((c) => [c.x, c.x + c.w]))], ys = [...new Set(used.flatMap((c) => [c.y, c.y + c.h]))];
    const top = Math.min(...used.map((c) => c.y)), bottom = Math.max(...used.map((c) => c.y + c.h));
    const left = Math.min(...used.map((c) => c.x)), right = Math.max(...used.map((c) => c.x + c.w));
    ctx.strokeStyle = '#333'; ctx.lineWidth = 0.2; ctx.beginPath();
    for (const x of xs) {
      if (top - off - tick >= 0) { ctx.moveTo(x, top - off - tick); ctx.lineTo(x, top - off); }
      if (bottom + off + tick <= L.page_h) { ctx.moveTo(x, bottom + off); ctx.lineTo(x, bottom + off + tick); }
    }
    for (const y of ys) {
      if (left - off - tick >= 0) { ctx.moveTo(left - off - tick, y); ctx.lineTo(left - off, y); }
      if (right + off + tick <= L.page_w) { ctx.moveTo(right + off, y); ctx.lineTo(right + off + tick, y); }
    }
    ctx.stroke();
  }
  canvas.addEventListener('click', (ev) => {
    if (!layout) return;
    const box = canvas.getBoundingClientRect();
    const x = ((ev.clientX - box.left) / box.width) * layout.page_w, y = ((ev.clientY - box.top) / box.height) * layout.page_h;
    const i = layout.cells.findIndex((c) => x >= c.x && x <= c.x + c.w && y >= c.y && y <= c.y + c.h);
    if (i < 0) return;
    if (layout.page > 1) { toast('Choose the start on the first sheet.'); return; }
    S.start = i + 1; paintControls(); changed();
  });

  // ------------------------------------------------------------------ settings -> server
  function request() {
    const [pw, ph] = paperSize();
    const sheet = S.kind === 'pack'
      ? { kind: 'pack', paper: { width: pw, height: ph }, cols: S.packCols, rows: S.packRows, width: S.width, height: S.height, left: S.left, top: S.top, gap_x: S.gapX, gap_y: S.gapY, area: S.area }
      : { kind: 'size', paper: { width: pw, height: ph }, width: S.width, height: S.height, margin: S.margin, gap: S.gap, mode: S.mode, cols: S.cols, rows: S.rows,
          count: S.count, orientation: S.orientation, align: S.align, area: S.area };
    return {
      sheet,
      content: { mode: S.inputMode, text: S.text, per_line: S.perLine, csv: S.csv, template: S.csvTemplate, blanks: { mode: S.blanks, count: S.blankCount } },
      style: { size: S.auto ? null : S.size, align: S.textAlign, valign: S.valign, family: S.family, bold: S.bold, italic: S.italic, first_bold: S.firstBold,
        first_scale: S.firstScale, first_color: S.firstColor || null, color: S.color, line_spacing: S.lineSpacing, pad: S.pad, inset: S.inset, shape: S.shape, radius: S.radius,
        fill: S.fill || null, border: S.border || null, border_width: S.borderWidth,
        ...(logo ? { logo: { width: logo.width, height: logo.height, position: S.logoPosition, pct: S.logoPct } } : {}) },
      copies: S.copies, fill: S.repeat, start: S.start, cut: S.cut, marks: S.marks, page: S.page,
      min_margin: minMargin(), borderless: matchingMedia().some((m) => m.borderless),
    };
  }
  const withLogo = (extra = {}) => {  // a form with the settings and, when the labels have one, the picture
    const f = new FormData();
    f.append('settings', JSON.stringify({ ...request(), ...extra }));
    if (logo) f.append('logo', logo.blob, 'logo.jpg');
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
    if (S.page > layout.sheets) S.page = layout.sheets;
    columns = layout.columns || [];
    paintColumns();
    csvNote.textContent = S.inputMode === 'csv' && columns.length ? `${layout.labels} labels from the spreadsheet. Columns: ${columns.join(', ')}.` : '';
    const printed = layout.labels - layout.blanks;
    const what = layout.blanks ? `${printed ? `${printed} with text and ` : ''}${layout.blanks} blank` : '';
    summary.textContent = layout.labels
      ? `${layout.labels} label${layout.labels === 1 ? '' : 's'}${what ? ` (${what})` : ''} on ${layout.sheets} sheet${layout.sheets === 1 ? '' : 's'} (${layout.per_sheet} places each)${printed ? `, text ${+layout.size.toFixed(1)} pt` : ''}.`
      : 'Type something to see it on the labels.';
    warnings.append(...layout.warnings.map((w) => h('div', { class: 'notice warn' }, icon('alert', 18), h('div', {}, w))));
    paintPager(); syncButtons(); drawPreview();
  }
  function paintColumns() {
    colChips.replaceChildren(...columns.map((c) => h('button', { class: 'chip col', type: 'button', title: `Add {${c}} to the label`, onclick: () => {
      const at = tplArea.selectionStart ?? tplArea.value.length;
      tplArea.value = `${tplArea.value.slice(0, at)}{${c}}${tplArea.value.slice(at)}`; S.csvTemplate = tplArea.value; tplArea.focus(); changed();
    } }, `{${c}}`)));
  }

  // ------------------------------------------------------------------ presets: saved choices (not the text or the logo)
  function describe(p) {
    const x = p.settings;
    const u = (v) => `${+(v / UNITS[x.unit || 'mm'].f).toFixed(UNITS[x.unit || 'mm'].dp)}`;
    const what = x.kind === 'pack' ? (templates().find((t) => t.id === x.pack)?.name || 'your own pack')
      : `${u(x.size.width)} × ${u(x.size.height)} ${x.unit || 'mm'} ${{ ellipse: 'circles or ovals', rounded: 'rounded labels', rect: 'labels' }[x.look?.shape || 'rect']}`;
    return `${what} · ${x.paper.label || `${x.paper.width} × ${x.paper.height} mm`}`;
  }
  function paintPresets() {
    presetChips.replaceChildren(...[...presets.map((p) => h('span', { class: `chip-wrap${p.builtin ? '' : ' mine'}` },
        h('button', { class: 'chip', type: 'button', title: describe(p), onclick: () => applyPreset(p) }, p.name),
        p.builtin ? null : h('button', { class: 'chip-x', type: 'button', 'aria-label': `Delete the preset ${p.name}`, title: 'Delete this preset (asks first)', onclick: () => confirmDelete(p) }, icon('x', 12)))),
      presets.length ? null : h('span', { class: 'hint' }, 'No presets yet. Set things up, then save them here.')].filter(Boolean));  // (a native replaceChildren would print "null")
  }
  async function loadPresets() {
    const res = await api('/labels/presets');
    presets = res.ok ? res.data.presets : [];
    paintPresets();
  }
  function presetSettings() {
    const paper = currentPaper();
    const [pw, ph] = paperSize();
    const out = { unit: S.unit, kind: S.kind, paper: { width: pw, height: ph, ...(paper.keyword ? { media: paper.keyword } : {}), label: paper.label },
      look: { shape: S.shape, radius: S.radius, fill: S.fill || null, border: S.border || null, border_width: S.borderWidth, color: S.color, family: S.family, bold: S.bold, italic: S.italic, line_spacing: S.lineSpacing, inset: S.inset },
      text: { size: S.auto ? null : S.size, align: S.textAlign, valign: S.valign, pad: S.pad, first_bold: S.firstBold, first_scale: S.firstScale, first_color: S.firstColor || null },
      area: S.area, copies: S.copies, repeat: S.repeat, cut: S.cut, marks: S.marks, sheet_copies: Number(S.sheetCopies) || 1 };
    if (S.kind === 'pack') {
      out.pack = S.pack;
      if (!S.pack) { out.grid = { cols: S.packCols, rows: S.packRows, left: S.left, top: S.top, gap_x: S.gapX, gap_y: S.gapY }; out.size = { width: S.width, height: S.height }; }
    } else {
      Object.assign(out, { size: { width: S.width, height: S.height }, orientation: S.orientation, margin: S.margin, gap: S.gap, mode: S.mode, align: S.align });
      if (S.mode === 'grid') { out.cols = S.cols; out.rows = S.rows; }
      if (S.mode === 'count') out.count = S.count;
    }
    if (S.mediaType) out.media_type = S.mediaType;
    if (S.quality) out.quality = Number(S.quality);
    if (S.resolution) out.resolution = S.resolution.split('x').map(Number);
    return out;
  }
  const controlState = () => new Map([...root.querySelectorAll('input[id], select[id]')].map((el) => [el.id, el.type === 'checkbox' ? el.checked : el.value]));
  function applyPreset(p) {
    const x = p.settings;
    const before = controlState();
    const look = x.look || {}, text = x.text || {};
    Object.assign(S, { unit: x.unit || 'mm', kind: x.kind || 'pack', start: 1, page: 1, copies: x.copies || 1, repeat: Boolean(x.repeat), cut: x.cut || 'none', marks: Boolean(x.marks), area: ['top', 'bottom'].includes(x.area) ? x.area : 'full',
      shape: look.shape || 'rect', radius: look.radius ?? 2, fill: look.fill || '', border: look.border || '', borderWidth: look.border_width ?? 0.3, color: look.color || '#000000',
      family: look.family || 'sans', bold: Boolean(look.bold), italic: Boolean(look.italic), lineSpacing: look.line_spacing ?? 1.2, inset: look.inset ?? 0,
      auto: text.size === null, size: text.size ?? S.size, textAlign: text.align || 'left', valign: text.valign || 'middle', pad: text.pad ?? 2,
      firstBold: Boolean(text.first_bold), firstScale: text.first_scale ?? 1, firstColor: text.first_color || '', sheetCopies: x.sheet_copies || 1 });
    if (S.kind === 'pack') {
      S.pack = templates().some((t) => t.id === x.pack) ? x.pack : '';
      const t = pack();
      if (t) Object.assign(S, { packCols: t.cols, packRows: t.rows, width: t.width, height: t.height, left: t.left, top: t.top, gapX: t.gap_x, gapY: t.gap_y });
      else if (x.grid) Object.assign(S, { packCols: x.grid.cols, packRows: x.grid.rows, left: x.grid.left, top: x.grid.top, gapX: x.grid.gap_x, gapY: x.grid.gap_y, width: x.size.width, height: x.size.height });
    } else {
      Object.assign(S, { width: x.size.width, height: x.size.height, orientation: x.orientation || 'auto',
        margin: x.margin ?? 5, gap: x.gap ?? 2, mode: x.mode || 'fill', align: x.align || 'center' });
      if (x.cols) S.cols = x.cols;
      if (x.rows) S.rows = x.rows;
      if (x.count) S.count = x.count;
    }
    const list = papers();
    const found = list.find((m) => x.paper.media && m.keyword === x.paper.media) || list.find((m) => Math.abs(m.width - x.paper.width) < 0.6 && Math.abs(m.height - x.paper.height) < 0.6);
    if (found) S.paper = found.id; else { S.paper = 'custom'; S.customW = x.paper.width; S.customH = x.paper.height; }
    S.mediaType = printer?.media_types?.some((t) => t.value === x.media_type) ? x.media_type : '';
    S.quality = printer?.qualities?.some((q) => q.value === x.quality) ? String(x.quality) : '';
    S.resolution = '';
    paintControls(); syncPaper(); syncPrinter(); changed();
    const after = controlState();  // what the preset changed lights up for a moment
    for (const [id, value] of after) {
      if (before.get(id) !== value) { const f = document.getElementById(id)?.closest('.field, .check-row'); if (f) { f.classList.remove('flash'); void f.offsetWidth; f.classList.add('flash'); setTimeout(() => f.classList.remove('flash'), 1600); } }
    }
    toast(`Filled in the settings from “${p.name}”.`);
  }
  const saveName = h('input', { class: 'input', maxlength: 60, placeholder: 'For example: Address labels on L7160', id: 'lb-preset-name', 'aria-label': 'Preset name', oninput: () => saveNote() });
  const saveMsg = h('p', { class: 'msg', role: 'alert' });
  const saveReplace = h('p', { class: 'hint' });
  const saveGo = h('button', { class: 'btn primary', type: 'button', title: 'Save the preset', onclick: () => saveAs(saveName.value) }, 'Save preset');
  const saveDialog = h('dialog', { 'aria-labelledby': 'lb-save-title' }, h('h3', { id: 'lb-save-title' }, 'Save as a preset'),
    h('p', {}, 'Keeps the sheet or label size, the style, the text settings and the print choices. The label text and the picture are not saved.'), saveName, saveReplace, saveMsg,
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Close without saving', onclick: () => saveDialog.close() }, 'Cancel'), saveGo));
  function saveNote() {
    saveMsg.textContent = '';
    const clash = presets.find((p) => !p.builtin && p.name.toLowerCase() === saveName.value.trim().toLowerCase());
    saveReplace.textContent = clash ? `A preset called “${clash.name}” already exists. Saving replaces it.` : '';
  }
  function openSave() {
    const t = pack();
    saveName.value = (t ? t.name.replace(/ labels?,? /i, ' ') : `${shown(S.width)} × ${shown(S.height)} ${S.unit} labels on ${currentPaper().label}`).slice(0, 60);
    saveNote(); saveDialog.showModal(); saveName.select();
  }
  async function saveAs(name) {
    const res = await api('/labels/presets', { method: 'POST', body: { name, settings: presetSettings() } });
    if (!res.ok) { saveMsg.textContent = res.error?.message || 'Could not save.'; return; }
    saveDialog.close();
    toast(res.data.replaced ? `Preset “${res.data.name}” updated.` : `Preset “${res.data.name}” saved.`);
    await loadPresets();
  }
  const delDialog = h('dialog', { 'aria-labelledby': 'lb-del-title' }, h('h3', { id: 'lb-del-title' }, 'Delete this preset?'), h('p', { id: 'lb-del-text' }),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Keep the preset', onclick: () => delDialog.close() }, 'Keep it'),
      h('button', { class: 'btn primary danger-fill', type: 'button', id: 'lb-del-go', title: 'Delete the preset for good' }, 'Delete')));
  function confirmDelete(p) {
    document.getElementById('lb-del-text').textContent = `“${p.name}” will be removed for everyone who uses this panda-print. Your other presets are not affected.`;
    document.getElementById('lb-del-go').onclick = async () => {
      delDialog.close();
      const res = await api(`/labels/presets/${p.id}`, { method: 'DELETE' });
      if (!res.ok) { toast(res.error?.message || 'Could not delete.', 'bad'); return; }
      toast('Preset deleted.');
      await loadPresets();
    };
    delDialog.showModal();
  }

  // ------------------------------------------------------------------ download and print
  async function download() {
    for (const b of downloadBtns) b.disabled = true;
    const res = await api('/labels/pdf?download=1', { method: 'POST', body: withLogo() });
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
    h('p', { class: 'hint' }, 'This is the first sheet of the PDF that will be sent. A landscape sheet is turned to match how the paper feeds into the printer; the labels stay upright on it.'),
    h('div', { class: 'actions' }, h('button', { class: 'btn', type: 'button', title: 'Close without printing', onclick: () => dialog.close() }, 'Cancel'), goBtn));
  let dlgToken = 0;
  dialog.addEventListener('close', () => { if (dialog.open) return; dlgToken += 1; sheets.replaceChildren(); });
  root.append(dialog, saveDialog, delDialog);

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
    const res = await api('/labels/pdf', { method: 'POST', body: withLogo() });
    if (mine !== dlgToken) return;
    if (!res.ok) { sheets.replaceChildren(h('p', { class: 'empty' }, res.error?.message || 'Could not draw the sheet.')); return; }
    try { await drawPdfBytes(kit, new Uint8Array(await res.data.arrayBuffer()), sheets, { alive: () => mine === dlgToken, width: Math.min(300, dialog.clientWidth - 80), maxPages: 1 }); }
    catch { sheets.replaceChildren(h('p', { class: 'empty' }, 'The preview could not be drawn, but the sheet is ready to print.')); }
  }
  async function doPrint() {
    const p = kit.printer.current;
    goBtn.disabled = true;
    const res = await api('/labels/print', { method: 'POST', body: withLogo({ printer: p.id, options: printOptions() }) });
    goBtn.disabled = false;
    if (res.ok) { dialog.close(); toast(`Sent to ${p.name} (job #${res.data.job_id}).`); return; }
    toast(res.error?.message || 'Could not print.', 'bad');
  }

  // ------------------------------------------------------------------ assemble the page
  const form = h('div', { class: 'ps-form' }, presetBox, sheetBox, paperBox, lookBox, contentBox, textBox, countBox, finishBox, printBox,
    h('section', { class: 'card lb-card narrow-only' }, head('printer', 'Make the sheet'), actionRow()));
  const side = h('aside', { class: 'ps-side' }, h('div', { class: 'sticky-col' },
    h('div', { class: 'card lb-card preview-card' }, h('div', { class: 'sheet-wrap' }, canvas), pager, summary, warnings),
    actionRow('wide-only center')));
  root.append(h('div', { class: 'ps-layout' }, form, side));

  textArea.value = S.text; csvArea.value = S.csv; tplArea.value = S.csvTemplate;

  async function loadConfig() {
    const id = kit.printer.current?.id;
    const res = await api(`/labels/config${id ? `?printer=${encodeURIComponent(id)}` : ''}`);
    if (!res.ok) { toast(res.error?.message || 'Could not load the label sheets.', 'bad'); return; }
    config = res.data;
    printer = config.printer;
    if (S.kind === 'pack' && S.pack && !templates().some((t) => t.id === S.pack)) S.pack = '';
    fillLists(); syncPaper(); syncPrinter(); paintControls(); syncVisibility();
    changed();
  }

  // The quick-links strip isn't useful here and would cut the pinned preview short at the end of the page.
  const view = root.parentElement;
  const hideLauncher = () => document.body.toggleAttribute('data-hide-launcher', !view.hidden);
  new MutationObserver(hideLauncher).observe(view, { attributes: true, attributeFilter: ['hidden'] });
  hideLauncher();

  paintControls(); syncVisibility(); paintColumns(); loadPresets(); drawPreview();
  kit.printer.subscribe(() => loadConfig());
  window.addEventListener('resize', () => { if (!root.hidden) drawPreview(); });
  await loadConfig();
}
