"""Tiny thread-safe TTL cache so many open dashboards don't hammer the printer."""
from __future__ import annotations

import threading
import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class TTLCache(Generic[T]):
    """Caches the result of `loader` for `ttl` seconds. Failures are not cached,
    but a failure also isn't retried by concurrent callers more than once."""

    def __init__(self, ttl: float, clock: Callable[[], float] = time.monotonic):
        self.ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._value: T | None = None
        self._at = float("-inf")

    def get(self, loader: Callable[[], T]) -> T:
        with self._lock:  # serialises loads: the second caller reuses the first's result
            if self._clock() - self._at < self.ttl:
                return self._value  # type: ignore[return-value]
            value = loader()
            self._value, self._at = value, self._clock()
            return value

    def clear(self) -> None:
        with self._lock:
            self._at = float("-inf")
