import asyncio
from datetime import date, datetime, timezone
from typing import Any


class RuntimeState:
    def __init__(self):
        self._queries_today = 0
        self._current_day = date.today()
        self._active_queries = 0
        self._last_error: dict[str, Any] | None = None
        self._lock = asyncio.Lock()
        self._idle_event = asyncio.Event()
        self._idle_event.set()

    async def start_query(self) -> None:
        async with self._lock:
            self._rollover_day_if_needed()
            self._queries_today += 1
            self._active_queries += 1
            self._idle_event.clear()

    async def finish_query(self) -> None:
        async with self._lock:
            if self._active_queries > 0:
                self._active_queries -= 1
            if self._active_queries == 0:
                self._idle_event.set()

    async def record_error(self, error_type: str, message: str, **context: Any) -> None:
        async with self._lock:
            self._last_error = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": error_type,
                "message": message,
                "context": context,
            }

    async def snapshot(self) -> dict[str, Any]:
        async with self._lock:
            self._rollover_day_if_needed()
            return {
                "queries_today": self._queries_today,
                "active_queries": self._active_queries,
                "last_error": self._last_error,
            }

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