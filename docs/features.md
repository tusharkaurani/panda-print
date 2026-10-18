# Features in detail

[← Back to the README](../README.md)

## CUPS is optional

panda-print talks to the printer directly (IPP for status and ink, SNMP for the page count). **CUPS**, the print service on
a Pi, Mac or Linux box, is only needed for printing. CUPS can run on the same machine or on another one on your network.

| Without CUPS you get | With CUPS you also get |
|---|---|
| Printer status and ink levels | The print queue and recent print jobs |
| Lifetime page count, and any print noticed from it | Telling a print from a computer apart from one sent straight to the printer |
| Last print, idle time | **Print now** (with a preview) |
| Several printers, quick links, the Print Utilities tab | Automatic printing when the printer sits unused, and your own pages in rotation |

A printer is "without CUPS" when it has no print queue. Cards that need CUPS show a faded sketch of what they would hold, what
CUPS adds and how to set it up (install CUPS, add the printer to it, then choose the queue under **Printers → Edit**). Nothing is
shown as an error for a choice you haven't made. A queue that is set but unreachable still shows an error with a retry.

## Automatic nozzle check (Nozzle Care tab)

Two words to keep apart: a **print** is something that came out of the printer, and a **nozzle check** is the test page; **reading** the page total prints nothing and is how the app notices prints (every 5 minutes by default). The tab's cards: *Last print* (anything, from any device), *Idle for* (time since then), *Last auto print* (a nozzle check printed automatically, by this app or by another timer found in the CUPS history) and *Last manual print* (one you started with **Print now**). *Recent prints* lists them with where each came from.

Inkjet nozzles clog when a printer sits unused. panda-print can print one small test page (four colour bars) when a
printer has gone a set number of days without printing anything. It replaces a cron or systemd timer on the host: if
you used one, retire it once you switch this on (otherwise both would print).

How it decides, per printer:

1. The idle clock is the printer's last print of any kind: a document from any device, or a nozzle check (which counts, so one check restarts the clock).
2. When the printer has been idle for the set number of days **and** the time of day is past the *don't print before* time, it sends one check to the printer's CUPS queue (IPP `Print-Job`; no `lp` needed in the container).
3. Guards: it only acts when the page counter can be read (so "idle" is known, not guessed). When a check comes due, the app reads the counter once more right before sending, so a page printed a minute ago cancels it, and an unreadable printer holds it back; after a failed attempt it waits an hour; it never sends a second check while an earlier one (sent in the last 6 hours) may still be printing; and an app-sent check whose page never appeared on the counter doesn't restart the idle clock.
4. It is **off by default**. Turn it on with the switch in the **Automation** card, per printer. `NOZZLE_AUTO=1` makes new printers start with it on.

The time is the server's clock, so set `TZ` (for example `TZ=Europe/London`) in the container; the image defaults to UTC.

The card's settings (all per printer, defaults from the environment so a new printer starts exactly as configured there):

| Setting | Default | What it does |
| --- | --- | --- |
| Print the nozzle check automatically | off | The switch (`NOZZLE_AUTO`) |
| Idle days before a check is due | 7 | Drives the automatic check and the "Next check" badge |
| Don't print before | 10:00 | Quiet hours, in the container's time zone: `TZ` if set, else the container's default (UTC in the image) (`NOZZLE_CHECK_TIME`) |
| Read the page counter every | 5 minutes | How often prints are noticed |
| A new page counts as the check if it prints within | 30 minutes | After a check is sent the page total rises by one; a rise within this time is credited to the check, a later one is treated as someone else's print |
| Also recognise checks whose job name has | `nozzle-check` | Spots nozzle-check pages sent to CUPS by another timer, so they aren't mistaken for someone printing |

Only values you change are stored; the rest keep following the defaults. **Save settings** is only enabled when something changed, and **Reset to defaults** puts the settings back (it leaves the on/off switch as it is, because the switch isn't one of the settings). Times on the card, including the "Next check" badge, are shown in the container's time zone.
The logic is in `app/tools/maintenance/schedule.py` (pure rules, with tests) and `Service.run_schedule`.

## Useful pages instead of a test pattern

An idle print that only shows colour bars wastes a sheet. Instead the app can rotate through pages you choose, for
example colouring pages, so the print is worth having.

- **Page to print**, on the Automation card: *Standard test page* (the colour bars) or *Rotate my pages*. **Manage pages…** opens the library.
- **The library** is shared by every printer. Add PDF, PNG or JPEG files (a picker or drag and drop; up to `MAX_PAGE_MB` each, `MAX_PAGES` files and `MAX_LIBRARY_MB` in total). Files are checked by their contents, not their names, and PostScript is refused. They are stored in `/data/pages`, so they live in the same volume as the history; include it in backups.
- **Preview and download:** every row shows a real picture of the page (the standard page is drawn as an exact replica of the colour bars). Click it, or the eye icon, to see the page the way it will be sent; PDFs show their pages (the first 12). Uploaded pages also have a **Download** button for the original file. The standard page is built in, so its Download and Delete are greyed out, and nothing can be uploaded over it. Thumbnails are small PNGs your browser draws when you upload (kept in `/data/pages` next to the file); a page without one falls back to the picture itself or a PDF icon.
- **Per printer:** the order, and a switch for each page (the standard test page is one of the items, so you can drop it). Each automatic print, and **Print now**, takes the next enabled page and then loops. Deleted or switched-off pages are skipped; with nothing switched on, the standard page is used.
- **Print a chosen page** (the Print button on a page, with a confirm) sends exactly that page without moving the rotation, which is also how to test a page you just added.
- **Both sides:** for a printer that can flip the paper **by itself**, the Pages dialog offers *Multi-page files print on both sides* (flip on the long edge or the short edge), which saves paper. It applies to files with more than one page, for automatic prints, **Print now** and each page's Print button; one-page files and pictures print as they are. Printing here is unattended, so the option only appears when both the printer and its CUPS queue report two-sided printing (a printer without an automatic duplexer doesn't list it) and is never sent otherwise. If the printer is off when asked, its last confirmed ability is used, and a printer never confirmed counts as not capable. Reset to defaults keeps the choice.
- **Ink:** a page only moves the inks it uses; a black-and-white page does not exercise cyan, magenta or yellow. The dialog says so, and you decide what to include. To be safe, set *Also print the standard test page every N prints* (0 = never) and every Nth print is the colour-bar page on top of the rotation.
- **Printing:** the file goes to the printer's CUPS queue as it is (`print-scaling=fit`, so a picture isn't cropped) and the host's CUPS converts it for the printer. If a page fails to print, the History row shows CUPS's reason; try that page with the Print button, and check that CUPS can print a PDF or picture from the command line (`lp -d <queue> file.pdf`).
- **Page counts:** a print records how many sheets it should make (PDFs are counted; pictures are one). The page counter is credited to the print page by page, so a 2-page PDF isn't mistaken for someone else's print. If a PDF's page count can't be read it is assumed to be one page.

PDFs are drawn in the browser with [pdf.js](https://github.com/mozilla/pdf.js) (Apache-2.0), bundled in `app/static/vendor/pdfjs`, so nothing is loaded from a CDN. It is fetched only when a PDF needs drawing, and runs with script evaluation off.

## Print Utilities tab

A tab of cards that open free online tools for things worth printing (passport photos, student worksheets, colouring pages, calendars and ruled paper, a PDF toolbox, posters, labels and crafts), grouped by category with at most four cards in a row. Each card is a drawing of the kind of page, the tool's name, a one-line description and its site; it opens in a new tab. The cards are drawings made by the app, so opening the tab loads nothing from those sites.

Every button and tab in the app shows what it does when you hover over it (a test fails if a new button has no hover text).

## When a printer is off

A printer that is switched off or off the network is a normal case, not a crash. The Overview keeps the last known ink levels, model and page count on screen, dimmed, with a note ("The printer isn't answering… from 2 hours ago") and a Try again button; the badge says Offline. The last good answer is stored in the database, so it survives a restart. Nozzle Care says it can't read the page count. The automatic print waits (it only prints when it can tell the printer is idle) and goes ahead by itself when the printer returns. **Print now** warns first: the page would wait in the CUPS queue and print when the printer is back. A printer that has never answered shows just the reason. Everything recovers on its own, and one printer being off never affects another.

## Several printers

Add as many printers as you like (**Manage printers…** in the header menu). The rules the code and `tests/test_multi_printer.py` hold to:

- **Everything per printer stays per printer:** status, history, automatic-print settings, rotation order and switches, rate limits, locks. Changing one printer never changes another.
- **Shared on purpose:** the library of pages (a new page appears in every printer's list; deleting one removes it from every list) and the launcher links file (a link can be limited to some printers with `printers:`).
- **One printer's trouble stays its own:** an unreachable address, a slow CUPS or a failing queue shows as that printer's error card. The background poller gives each printer its own worker, never runs one printer twice at once, and a hung printer doesn't delay the others.
- **Removing a printer** deletes its history and settings (not the shared pages) and, if it was selected, the header falls back to the first one. Removing the last one returns to the welcome wizard. A new printer that reuses a removed printer's name starts clean.
- **Editing** keeps the printer's id, so renaming keeps its history; changing its address or queue rebuilds its connection, and the print lock survives the rebuild so two copies can't print at once.
- **Names and ids:** any name works (symbols, other alphabets, near-identical names). Ids are made from the name, made unique, and never clash with the app's own words (`pages`, `probe`, `discover`, ...).
- Two CUPS queues on one physical printer are fine; each keeps its own books.

The tests use fakes only. `test_a_removed_printer_leaves_nothing_in_any_table` fails if a new tool adds a `printer_id` table and forgets `on_delete`, and `test_every_printer_route_answers_404_for_an_unknown_printer_and_never_500` walks the URL map, so a new per-printer route is covered automatically.

## Passport Sheet Maker (inside Print Utilities)

Open **Print Utilities** and choose **Passport Sheet Maker** under *Built in*; a breadcrumb takes you back. 
Lay out copies of a photo you have already edited and cropped (a passport or ID photo, say) on a sheet of paper.

1. **Choose the photos** (JPEG, PNG or WebP; up to 20 different photos on one sheet; the browser converts each to a high-quality JPEG, so every pixel is kept, stepping the quality down only if they together exceed the upload limit). Nothing is stored: the photos are sent for each preview, download or print and then forgotten.
   - With several photos the sheet is shared between them, equally to begin with (three photos on a 30-place sheet get 10 each). The total is always what fits: change one photo's number and the others give or take so the total stays the same, keeping the ratio they had between themselves (raising the first of 10, 10, 10 to 14 gives 14, 8, 8). Use the **−** and **+** buttons, type a number, or drag the dividers on the bar that shows the whole sheet; a divider moves places between the two photos beside it, and the arrow keys do the same one place at a time. If you later change the paper, the size or the margins so that a different number fits, the same split is kept as a ratio, and every photo keeps at least one place while there is room for all of them. To print fewer than the page holds, set **How many** to a number of photos. Photos are placed in blocks in reading order, so each photo's copies sit together.
2. **Say how big each copy is:** pick a common size or type the width and height in mm, cm or inches. The common sizes are examples (35 × 45 mm, 2 × 2 in, 50 × 70 mm, ...); check your country's rules.
3. **Pick the paper.** With a CUPS printer selected the list is the sizes that printer's queue offers (including borderless ones); otherwise common sizes, and a custom size for the PDF. **Print on** lets you use only the top or the bottom half of the paper (the top half of A4 is an A5 area) and leaves the rest blank, so you can use it later: print the other half with the opposite choice and put the sheet back in the tray the same way it went in (let the ink dry first). The PDF is still a whole sheet, so it feeds normally; the blank part is tinted in the preview only. A half page is laid out the way the paper is fed, so "Sheet direction" is fixed, and margins are measured inside the half (only edges on the paper's own edge are checked against what the printer can reach). Set the margin and the space between photos, and whether to fill the page, make a grid (for example 5 columns × 6 rows) or print a set number. The sheet direction can be automatic, which picks whichever fits more.
4. **Finish:** optional cut marks and outlines for the whole sheet. Cropping is **per photo**: each photo's row has **Crop and fit**, where you choose to crop that photo to the frame (and move it left/right and up/down in its frame) or show all of it on white. One photo can be cropped while another, already the right shape, is left alone; **Use for all photos** copies one photo's choice to the rest. The preview updates as you type and says how many fit and how sharp the photos will be (a warning appears under about 250 pixels per inch for the softest photo used). On a wide screen the preview stays pinned beside the settings as you scroll; on a phone it is a slim strip pinned at the top. The quick-links strip is hidden on this tab so the preview isn't cut short at the end of the page.
5. **Download the PDF**, or **Preview and print**. Printing shows the real PDF first, then sends it through the printer's CUPS queue.

How it prints: the PDF page is exactly the paper size and is sent with scaling off (`print-scaling=none`), so each photo comes out at the size you chose. You also pick the paper type (plain, glossy photo, ...), the print quality and the number of copies, from what the queue offers; a resolution picker appears only if the printer offers more than one. A landscape layout is turned onto a portrait page, the way paper is fed into the printer, with the photos still upright on the sheet. Margins closer to the edge than the printer can reach are flagged, unless you choose a borderless paper size. Printing needs CUPS; without it you can still download the PDF. Printing is rate-limited per printer, like Print now.

Not verified on real hardware yet: print one sheet on plain paper and measure a photo with a ruler before cutting a stack. The layout, the PDF and the job options are covered by tests against fakes, and the PDF opens in a normal viewer.

### Presets

Presets are a quick start, like autofill. Click one and the settings below are filled in (the controls that changed light up for a moment), then change anything you like. A preset holds the photo size, unit, paper, margins, layout, part of the page, finishing and print choices, never the photos. Paper type and quality are filled in only if the selected printer offers them, and the paper is matched by its CUPS name or its size. Nothing you change afterwards is saved back to a preset.

- **Save these settings as a preset…** keeps the current settings as a new preset of your own, in the database, so every device that uses this panda-print sees it. Saving under an existing name replaces it (up to 50). A saved preset has a small ✕ on its button to delete it.
- Two come with the app: **35 × 45 mm on A4 (as A5)** (the top half of A4) and **35 × 45 mm on A4 (210 × 297 mm)**. They can't be deleted. They are listed in `app/tools/photosheet/presets.yml`, which is read on demand, so editing it needs no restart; the comments at the top describe each setting. The server can write the text for a preset you saved (`GET /api/photosheet/presets/<id>/yaml`), if you want to ship it with the code.

## Label Sheet Maker (inside Print Utilities)

Open **Print Utilities** and choose **Label Sheet Maker** under *Built in*. It puts text on the labels of a sticker sheet: address labels, jar labels, name tags.

1. **Pick the sheet.** The list holds common Avery packs for A4 (L7159, L7160, L7161, L7162, L7163, L7164, L7165, L7173, L7651) and US Letter (5160, 5161, 5162, 5163, 5164, 5167), with the published label sizes. Choose **My own grid** (or edit anything under *Measurements*) to enter the labels across and down, the label size, the left and top margins, the space between labels and the corner rounding, in mm, cm or inches. Add packs of your own to `app/tools/labels/templates.yml` (see the comment at the top); the file is read on each visit, so no restart is needed. Check a pack's numbers against your own sheet with a ruler: manufacturers revise them.
2. **Say what goes on the labels.** Type or paste text, with a blank line between labels (an address is several lines) or one label per line. Or choose **From a spreadsheet (CSV)**: pick a file saved from Excel, Numbers or Google Sheets (comma, semicolon or tab separated; the first row is the column names) or paste it. Write what each label says with the column names in `{ }`, for example `{First} {Last}`, then `{Street}`, then `{City}`; the column buttons insert them. A line whose values are all empty is left out, so a missing second address line leaves no gap. Leave the box empty to print every column, one per line.
3. **Set the text.** A size in points, or **as large as fits every label** (one size for all, so the sheet looks even; one very long label makes all of them smaller). Left, centre or right, top or middle, space inside the label, first line bold (handy for names) or all bold. Long lines wrap; text that still doesn't fit is reported, with how many labels are affected. The font is Helvetica, which every printer and PDF viewer has; characters it can't show (for example Chinese or Arabic) print as "?" and the page lists them.
4. **Say how many.** Copies of each label, or **Repeat the list to fill the sheet** (one return-address label ×30). **Start at label number** is for a part-used sheet: places before it stay empty, and you can click a label in the preview to choose it. A list longer than one sheet runs on to more sheets (up to 100 sheets and 5000 labels), and the preview has Previous and Next buttons.
5. **Download the PDF**, or **Print…**: the real PDF is shown first, then sent through the printer's CUPS queue. The page is exactly the paper size and is sent with scaling off (`print-scaling=none`), so the text lands on the labels. The paper must be the one the label sheet is on (the page picks the printer's name for it and refuses a different size); paper type (try "Labels" if offered) and quality come from what the queue offers. Text closer to the paper's edge than the printer can reach is flagged.

**Show label borders** draws each label's outline: print one sheet that way on plain paper and hold it against a real sheet to check the fit, then switch it off for the real labels.

Not verified on real hardware yet. The geometry, the text setting, the spreadsheet reading and the PDF are covered by tests against fakes.
