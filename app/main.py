from contextlib import asynccontextmanager
import time

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import settings
from app.services.account_pool import AccountPool
from app.services.cache import ResultCache
from app.services.rate_limiter import RateLimiter
from app.services.request_rate_limiter import RequestRateLimiter
from app.utils.logger import configure_logging, get_logger


configure_logging()
logger = get_logger("main")


def get_pool(request: Request) -> AccountPool:
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise RuntimeError("AccountPool não foi inicializado.")
    return pool


def get_cache(request: Request) -> ResultCache:
    cache = getattr(request.app.state, "cache", None)
    if cache is None:
        raise RuntimeError("ResultCache não foi inicializado.")
    return cache


@asynccontextmanager
async def lifespan(app: FastAPI):
    clients = await _connect_clients()
    rate_limiter = RateLimiter(settings.rate_limit_interval)
    app.state.pool = AccountPool(clients, rate_limiter=rate_limiter)
    app.state.cache = ResultCache(settings.cache_ttl_hours)
    app.state.request_rate_limiter = RequestRateLimiter(settings.max_requests_per_minute)
    app.state.started_at = time.monotonic()

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


def _get_client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        first_ip = forwarded_for.split(",", 1)[0].strip()
        if first_ip:
            return first_ip

    if request.client is not None and request.client.host:
        return request.client.host

    return "unknown"


@app.middleware("http")
async def enforce_api_rate_limit(request: Request, call_next):
    if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
        return await call_next(request)

    limiter = getattr(request.app.state, "request_rate_limiter", None)
    if limiter is None:
        return await call_next(request)

    client_ip = _get_client_ip(request)
    allowed, retry_after = await limiter.check(client_ip)
    if not allowed:
        logger.warning(
            "Rate limit da API excedido.",
            extra={
                "event": "api_rate_limit_hit",
                "ip": client_ip,
                "path": request.url.path,
                "retry_after": retry_after,
            },
        )
        return JSONResponse(
            status_code=429,
            content={"detail": "Rate limit excedido. Tente novamente mais tarde."},
            headers={"Retry-After": str(retry_after)},
        )

    return await call_next(request)


@app.get("/api/health", tags=["system"])
async def health_check(request: Request) -> dict[str, float | int | str]:
    pool = get_pool(request)
    started_at = getattr(request.app.state, "started_at", time.monotonic())
    uptime = max(0.0, time.monotonic() - started_at)
    return {
        "status": "ok",
        "accounts": pool.size,
        "uptime": round(uptime, 3),
    }


from app.routes.consultas import router as consultas_router


app.include_router(consultas_router)