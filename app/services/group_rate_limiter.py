import asyncio
import time

from app.utils.logger import get_logger


logger = get_logger("services.group_rate_limiter")


class GroupRateLimiter:
    def __init__(self, min_interval: float = 2.0):
        self.min_interval = min_interval
        self._last_call: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, group_id: int, *, adapter: str | None = None) -> float:
        key = str(group_id)
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()

        async with self._locks[key]:
            now = time.monotonic()
            elapsed = now - self._last_call.get(key, 0.0)
            delay = max(0.0, self.min_interval - elapsed)

            if delay > 0:
                logger.info(
                    "Rate limit por grupo aplicado.",
                    extra={
                        "event": "telegram_group_rate_limit",
                        "group_id": group_id,
                        "adapter": adapter,
                        "delay_seconds": round(delay, 3),
                    },
                )
                await asyncio.sleep(delay)

            self._last_call[key] = time.monotonic()
            return delay