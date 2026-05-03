from dataclasses import dataclass
import hashlib
import time
from threading import Lock
from typing import Any


@dataclass
class CacheEntry:
    command: str
    base: str | None
    stored_at: float
    expires_at: float
    stale_until: float
    payload: dict[str, Any]


class ResultCache:
    def __init__(self, ttl_hours: int = 24, stale_ttl_seconds: int = 3600):
        self._ttl_seconds = max(0, ttl_hours) * 3600
        self._stale_ttl_seconds = max(0, stale_ttl_seconds)
        self._store: dict[str, CacheEntry] = {}
        self._lock = Lock()

    def get(self, command: str, base: str | None) -> dict[str, Any] | None:
        cache_key = self.make_key(command, base)

        with self._lock:
            self._purge_dead_entries_locked()
            entry = self._store.get(cache_key)
            if entry is None:
                return None

            now = time.monotonic()
            if now > entry.expires_at:
                return None

            return entry.payload

    def get_stale(self, command: str, base: str | None) -> dict[str, Any] | None:
        cache_key = self.make_key(command, base)

        with self._lock:
            self._purge_dead_entries_locked()
            entry = self._store.get(cache_key)
            if entry is None:
                return None

            now = time.monotonic()
            if now <= entry.expires_at or now > entry.stale_until:
                return None

            return entry.payload

    def set(self, command: str, base: str | None, payload: dict[str, Any]) -> None:
        cache_key = self.make_key(command, base)
        with self._lock:
            self._purge_dead_entries_locked()
            now = time.monotonic()
            self._store[cache_key] = CacheEntry(
                command=command,
                base=base,
                stored_at=now,
                expires_at=now + self._ttl_seconds,
                stale_until=now + self._ttl_seconds + self._stale_ttl_seconds,
                payload=payload,
            )

    def delete(self, command: str, base: str | None = None) -> int:
        with self._lock:
            self._purge_dead_entries_locked()
            keys_to_remove = [
                cache_key
                for cache_key, entry in self._store.items()
                if entry.command == command and (base is None or entry.base == base)
            ]

            for cache_key in keys_to_remove:
                self._store.pop(cache_key, None)

            return len(keys_to_remove)

    def make_key(self, command: str, base: str | None) -> str:
        normalized_base = base or "direct"
        raw_key = f"{command}::{normalized_base}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @property
    def size(self) -> int:
        with self._lock:
            self._purge_dead_entries_locked()
            now = time.monotonic()
            return sum(1 for entry in self._store.values() if now <= entry.expires_at)

    @property
    def stale_size(self) -> int:
        with self._lock:
            self._purge_dead_entries_locked()
            now = time.monotonic()
            return sum(1 for entry in self._store.values() if entry.expires_at < now <= entry.stale_until)

    def _purge_dead_entries_locked(self) -> None:
        if not self._store:
            return

        now = time.monotonic()
        expired_keys = [
            cache_key
            for cache_key, entry in self._store.items()
            if now > entry.stale_until
        ]

        for cache_key in expired_keys:
            self._store.pop(cache_key, None)