import asyncio
import time

from app.utils.logger import get_logger


logger = get_logger("services.rate_limiter")


class RateLimiter:
    def __init__(self, min_interval: float = 3.0):
        self.min_interval = min_interval
        self._last_call: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def wait(self, label: str) -> float:
        if label not in self._locks:
            self._locks[label] = asyncio.Lock()

        async with self._locks[label]:
            now = time.monotonic()
            elapsed = now - self._last_call.get(label, 0.0)
            delay = max(0.0, self.min_interval - elapsed)

            if delay > 0:
                logger.info(
                    "Rate limit por conta aplicado.",
                    extra={
                        "event": "telegram_account_rate_limit",
                        "account": label,
                        "delay_seconds": round(delay, 3),
                    },
                )
                await asyncio.sleep(delay)

            self._last_call[label] = time.monotonic()
            return delay