import asyncio
from datetime import date

from telethon import TelegramClient

from app.services.rate_limiter import RateLimiter


class AccountPool:
    def __init__(
        self,
        clients: dict[str, TelegramClient],
        rate_limiter: RateLimiter | None = None,
    ):
        if not clients:
            raise ValueError("AccountPool requer pelo menos um TelegramClient.")

        self._clients = clients
        self._labels = list(clients.keys())
        self._locks = {label: asyncio.Lock() for label in self._labels}
        self._selection_lock = asyncio.Lock()
        self._availability_event = asyncio.Event()
        self._availability_event.set()
        self._index = 0
        self._rate_limiter = rate_limiter
        self._current_day = date.today()
        self._queries_today: dict[str, int] = {label: 0 for label in self._labels}
        self._total_queries: dict[str, int] = {label: 0 for label in self._labels}

    async def acquire(
        self,
        exclude_labels: set[str] | None = None,
    ) -> tuple[str, TelegramClient]:
        excluded = exclude_labels or set()

        while True:
            selected: tuple[str, TelegramClient] | None = None

            async with self._selection_lock:
                total_accounts = len(self._labels)

                for offset in range(total_accounts):
                    index = (self._index + offset) % total_accounts
                    label = self._labels[index]
                    account_lock = self._locks[label]

                    if label in excluded or account_lock.locked():
                        continue

                    await account_lock.acquire()
                    self._index = (index + 1) % total_accounts
                    self._mark_selected_locked(label)
                    selected = (label, self._clients[label])
                    break

                if selected is None:
                    self._availability_event.clear()

            if selected is None:
                await self._availability_event.wait()
                continue

            label, client = selected
            if self._rate_limiter is not None:
                await self._rate_limiter.wait(label)
            return label, client

    async def try_acquire(
        self,
        exclude_labels: set[str] | None = None,
    ) -> tuple[str, TelegramClient] | None:
        excluded = exclude_labels or set()

        async with self._selection_lock:
            selected: tuple[str, TelegramClient] | None = None
            total_accounts = len(self._labels)

            for offset in range(total_accounts):
                index = (self._index + offset) % total_accounts
                label = self._labels[index]
                account_lock = self._locks[label]

                if label in excluded or account_lock.locked():
                    continue

                await account_lock.acquire()
                self._index = (index + 1) % total_accounts
                self._mark_selected_locked(label)
                selected = (label, self._clients[label])
                break

            if selected is None:
                return None

        label, client = selected
        if self._rate_limiter is not None:
            await self._rate_limiter.wait(label)
        return label, client

    def release(self, label: str) -> None:
        try:
            account_lock = self._locks[label]
        except KeyError as exc:
            raise KeyError(f"Conta '{label}' não existe no pool.") from exc

        if not account_lock.locked():
            return

        account_lock.release()
        self._availability_event.set()

    @property
    def size(self) -> int:
        return len(self._labels)

    async def stats_snapshot(self) -> dict[str, dict[str, int]]:
        async with self._selection_lock:
            self._rollover_day_if_needed_locked()
            return {
                label: {
                    "queries_today": self._queries_today.get(label, 0),
                    "total_queries": self._total_queries.get(label, 0),
                }
                for label in self._labels
            }

    def _mark_selected_locked(self, label: str) -> None:
        self._rollover_day_if_needed_locked()
        self._queries_today[label] = self._queries_today.get(label, 0) + 1
        self._total_queries[label] = self._total_queries.get(label, 0) + 1

    def _rollover_day_if_needed_locked(self) -> None:
        today = date.today()
        if today == self._current_day:
            return

        self._current_day = today
        self._queries_today = {label: 0 for label in self._labels}