import asyncio
from contextlib import asynccontextmanager
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from telethon import TelegramClient
from telethon.sessions import StringSession

from app.config import settings
from app.models.requests import ConsultationType
from app.models.responses import HealthResponse, StatusResponse
from app.services.account_pool import AccountPool
from app.services.bot_health import BotHealth
from app.services.bot_router import BotRouter
from app.services.cache import ResultCache
from app.services.group_rate_limiter import GroupRateLimiter
from app.services.rate_limiter import RateLimiter
from app.services.request_rate_limiter import RequestRateLimiter
from app.services.runtime_state import RuntimeState
from app.utils.logger import configure_logging, get_logger, log_context


configure_logging()
logger = get_logger("main")
GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS = 30.0


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


def get_runtime_state(request: Request) -> RuntimeState:
    runtime_state = getattr(request.app.state, "runtime_state", None)
    if runtime_state is None:
        raise RuntimeError("RuntimeState não foi inicializado.")
    return runtime_state


def get_bot_router(request: Request) -> BotRouter:
    bot_router = getattr(request.app.state, "bot_router", None)
    if bot_router is None:
        raise RuntimeError("BotRouter não foi inicializado.")
    return bot_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    clients = await _connect_clients()
    rate_limiter = RateLimiter(settings.rate_limit_interval)
    group_rate_limiter = GroupRateLimiter(2.0)
    bot_health = BotHealth()
    app.state.pool = AccountPool(clients, rate_limiter=rate_limiter)
    app.state.cache = ResultCache(settings.cache_ttl_hours)
    app.state.request_rate_limiter = RequestRateLimiter(settings.max_requests_per_minute)
    app.state.runtime_state = RuntimeState()
    app.state.bot_health = bot_health
    app.state.group_rate_limiter = group_rate_limiter
    app.state.bot_router = BotRouter(health_tracker=bot_health, group_rate_limiter=group_rate_limiter)
    app.state.clients = clients
    app.state.started_at = time.monotonic()

    try:
        yield
    finally:
        runtime_state = app.state.runtime_state
        logger.info(
            "Iniciando graceful shutdown.",
            extra={
                "event": "graceful_shutdown_started",
                "timeout_seconds": GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
            },
        )
        queries_completed = await runtime_state.wait_for_idle(GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS)
        if not queries_completed:
            await runtime_state.record_error(
                "shutdown_timeout",
                "Graceful shutdown expirou aguardando queries em andamento.",
                timeout_seconds=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
            )
            logger.warning(
                "Graceful shutdown expirou aguardando queries em andamento.",
                extra={
                    "event": "graceful_shutdown_timeout",
                    "timeout_seconds": GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
                },
            )

        disconnect_tasks = [client.disconnect() for client in clients.values()]
        try:
            await asyncio.wait_for(
                asyncio.gather(*disconnect_tasks, return_exceptions=True),
                timeout=GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "Timeout ao desconectar clients Telethon durante shutdown.",
                extra={
                    "event": "telethon_disconnect_timeout",
                    "timeout_seconds": GRACEFUL_SHUTDOWN_TIMEOUT_SECONDS,
                },
            )


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
    description=(
        "API REST para automatizar consultas via múltiplos bots Telegram, "
        "com balanceamento entre contas, cache, fallback e documentação OpenAPI."
    ),
    version="0.6.0",
    openapi_tags=[
        {"name": "consultas", "description": "Endpoints de consulta com fallback automático entre bots suportados."},
        {"name": "system", "description": "Endpoints de saúde, status e monitoramento da API."},
    ],
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
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
async def enforce_api_security(request: Request, call_next):
    request_id = uuid.uuid4().hex
    request.state.request_id = request_id

    with log_context(request_id=request_id, path=request.url.path, method=request.method):
        if not request.url.path.startswith("/api/") or request.url.path == "/api/health":
            response = await call_next(request)
            response.headers.setdefault("X-Request-ID", request_id)
            return response

        client_ip = _get_client_ip(request)
        allowed_api_keys = settings.allowed_api_keys
        if not allowed_api_keys:
            logger.error(
                "Nenhuma API key configurada para endpoints protegidos.",
                extra={
                    "event": "api_auth_not_configured",
                    "path": request.url.path,
                },
            )
            response = JSONResponse(
                status_code=503,
                content={"detail": "Autenticação não configurada no servidor."},
            )
            response.headers.setdefault("X-Request-ID", request_id)
            return response

        provided_api_key = request.headers.get("X-API-Key", "").strip()
        if provided_api_key not in allowed_api_keys:
            logger.warning(
                "Falha de autenticação por API key.",
                extra={
                    "event": "api_auth_failed",
                    "ip": client_ip,
                    "path": request.url.path,
                },
            )
            response = JSONResponse(
                status_code=401,
                content={"detail": "API key ausente ou inválida."},
            )
            response.headers.setdefault("X-Request-ID", request_id)
            return response

        limiter = getattr(request.app.state, "request_rate_limiter", None)
        if limiter is None:
            response = await call_next(request)
            response.headers.setdefault("X-Request-ID", request_id)
            return response

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
            response = JSONResponse(
                status_code=429,
                content={"detail": "Rate limit excedido. Tente novamente mais tarde."},
                headers={"Retry-After": str(retry_after)},
            )
            response.headers.setdefault("X-Request-ID", request_id)
            return response

        response = await call_next(request)
        response.headers.setdefault("X-Request-ID", request_id)
        return response


@app.get(
    "/api/health",
    tags=["system"],
    response_model=HealthResponse,
    summary="Health check da API",
    description="Retorna o estado básico do serviço sem exigir autenticação.",
)
async def health_check(request: Request) -> dict[str, float | int | str]:
    pool = get_pool(request)
    started_at = getattr(request.app.state, "started_at", time.monotonic())
    uptime = max(0.0, time.monotonic() - started_at)
    return {
        "status": "ok",
        "accounts": pool.size,
        "uptime": round(uptime, 3),
    }


@app.get(
    "/api/status",
    tags=["system"],
    response_model=StatusResponse,
    summary="Status detalhado da API",
    description="Retorna o estado das contas Telegram, tamanho do cache, uptime, contador diário de consultas e último erro conhecido.",
)
async def status_check(request: Request) -> dict[str, object]:
    started_at = getattr(request.app.state, "started_at", time.monotonic())
    uptime = max(0.0, time.monotonic() - started_at)
    cache = get_cache(request)
    runtime_state = get_runtime_state(request)
    runtime_snapshot = await runtime_state.snapshot()
    clients: dict[str, TelegramClient] = getattr(request.app.state, "clients", {})

    accounts = [
        {
            "label": label,
            "status": "connected" if client.is_connected() else "disconnected",
        }
        for label, client in clients.items()
    ]

    return {
        "accounts": accounts,
        "cache_size": cache.size,
        "uptime": round(uptime, 3),
        "queries_today": runtime_snapshot["queries_today"],
        "last_error": runtime_snapshot["last_error"],
    }


@app.get(
    "/api/status/bots",
    tags=["system"],
    summary="Status detalhado dos adapters",
    description="Retorna health, taxa de sucesso, tempo médio e estado do circuit breaker por adapter.",
)
async def bot_status_check(request: Request) -> dict[str, dict[str, object]]:
    bot_router = get_bot_router(request)
    return bot_router.get_bot_statuses()


@app.get(
    "/api/debug/chain/{tipo}",
    tags=["system"],
    summary="Diagnóstico da chain de fallback",
    description="Mostra a ordem de fallback para um tipo de consulta e o estado atual de cada adapter.",
)
async def debug_chain(
    request: Request,
    tipo: ConsultationType,
    base: str | None = None,
) -> dict[str, object]:
    bot_router = get_bot_router(request)
    return bot_router.describe_chain(tipo, base)


@app.get(
    "/api/debug/last-errors",
    tags=["system"],
    summary="Últimos erros registrados",
    description="Retorna os últimos 50 erros registrados em memória para diagnóstico operacional.",
)
async def debug_last_errors(request: Request) -> dict[str, object]:
    runtime_state = get_runtime_state(request)
    return {"errors": await runtime_state.last_errors(50)}


@app.delete(
    "/api/cache/{tipo}/{query_input}",
    tags=["system"],
    summary="Invalidar cache manualmente",
    description="Remove entradas de cache por tipo e input, opcionalmente filtrando pela base.",
)
async def delete_cache_entry(
    request: Request,
    tipo: ConsultationType,
    query_input: str,
    base: str | None = None,
) -> dict[str, object]:
    cache = get_cache(request)
    command = f"/{tipo} {query_input}".strip()
    deleted = cache.delete(command, base)
    return {
        "status": "ok",
        "tipo": tipo,
        "input": query_input,
        "base": base,
        "deleted": deleted,
    }


from app.routes.consultas import router as consultas_router


app.include_router(consultas_router)