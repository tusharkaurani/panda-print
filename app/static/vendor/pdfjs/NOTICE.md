# pdf.js (vendored)

This folder holds an unmodified copy of two files from **pdfjs-dist 6.3.289**
(https://github.com/mozilla/pdf.js, published to npm as `pdfjs-dist`), licensed under the
**Apache License 2.0** (see `LICENSE`). panda-print loads them from its own server, with no CDN,
only to draw page thumbnails and the preview of uploaded PDFs in the browser.

- `pdf.min.mjs`: the library
- `pdf.worker.min.mjs`: its worker

To update, replace both files with the same files from a newer `pdfjs-dist` release.
