"""CSRF protection for state-changing requests.

There is no login (SSO lives in the reverse proxy), so we use a signed
double-submit token plus an Origin check:

* GET /api/csrf sets an HttpOnly, SameSite=Strict cookie holding a signed token
  and returns the same token in the JSON body.
* Every POST/PUT/PATCH/DELETE must send it back in the X-CSRF-Token header; the
  header must equal the cookie and carry a valid HMAC (so an attacker who can
  only plant a cookie cannot forge a matching pair).
* If the browser sends Origin (or Referer), its host must match the Host the
  request arrived on (or X-Forwarded-Host from the proxy, or TRUSTED_ORIGINS).
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

COOKIE = "panda_csrf"
HEADER = "X-CSRF-Token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def load_secret(configured: str | None, data_dir: Path) -> bytes:
    """SECRET_KEY if given, else a random key persisted (0600) under DATA_DIR."""
    if configured:
        return configured.encode()
    path = data_dir / "secret.key"
    try:
        return path.read_bytes().strip()
    except FileNotFoundError:
        pass
    data_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode()
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(key)
    return key


class Csrf:
    def __init__(self, secret: bytes, trusted_origins: tuple[str, ...] = ()):
        self._secret = secret
        self._trusted_hosts = {urlsplit(o).netloc or o for o in trusted_origins}

    def _sign(self, nonce: str) -> str:
        return hmac.new(self._secret, nonce.encode(), hashlib.sha256).hexdigest()[:32]

    def issue(self) -> str:
        nonce = secrets.token_hex(16)
        return f"{nonce}.{self._sign(nonce)}"

    def valid(self, token: str | None) -> bool:
        if not token or "." not in token:
            return False
        nonce, sig = token.split(".", 1)
        return hmac.compare_digest(sig, self._sign(nonce))

    def origin_ok(self, origin_or_referer: str | None, hosts: list[str]) -> bool:
        if not origin_or_referer:
            return True  # non-browser client or privacy setting; the token still applies
        netloc = urlsplit(origin_or_referer).netloc
        if not netloc:  # "null" or garbage
            return False
        return netloc in self._trusted_hosts or netloc in [h for h in hosts if h]

    def check(self, method: str, headers, cookies, hosts: list[str]) -> str | None:
        """Return an error message if the request must be rejected, else None."""
        if method in SAFE_METHODS:
            return None
        if not self.origin_ok(headers.get("Origin") or headers.get("Referer"), hosts):
            return "Cross-origin request blocked."
        header, cookie = headers.get(HEADER), cookies.get(COOKIE)
        if not (header and cookie and hmac.compare_digest(header, cookie) and self.valid(header)):
            return "Missing or invalid CSRF token. Reload the page and try again."
        return None
