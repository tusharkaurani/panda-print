"""Friendly errors for upstream (printer / CUPS) failures."""
from __future__ import annotations

import http.client
import socket
import urllib.error


class SourceError(Exception):
    """An upstream data source (printer, CUPS, SNMP) could not be used.

    `message` is safe to show to the user; `hint` says what to check.
    """

    def __init__(self, source: str, message: str, hint: str | None = None):
        super().__init__(message)
        self.source = source
        self.message = message
        self.hint = hint

    def to_dict(self) -> dict:
        d = {"source": self.source, "message": self.message}
        if self.hint:
            d["hint"] = self.hint
        return d


def explain(source: str, target: str, exc: BaseException) -> SourceError:
    """Turn a low-level network exception into a SourceError."""
    if isinstance(exc, SourceError):
        return exc
    target = target[:1].upper() + target[1:]
    if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, BaseException):
        exc = exc.reason
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return SourceError(source, f"{target} did not answer in time.",
                           "Is it powered on and on the network?")
    if isinstance(exc, ConnectionRefusedError):
        return SourceError(source, f"{target} refused the connection.",
                           "Is the service running and the address/port correct?")
    if isinstance(exc, socket.gaierror):
        return SourceError(source, f"Could not resolve the address of {target}.",
                           "Check the host name in the configuration.")
    if isinstance(exc, (OSError, http.client.HTTPException)):
        return SourceError(source, f"Could not talk to {target}: {exc}.",
                           "Check the network path and the address in the configuration.")
    return SourceError(source, f"Unexpected problem talking to {target}: {exc}")
