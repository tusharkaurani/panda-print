# Third-party software

panda-print is MIT-licensed (see `LICENSE`). It uses the following, all under permissive licences.

| Component | Used for | Licence | How it is included |
|---|---|---|---|
| [Flask](https://flask.palletsprojects.com/) | web framework | BSD-3-Clause | Python dependency (`requirements.txt`) |
| [waitress](https://docs.pylonsproject.org/projects/waitress/) | web server | ZPL-2.1 | Python dependency |
| [PyYAML](https://pyyaml.org/) | reading `links.yml` and the utilities list | MIT | Python dependency |
| [pdf.js](https://github.com/mozilla/pdf.js) 6.3.289 | drawing PDF thumbnails and previews in the browser | Apache-2.0 | unmodified files in `app/static/vendor/pdfjs/`, with its `LICENSE` and a `NOTICE.md` |
| Python, Alpine Linux | the container image | PSF, various | base image `python:3.12-alpine` |

Test and development only: pytest (MIT).

No fonts, icons, scripts or images are loaded from a third party at run time. Icons are drawn by the app.

## Trademarks

Brother, CUPS, HP, Canon, Avery and the other names in this project belong to their owners. panda-print is not affiliated with or endorsed by any of them. They appear only to say what the software works with or links to.

## Links to other sites

The Print Utilities tab links to third-party websites. They are not part of this project, they can change or start charging, and the project has no connection to them. See the note at the top of `app/tools/utilities/catalog.yml`.
