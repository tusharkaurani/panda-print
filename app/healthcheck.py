"""Container healthcheck: python -m app.healthcheck (exit 0 = healthy). No curl needed."""
import os
import sys
import urllib.request


def main() -> int:
    port = os.environ.get("PORT", "8095")
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=3) as r:
            return 0 if r.status == 200 else 1
    except Exception as e:
        print(f"unhealthy: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
