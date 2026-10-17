# Contributing

Thanks for helping. panda-print is small on purpose: a few Python files, plain JavaScript, no build step.

## Run it

```bash
make dev     # http://localhost:8095, data in .local/data
make test    # the whole suite, against fakes only
```

The tests never touch a real printer or CUPS. `tests/fakes.py` has a fake printer and a fake network, and
`tests/conftest.py` has a fake CUPS server. Please keep it that way.

## House rules (tests enforce several)

- **Per printer, never global.** Anything about a printer keeps a `printer_id`, is removed through `ctx.printers.on_delete`,
  and works with any number of printers (`tests/test_multi_printer.py` walks the routes and tables).
- **CUPS is optional.** A printer with no print queue must still show its status, ink and page count. A feature that needs CUPS
  checks `service.cups_configured` and shows the "add CUPS" panel instead of an error (`tests/test_cups_optional.py`).
- **One printer's trouble stays its own.** Background work runs per printer (see `poller.py`).
- **No internet at run time.** No CDN, no external fonts, no analytics. Vendored code goes in `app/static/vendor/` with its licence.
- **Strict CSP.** No inline scripts or `style=""`; use classes, or `style.setProperty` from script.
- **Every button says what it does** with a `title` (a test fails otherwise). Words are plain and short.
- **Closed dialogs stay hidden.** Don't set `display` on a `dialog` that isn't `[open]`.
- Secrets, addresses and personal data never go in the repository.

## Adding a tool

See "Adding a tool" in [docs/development.md](docs/development.md). A tool is a folder under `app/tools/` with `create(ctx) -> Tool`, an optional API
blueprint and a `ui/ui.js`.

## Sending a change

1. Small, focused commits with a message that says why.
2. Add or update tests; run `make test`.
3. If you change something users see, check it in a browser on a narrow and a wide window, in light and dark.
4. If you can, say which printer you tried it on. The author has only verified a Brother DCP-T730DW against a real device.

By contributing you agree your work is released under the MIT licence of this project.

## Tests of the pages

`tests/test_ui_smoke.py` opens each tool's page under node against recorded answers of the real API (with a small fake DOM in `tests/js/`) and does what a person would do next. It fails on an exception while a tab opens, a console error, a stray `false`/`null`/`undefined` printed in the page, or a dropdown with no choices. It needs node and is skipped without it. New pages should be added to it.
