import asyncio
import math
import time
from collections import deque


class RequestRateLimiter:
    def __init__(self, max_requests: int = 20, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._requests: dict[str, deque[float]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def check(self, key: str) -> tuple[bool, int]:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()

        async with self._locks[key]:
            now = time.monotonic()
            timestamps = self._requests.setdefault(key, deque())

            while timestamps and now - timestamps[0] >= self.window_seconds:
                timestamps.popleft()

            if len(timestamps) >= self.max_requests:
                retry_after = max(1, math.ceil(self.window_seconds - (now - timestamps[0])))
                return False, retry_after

            timestamps.append(now)
            return True, 0