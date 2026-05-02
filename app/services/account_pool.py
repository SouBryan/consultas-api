import asyncio

from telethon import TelegramClient


class AccountPool:
    def __init__(self, clients: dict[str, TelegramClient]):
        if not clients:
            raise ValueError("AccountPool requer pelo menos um TelegramClient.")

        self._clients = clients
        self._labels = list(clients.keys())
        self._locks = {label: asyncio.Lock() for label in self._labels}
        self._selection_lock = asyncio.Lock()
        self._available_accounts = asyncio.Semaphore(len(self._labels))
        self._index = 0

    async def acquire(self) -> tuple[str, TelegramClient]:
        await self._available_accounts.acquire()

        async with self._selection_lock:
            total_accounts = len(self._labels)

            for offset in range(total_accounts):
                index = (self._index + offset) % total_accounts
                label = self._labels[index]
                account_lock = self._locks[label]

                if account_lock.locked():
                    continue

                await account_lock.acquire()
                self._index = (index + 1) % total_accounts
                return label, self._clients[label]

        self._available_accounts.release()
        raise RuntimeError("Nenhuma conta disponível para aquisição.")

    def release(self, label: str) -> None:
        try:
            account_lock = self._locks[label]
        except KeyError as exc:
            raise KeyError(f"Conta '{label}' não existe no pool.") from exc

        if not account_lock.locked():
            return

        account_lock.release()
        self._available_accounts.release()

    @property
    def size(self) -> int:
        return len(self._labels)