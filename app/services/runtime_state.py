import asyncio
from collections import deque
from datetime import date, datetime, timezone
import time
from typing import Any


class RuntimeState:
    def __init__(self):
        self._queries_today = 0
        self._total_queries = 0
        self._current_day = date.today()
        self._active_queries = 0
        self._last_error: dict[str, Any] | None = None
        self._recent_errors: deque[dict[str, Any]] = deque(maxlen=50)
        self._query_timestamps: deque[float] = deque()
        self._cache_hits = 0
        self._cache_misses = 0
        self._cache_stale_hits = 0
        self._lock = asyncio.Lock()
        self._idle_event = asyncio.Event()
        self._idle_event.set()

    async def start_query(self) -> None:
        async with self._lock:
            self._rollover_day_if_needed()
            self._queries_today += 1
            self._total_queries += 1
            self._active_queries += 1
            self._query_timestamps.append(time.time())
            self._purge_old_query_timestamps_locked()
            self._idle_event.clear()

    async def finish_query(self) -> None:
        async with self._lock:
            if self._active_queries > 0:
                self._active_queries -= 1
            if self._active_queries == 0:
                self._idle_event.set()

    async def record_error(self, error_type: str, message: str, **context: Any) -> None:
        async with self._lock:
            error_entry = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": error_type,
                "message": message,
                "context": context,
            }
            self._last_error = error_entry
            self._recent_errors.append(error_entry)

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            self._rollover_day_if_needed()
            self._purge_old_query_timestamps_locked()
            return {
                "queries_today": self._queries_today,
                "total_queries": self._total_queries,
                "queries_last_hour": len(self._query_timestamps),
                "active_queries": self._active_queries,
                "last_error": self._last_error,
                "last_errors": list(reversed(self._recent_errors)),
            }

    async def record_cache_event(self, outcome: str) -> None:
        async with self._lock:
            if outcome == "hit":
                self._cache_hits += 1
                return
            if outcome == "stale":
                self._cache_stale_hits += 1
                return
            if outcome == "miss":
                self._cache_misses += 1

    async def metrics_snapshot(self) -> dict[str, Any]:
        async with self._lock:
            self._rollover_day_if_needed()
            self._purge_old_query_timestamps_locked()
            total_cache_events = self._cache_hits + self._cache_misses + self._cache_stale_hits
            served_from_cache = self._cache_hits + self._cache_stale_hits
            return {
                "queries_today": self._queries_today,
                "total_queries": self._total_queries,
                "queries_last_hour": len(self._query_timestamps),
                "active_queries": self._active_queries,
                "cache_hits": self._cache_hits,
                "cache_misses": self._cache_misses,
                "cache_stale_hits": self._cache_stale_hits,
                "cache_hit_rate": round(served_from_cache / total_cache_events, 4) if total_cache_events else 0.0,
            }

    async def last_errors(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self._lock:
            if limit <= 0:
                return []
            return list(reversed(list(self._recent_errors)[-limit:]))

    async def wait_for_idle(self, timeout: float) -> bool:
        if self._idle_event.is_set():
            return True

        try:
            await asyncio.wait_for(self._idle_event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _rollover_day_if_needed(self) -> None:
        today = date.today()
        if today != self._current_day:
            self._current_day = today
            self._queries_today = 0

    def _purge_old_query_timestamps_locked(self) -> None:
        now = time.time()
        while self._query_timestamps and now - self._query_timestamps[0] >= 3600:
            self._query_timestamps.popleft()