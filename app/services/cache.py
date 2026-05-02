import hashlib
import time
from threading import Lock
from typing import Any


class ResultCache:
    def __init__(self, ttl_hours: int = 24):
        self._ttl_seconds = max(0, ttl_hours) * 3600
        self._store: dict[str, tuple[float, dict[str, Any]]] = {}
        self._lock = Lock()

    def get(self, command: str, base: str | None) -> dict[str, Any] | None:
        cache_key = self.make_key(command, base)

        with self._lock:
            entry = self._store.get(cache_key)
            if entry is None:
                return None

            stored_at, payload = entry
            if time.monotonic() - stored_at > self._ttl_seconds:
                self._store.pop(cache_key, None)
                return None

            return payload

    def set(self, command: str, base: str | None, payload: dict[str, Any]) -> None:
        cache_key = self.make_key(command, base)
        with self._lock:
            self._store[cache_key] = (time.monotonic(), payload)

    def make_key(self, command: str, base: str | None) -> str:
        normalized_base = base or "direct"
        raw_key = f"{command}::{normalized_base}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()