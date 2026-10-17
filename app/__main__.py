"""python -m app: run the dashboard with waitress (single process, a few threads)."""
import logging

from waitress import serve

from app.factory import create_app
from app.settings import Settings


def main() -> None:
    settings = Settings.from_env()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    app = create_app(settings)
    log = logging.getLogger("panda-print")
    log.info("panda-print listening on %s:%s", settings.host, settings.port)
    # 8 threads: a page load fires several slow device calls at once
    serve(app, host=settings.host, port=settings.port, threads=8, ident="panda-print",
          # room for a page upload; Flask itself caps every other request at 64 KB
          max_request_body_size=(settings.max_page_mb + 1) * 2**20, connection_limit=50, channel_timeout=30, _quiet=True)


if __name__ == "__main__":
    main()
