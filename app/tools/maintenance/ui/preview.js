// What a page will look like on paper: real thumbnails, and the Preview dialog.
//  * the standard test page is drawn as an exact replica (the same four bars the PostScript prints);
//  * pictures are drawn on a paper-shaped canvas;
//  * PDFs are drawn with pdf.js, bundled in static/vendor/pdfjs (no CDN). It is loaded only when a PDF needs drawing.

const PDFJS = '/static/vendor/pdfjs/';
const PAPER = { w: 160, h: 226 };  // thumbnail canvas: A4 shaped
let pdfjs = null;

export function loadPdfjs() {
  pdfjs ||= import(`${PDFJS}pdf.min.mjs`).then((lib) => {
    lib.GlobalWorkerOptions.workerSrc = `${PDFJS}pdf.worker.min.mjs`;
    return lib;
  });
  return pdfjs;
}

const openPdf = async (data) => (await loadPdfjs()).getDocument({ data, isEvalSupported: false, useSystemFonts: true }).promise;

// The standard page as printed: four 40x14 pt bars (cyan, magenta, yellow, black) one above the other, 72 pt from
// the left, starting 720 pt up on an A4 sheet (595 x 842 pt). Same numbers as nozzle-check.ps.
export function standardSvg(kit) {
  const { svg } = kit;
  const bars = ['#00aeef', '#ec008c', '#fff200', '#231f20'].map((fill, i) =>
    svg('rect', { x: 72, y: 842 - (720 - 20 * i) - 14, width: 40, height: 14, fill }));
  return svg('svg', { viewBox: '0 0 595 842', role: 'img', 'aria-label': 'The standard test page: four colour bars', preserveAspectRatio: 'xMidYMid meet' },
    svg('rect', { width: 595, height: 842, fill: '#fff' }), ...bars);
}

const blob = (canvas) => new Promise((res) => canvas.toBlob(res, 'image/png'));

function paperCanvas() {
  const canvas = document.createElement('canvas');
  canvas.width = PAPER.w; canvas.height = PAPER.h;
  const ctx = canvas.getContext('2d');
  ctx.fillStyle = '#fff';
  ctx.fillRect(0, 0, PAPER.w, PAPER.h);
  return { canvas, ctx };
}

// A small PNG picture of a file for the library, or null if it can't be drawn (the page still works without one).
// Never waits forever: a browser tab that isn't being painted (in the background) stalls pdf.js, and an upload
// must not hang on a picture.
export function makeThumb(file, limitMs = 15000) {
  return Promise.race([drawThumb(file), new Promise((res) => setTimeout(() => res(null), limitMs))]);
}

const drawn = new Map();  // page id -> data: URL (or null): pictures drawn here for pages stored without one

// The picture for a page the library has no thumbnail for (e.g. uploaded before thumbnails existed). Not stored.
export function drawnThumb(item) {
  if (!drawn.has(item.id)) {
    drawn.set(item.id, (async () => {
      const res = await fetch(`/api/maintenance/pages/${item.id}/file`, { credentials: 'same-origin' });
      const png = res.ok ? await makeThumb(new File([await res.blob()], item.name, { type: item.mime })) : null;
      return png ? await new Promise((ok) => { const r = new FileReader(); r.onload = () => ok(r.result); r.readAsDataURL(png); }) : null;  // data: (CSP allows it, not blob:)
    })().catch(() => null));
  }
  return drawn.get(item.id);
}

async function drawThumb(file) {
  try {
    const { canvas, ctx } = paperCanvas();
    const margin = 6, boxW = PAPER.w - 2 * margin, boxH = PAPER.h - 2 * margin;
    if (file.type.startsWith('image/')) {
      const bitmap = await createImageBitmap(file);
      const k = Math.min(boxW / bitmap.width, boxH / bitmap.height);
      const w = bitmap.width * k, h = bitmap.height * k;
      ctx.drawImage(bitmap, (PAPER.w - w) / 2, (PAPER.h - h) / 2, w, h);
      bitmap.close?.();
    } else if (file.type === 'application/pdf') {
      const doc = await openPdf(new Uint8Array(await file.arrayBuffer()));
      const page = await doc.getPage(1);
      const base = page.getViewport({ scale: 1 });
      const k = Math.min(boxW / base.width, boxH / base.height);
      const view = page.getViewport({ scale: k });
      const tile = document.createElement('canvas');
      tile.width = Math.ceil(view.width); tile.height = Math.ceil(view.height);
      await page.render({ canvas: tile, canvasContext: tile.getContext('2d'), viewport: view }).promise;
      ctx.drawImage(tile, (PAPER.w - tile.width) / 2, (PAPER.h - tile.height) / 2);
      doc.loadingTask.destroy();
    } else {
      return null;
    }
    return await blob(canvas);
  } catch (e) {
    console.warn('could not make a thumbnail', e);
    return null;
  }
}

// ------------------------------------------------------------------ drawing a page's sheets

const sheetOf = (kit, ...kids) => kit.h('div', { class: 'sheet' }, ...kids);
const noteOf = (kit, text) => kit.h('p', { class: 'empty' }, text);

// Draws what a library page (or the standard page) looks like on paper into `sheets` (a container element).
// `alive()` says whether this drawing is still wanted; `width` is the sheet width in CSS pixels.
export async function drawSheets(kit, item, sheets, { alive = () => true, width = 420, maxPages = 12 } = {}) {
  const { h } = kit;
  sheets.replaceChildren();
  if (item.standard) { sheets.append(sheetOf(kit, standardSvg(kit))); return; }
  const file = `/api/maintenance/pages/${item.id}/file`;
  if (item.mime.startsWith('image/')) { sheets.append(sheetOf(kit, h('img', { src: file, alt: item.name }))); return; }
  sheets.append(noteOf(kit, 'Drawing the pages…'));
  try {
    const res = await fetch(file, { credentials: 'same-origin' });
    const doc = await openPdf(new Uint8Array(await res.arrayBuffer()));
    if (!alive()) return;
    sheets.replaceChildren();
    const count = Math.min(doc.numPages, maxPages);  // plenty to see what it is; keeps a long book quick
    const ratio = window.devicePixelRatio || 1;
    for (let n = 1; n <= count && alive(); n++) {
      const page = await doc.getPage(n);
      const base = page.getViewport({ scale: 1 });
      const cssWidth = Math.max(160, Math.min(560, width));
      const view = page.getViewport({ scale: (cssWidth / base.width) * ratio });
      const canvas = h('canvas', { 'aria-label': `Page ${n} of ${item.pages}` });
      canvas.width = Math.ceil(view.width); canvas.height = Math.ceil(view.height);
      canvas.style.setProperty('width', `${cssWidth}px`);
      sheets.append(sheetOf(kit, canvas));
      await page.render({ canvas, canvasContext: canvas.getContext('2d'), viewport: view }).promise;
    }
    if (doc.numPages > count && alive()) sheets.append(noteOf(kit, `…and ${doc.numPages - count} more pages.`));
    doc.loadingTask.destroy();
  } catch (e) {
    console.warn('preview failed', e);
    if (alive()) sheets.replaceChildren(noteOf(kit, 'This PDF could not be drawn here. You can still download it to look at it.'));
  }
}

// ------------------------------------------------------------------ the Preview dialog

export function createPreview(kit) {
  const { h, icon } = kit;
  const sheets = h('div', { class: 'sheets' });
  const title = h('h3', { id: 'pv-title' });
  const meta = h('p', { class: 'sub' });
  const download = h('a', { class: 'btn', hidden: true, title: 'Download the original file' }, icon('download', 16), 'Download');
  const dialog = h('dialog', { class: 'wide preview', 'aria-labelledby': 'pv-title' }, title, meta, sheets,
    h('p', { class: 'hint' }, 'Shown the way it will be sent. CUPS fits the page to the paper, so margins can differ slightly.'),
    h('div', { class: 'actions' }, download, h('button', { class: 'btn primary', type: 'button', title: 'Close the preview', onclick: () => dialog.close() }, 'Close')));
  let token = 0;  // a newer preview replaces one that is still drawing
  dialog.addEventListener('close', () => { if (dialog.open) return; token += 1; sheets.replaceChildren(); });  // (a close event can arrive after a reopen)

  return {
    dialog,
    open(item) {
      token += 1;
      const mine = token;
      title.textContent = item.name;
      meta.textContent = item.standard ? 'The built-in test page: four colour bars (cyan, magenta, yellow, black).'
        : `${item.pages} page${item.pages === 1 ? '' : 's'}`;
      download.hidden = !!item.standard;  // the standard page isn't a file
      if (!item.standard) {
        download.setAttribute('href', `/api/maintenance/pages/${item.id}/file?download=1`);
        download.setAttribute('download', '');
      }
      dialog.showModal();
      drawSheets(kit, item, sheets, { alive: () => mine === token, width: dialog.clientWidth - 80 });
    },
  };
}
