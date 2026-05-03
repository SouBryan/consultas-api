import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from app.main import get_bot_router, get_cache, get_pool, get_runtime_state
from app.models.requests import (
    ConsultationType,
    ConsultaBINRequest,
    ConsultaCEPRequest,
    ConsultaCNPJRequest,
    ConsultaCPFRequest,
    ConsultaEmailRequest,
    ConsultaEnderecoRequest,
    ConsultaFotoRequest,
    ConsultaGenericaRequest,
    ConsultaIPRequest,
    ConsultaMaeRequest,
    ConsultaNomeRequest,
    ConsultaPIXRequest,
    ConsultaTelefoneRequest,
    ConsultaTituloRequest,
)
from app.models.responses import ConsultaResponse, ErrorResponse
from app.services.account_pool import AccountPool
from app.services.adapters import AllBotsFailedError, BotResponseError
from app.services.bot_router import BotRouter
from app.services.cache import ResultCache
from app.services.runtime_state import RuntimeState
from app.utils.logger import get_logger


router = APIRouter(prefix="/api/consulta", tags=["consultas"])
logger = get_logger("routes.consultas")

COMMON_ERROR_RESPONSES = {
    401: {"model": ErrorResponse, "description": "API key ausente ou inválida."},
    403: {"model": ErrorResponse, "description": "A base consultada exige assinatura ativa."},
    404: {"model": ErrorResponse, "description": "O bot não encontrou resultados para a consulta."},
    422: {"model": ErrorResponse, "description": "Entrada inválida ou base incompatível com o tipo informado."},
    429: {"model": ErrorResponse, "description": "Rate limit da API excedido para o IP do cliente."},
    500: {"model": ErrorResponse, "description": "Erro interno inesperado durante o processamento."},
    503: {"model": ErrorResponse, "description": "Todos os bots disponíveis falharam ou o módulo está indisponível."},
    504: {"model": ErrorResponse, "description": "Timeout ao consultar o bot em todas as contas disponíveis."},
}


def _build_cache_command(tipo: ConsultationType, query_input: str) -> str:
    return f"/{tipo} {query_input}".strip()


def _build_success_payload(adapter_result: dict[str, Any]) -> dict[str, Any]:
    payload_data = adapter_result.get("data", {})
    if not isinstance(payload_data, dict):
        payload_data = {"raw": payload_data}

    link = adapter_result.get("link") or ""
    return {
        "status": "success",
        "link": str(link),
        "data": payload_data,
    }


async def _execute_consulta(
    response: Response,
    pool: AccountPool,
    cache: ResultCache,
    runtime_state: RuntimeState,
    bot_router: BotRouter,
    tipo: ConsultationType,
    query_input: str,
    base: str | None = None,
) -> dict[str, Any]:
    command = _build_cache_command(tipo, query_input)
    await runtime_state.start_query()

    try:
        cached_payload = cache.get(command, base)

        if cached_payload is not None:
            logger.info(
                "Consulta servida do cache.",
                extra={
                    "event": "consulta_cache_hit",
                    "tipo": tipo,
                    "input": query_input,
                    "base": base,
                },
            )
            response.headers["X-Cache"] = "HIT"
            return cached_payload

        started_at = time.monotonic()
        tried_labels: set[str] = set()
        timeout_failures: list[dict[str, Any]] = []
        max_attempts = min(2, pool.size)

        for attempt in range(1, max_attempts + 1):
            label, client = await pool.acquire(exclude_labels=tried_labels)
            attempt_started_at = time.monotonic()

            try:
                adapter_result = await bot_router.route_query(client, tipo, query_input, base)
                payload = _build_success_payload(adapter_result)
                cache.set(command, base, payload)
                response.headers["X-Cache"] = "MISS"

                logger.info(
                    "Consulta concluída com sucesso.",
                    extra={
                        "event": "consulta_success",
                        "tipo": tipo,
                        "input": query_input,
                        "base": base,
                        "account": label,
                        "adapter": adapter_result.get("adapter"),
                        "attempt": attempt,
                        "retry_used": attempt > 1,
                        "timeout_failures": timeout_failures,
                        "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
                        "cache": "MISS",
                    },
                )
                return payload
            except AllBotsFailedError as exc:
                preferred_exception = exc.preferred_exception

                if isinstance(preferred_exception, TimeoutError):
                    tried_labels.add(label)
                    timeout_failures.append(
                        {
                            "account": label,
                            "message": str(preferred_exception),
                            "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                            "bot_failures": exc.failures,
                        }
                    )
                    logger.warning(
                        "Timeout ao consultar chain de bots.",
                        extra={
                            "event": "consulta_timeout",
                            "tipo": tipo,
                            "input": query_input,
                            "base": base,
                            "account": label,
                            "attempt": attempt,
                            "failures": exc.failures,
                            "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                        },
                    )
                    continue

                if isinstance(preferred_exception, BotResponseError):
                    await runtime_state.record_error(
                        preferred_exception.error_code,
                        str(preferred_exception),
                        tipo=tipo,
                        input=query_input,
                        base=base,
                        account=label,
                        attempt=attempt,
                        failures=exc.failures,
                    )
                    logger.warning(
                        "Todos os bots do chain falharam com erro de negócio.",
                        extra={
                            "event": "bot_chain_error",
                            "tipo": tipo,
                            "input": query_input,
                            "base": base,
                            "account": label,
                            "error_code": preferred_exception.error_code,
                            "status_code": preferred_exception.status_code,
                            "failures": exc.failures,
                            "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                        },
                    )
                    raise HTTPException(
                        status_code=preferred_exception.status_code,
                        detail=str(preferred_exception),
                        headers={"X-Cache": "MISS"},
                    ) from preferred_exception

                if isinstance(preferred_exception, ValueError):
                    await runtime_state.record_error(
                        "validation",
                        str(preferred_exception),
                        tipo=tipo,
                        input=query_input,
                        base=base,
                        account=label,
                        attempt=attempt,
                        failures=exc.failures,
                    )
                    logger.warning(
                        "Todos os bots do chain falharam por validação.",
                        extra={
                            "event": "consulta_validation_error",
                            "tipo": tipo,
                            "input": query_input,
                            "base": base,
                            "account": label,
                            "failures": exc.failures,
                            "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                        },
                    )
                    raise HTTPException(
                        status_code=422,
                        detail=str(preferred_exception),
                        headers={"X-Cache": "MISS"},
                    ) from preferred_exception

                await runtime_state.record_error(
                    "bots_failed",
                    str(exc),
                    tipo=tipo,
                    input=query_input,
                    base=base,
                    account=label,
                    attempt=attempt,
                    failures=exc.failures,
                )
                logger.error(
                    "Nenhum bot do chain conseguiu atender a consulta.",
                    extra={
                        "event": "consulta_all_bots_failed",
                        "tipo": tipo,
                        "input": query_input,
                        "base": base,
                        "account": label,
                        "failures": exc.failures,
                        "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                    },
                )
                raise HTTPException(
                    status_code=503,
                    detail="Nenhum bot disponível conseguiu responder à consulta.",
                    headers={"X-Cache": "MISS"},
                ) from exc
            except ValueError as exc:
                await runtime_state.record_error(
                    "validation",
                    str(exc),
                    tipo=tipo,
                    input=query_input,
                    base=base,
                    account=label,
                    attempt=attempt,
                )
                logger.warning(
                    "Erro de validação durante a consulta.",
                    extra={
                        "event": "consulta_validation_error",
                        "tipo": tipo,
                        "input": query_input,
                        "base": base,
                        "account": label,
                        "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                    },
                )
                raise HTTPException(
                    status_code=422,
                    detail=str(exc),
                    headers={"X-Cache": "MISS"},
                ) from exc
            except Exception as exc:
                await runtime_state.record_error(
                    "system",
                    "Erro interno ao processar consulta.",
                    tipo=tipo,
                    input=query_input,
                    base=base,
                    account=label,
                    attempt=attempt,
                )
                logger.error(
                    "Falha inesperada ao processar consulta.",
                    extra={
                        "event": "consulta_system_error",
                        "tipo": tipo,
                        "input": query_input,
                        "base": base,
                        "account": label,
                        "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                    },
                    exc_info=exc,
                )
                raise HTTPException(
                    status_code=500,
                    detail="Erro interno ao processar consulta.",
                    headers={"X-Cache": "MISS"},
                ) from exc
            finally:
                pool.release(label)

        await runtime_state.record_error(
            "timeout",
            "Bot não respondeu em nenhuma das contas disponíveis.",
            tipo=tipo,
            input=query_input,
            base=base,
            timeouts=timeout_failures,
        )
        logger.error(
            "Consulta falhou após esgotar retry por timeout.",
            extra={
                "event": "consulta_retry_exhausted",
                "tipo": tipo,
                "input": query_input,
                "base": base,
                "timeouts": timeout_failures,
                "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
            },
        )
        raise HTTPException(
            status_code=504,
            detail="Bot não respondeu em nenhuma das contas disponíveis.",
            headers={"X-Cache": "MISS"},
        )
    finally:
        await runtime_state.finish_query()


def _build_generic_execution_args(
    payload: ConsultaGenericaRequest,
) -> tuple[ConsultationType, str, str | None]:
    try:
        if payload.tipo == "cpf":
            specific = ConsultaCPFRequest.model_validate(
                {"cpf": payload.input, "base": payload.base or "completo"}
            )
            return payload.tipo, specific.query_input, specific.base

        if payload.tipo == "nome":
            specific = ConsultaNomeRequest.model_validate(
                {"nome": payload.input, "base": payload.base or "nome"}
            )
            return payload.tipo, specific.query_input, specific.base

        if payload.tipo == "telefone":
            specific = ConsultaTelefoneRequest.model_validate(
                {"telefone": payload.input, "base": payload.base or "telefone"}
            )
            return payload.tipo, specific.query_input, specific.base

        if payload.tipo == "cep":
            if payload.base is not None:
                raise ValueError("Tipo de consulta 'cep' não aceita base.")
            specific = ConsultaCEPRequest.model_validate({"cep": payload.input})
            return payload.tipo, specific.query_input, None

        if payload.tipo == "email":
            specific = ConsultaEmailRequest.model_validate(
                {"email": payload.input, "base": payload.base or "email"}
            )
            return payload.tipo, specific.query_input, specific.base

        if payload.tipo == "cnpj":
            if payload.base is not None:
                raise ValueError("Tipo de consulta 'cnpj' não aceita base.")
            specific = ConsultaCNPJRequest.model_validate({"cnpj": payload.input})
            return payload.tipo, specific.query_input, None

        if payload.tipo == "bin":
            if payload.base is not None:
                raise ValueError("Tipo de consulta 'bin' não aceita base.")
            specific = ConsultaBINRequest.model_validate({"bin": payload.input})
            return payload.tipo, specific.query_input, None

        if payload.tipo == "endereco":
            if payload.base is not None:
                raise ValueError("Tipo de consulta 'endereco' não aceita base.")
            specific = ConsultaEnderecoRequest.model_validate({"cpf": payload.input})
            return payload.tipo, specific.query_input, None

        if payload.tipo == "mae":
            if payload.base is not None:
                raise ValueError("Tipo de consulta 'mae' não aceita base.")
            specific = ConsultaMaeRequest.model_validate({"nome": payload.input})
            return payload.tipo, specific.query_input, None

        if payload.tipo == "foto":
            if payload.base is not None:
                raise ValueError("Tipo de consulta 'foto' não aceita base.")
            specific = ConsultaFotoRequest.model_validate({"cpf": payload.input})
            return payload.tipo, specific.query_input, None

        if payload.tipo == "ip":
            if payload.base is not None:
                raise ValueError("Tipo de consulta 'ip' não aceita base.")
            specific = ConsultaIPRequest.model_validate({"ip": payload.input})
            return payload.tipo, specific.query_input, None

        if payload.tipo == "titulo":
            normalized_base = payload.base or "titulo"
            if normalized_base != "titulo":
                raise ValueError("Tipo de consulta 'titulo' só aceita a base 'titulo'.")
            specific = ConsultaTituloRequest.model_validate({"titulo": payload.input})
            return payload.tipo, specific.query_input, normalized_base

        if payload.tipo == "pix":
            normalized_base = payload.base or "pix"
            if normalized_base not in {"pix", "pix2"}:
                raise ValueError("Tipo de consulta 'pix' só aceita as bases 'pix' ou 'pix2'.")
            nome, separator, meio_cpf = payload.input.partition("|")
            if not separator:
                raise ValueError("Input de PIX deve seguir o formato 'nome completo|123456'.")
            specific = ConsultaPIXRequest.model_validate(
                {"nome": nome, "meio_cpf": meio_cpf}
            )
            return payload.tipo, specific.query_input, normalized_base
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    raise HTTPException(status_code=422, detail="Tipo de consulta não suportado.")


@router.post(
    "/cpf",
    response_model=ConsultaResponse,
    summary="Consultar CPF",
    description="Executa a consulta de CPF usando fallback automático entre os bots suportados para esse tipo.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_cpf(
    response: Response,
    payload: ConsultaCPFRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="cpf",
        query_input=payload.query_input,
        base=payload.base,
    )


@router.post(
    "/nome",
    response_model=ConsultaResponse,
    summary="Consultar nome completo",
    description="Consulta um nome completo usando fallback entre bots compatíveis.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_nome(
    response: Response,
    payload: ConsultaNomeRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="nome",
        query_input=payload.query_input,
        base=payload.base,
    )


@router.post(
    "/telefone",
    response_model=ConsultaResponse,
    summary="Consultar telefone",
    description="Consulta um telefone com DDD usando fallback entre os bots suportados.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_telefone(
    response: Response,
    payload: ConsultaTelefoneRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="telefone",
        query_input=payload.query_input,
        base=payload.base,
    )


@router.post(
    "/cep",
    response_model=ConsultaResponse,
    summary="Consultar CEP",
    description="Consulta um CEP com fallback entre adapters compatíveis.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_cep(
    response: Response,
    payload: ConsultaCEPRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="cep",
        query_input=payload.query_input,
    )


@router.post(
    "/email",
    response_model=ConsultaResponse,
    summary="Consultar e-mail",
    description="Consulta um endereço de e-mail usando fallback entre os bots suportados.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_email(
    response: Response,
    payload: ConsultaEmailRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="email",
        query_input=payload.query_input,
        base=payload.base,
    )


@router.post(
    "/cnpj",
    response_model=ConsultaResponse,
    summary="Consultar CNPJ",
    description="Consulta CNPJ usando o DataFlow nesta primeira fase da arquitetura multi-bot.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_cnpj(
    response: Response,
    payload: ConsultaCNPJRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="cnpj",
        query_input=payload.query_input,
    )


@router.post(
    "/bin",
    response_model=ConsultaResponse,
    summary="Consultar BIN",
    description="Consulta BIN diretamente pelo DataFlow.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_bin(
    response: Response,
    payload: ConsultaBINRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="bin",
        query_input=payload.query_input,
    )


@router.post(
    "/endereco",
    response_model=ConsultaResponse,
    summary="Consultar endereço",
    description="Consulta endereço via DataFlow usando CPF como input nesta fase inicial.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_endereco(
    response: Response,
    payload: ConsultaEnderecoRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="endereco",
        query_input=payload.query_input,
    )


@router.post(
    "/mae",
    response_model=ConsultaResponse,
    summary="Consultar nome da mãe",
    description="Consulta nome da mãe usando o DataFlow nesta fase.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_mae(
    response: Response,
    payload: ConsultaMaeRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="mae",
        query_input=payload.query_input,
    )


@router.post(
    "/foto",
    response_model=ConsultaResponse,
    summary="Consultar foto",
    description="Consulta foto pelo DataFlow. Quando o bot retornar mídia diretamente, o payload indicará isso em `data`.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_foto(
    response: Response,
    payload: ConsultaFotoRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="foto",
        query_input=payload.query_input,
    )


@router.post(
    "/ip",
    response_model=ConsultaResponse,
    summary="Consultar IP",
    description="Consulta um endereço IPv4. Nesta fase, o fallback usa apenas o adapter do Black Consultas.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_ip(
    response: Response,
    payload: ConsultaIPRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="ip",
        query_input=payload.query_input,
    )


@router.post(
    "/titulo",
    response_model=ConsultaResponse,
    summary="Consultar título de eleitor",
    description="Consulta um título de eleitor com fallback automático entre os bots compatíveis.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_titulo(
    response: Response,
    payload: ConsultaTituloRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="titulo",
        query_input=payload.query_input,
        base="titulo",
    )


@router.post(
    "/pix",
    response_model=ConsultaResponse,
    summary="Consultar PIX",
    description="Consulta PIX usando o Black Consultas nesta fase da arquitetura multi-bot.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_pix(
    response: Response,
    payload: ConsultaPIXRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo="pix",
        query_input=payload.query_input,
        base="pix",
    )


@router.post(
    "",
    response_model=ConsultaResponse,
    summary="Consultar via endpoint genérico",
    description="Recebe o tipo, o input e a base em um único payload e aplica o fallback configurado para o tipo solicitado.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_generica(
    response: Response,
    payload: ConsultaGenericaRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
    bot_router: BotRouter = Depends(get_bot_router),
):
    tipo, query_input, base = _build_generic_execution_args(payload)
    return await _execute_consulta(
        response=response,
        cache=cache,
        pool=pool,
        runtime_state=runtime_state,
        bot_router=bot_router,
        tipo=tipo,
        query_input=query_input,
        base=base,
    )