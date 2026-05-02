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
            self._purge_expired_locked()
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
            self._purge_expired_locked()
            self._store[cache_key] = (time.monotonic(), payload)

    def make_key(self, command: str, base: str | None) -> str:
        normalized_base = base or "direct"
        raw_key = f"{command}::{normalized_base}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @property
    def size(self) -> int:
        with self._lock:
            self._purge_expired_locked()
            return len(self._store)

    def _purge_expired_locked(self) -> None:
        if not self._store:
            return

        now = time.monotonic()
        expired_keys = [
            cache_key
            for cache_key, (stored_at, _) in self._store.items()
            if now - stored_at > self._ttl_seconds
        ]

        for cache_key in expired_keys:
            self._store.pop(cache_key, None)