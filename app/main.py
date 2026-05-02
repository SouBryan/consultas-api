from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import settings
from app.services.account_pool import AccountPool


def get_pool(request: Request) -> AccountPool:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("AccountPool não foi inicializado.")
    return pool


@asynccontextmanager
async def lifespan(app: FastAPI):
    clients = await _connect_clients()
    app.state.pool = AccountPool(clients)

    try:
        yield
    finally:
        for client in clients.values():
            await client.disconnect()


async def _connect_clients() -> dict[str, TelegramClient]:
    clients: dict[str, TelegramClient] = {}
    credentials = {
        "bryan": settings.telegram_session_string_bryan,
        "bryan2": settings.telegram_session_string_bryan2,
    }

    for label, session_string in credentials.items():
        client = TelegramClient(
            StringSession(session_string),
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        await client.connect()

        if not await client.is_user_authorized():
            await client.disconnect()
            raise RuntimeError(f"Session string da conta '{label}' não está autorizada.")

        await client.get_me()
        clients[label] = client

    return clients


app = FastAPI(
    title="Consultas API",
    version="0.1.0",
    lifespan=lifespan,
)


from app.routes.consultas import router as consultas_router


app.include_router(consultas_router)