import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import ValidationError

from app.main import get_cache, get_pool, get_runtime_state
from app.models.requests import (
    ConsultationType,
    ConsultaCEPRequest,
    ConsultaCPFRequest,
    ConsultaEmailRequest,
    ConsultaGenericaRequest,
    ConsultaIPRequest,
    ConsultaNomeRequest,
    ConsultaPIXRequest,
    ConsultaTelefoneRequest,
    ConsultaTituloRequest,
)
from app.models.responses import ConsultaResponse, ErrorResponse
from app.services.account_pool import AccountPool
from app.services.cache import ResultCache
from app.services.runtime_state import RuntimeState
from app.services.scraper import scrape_result
from app.services.telegram_worker import (
    BotResponseError,
    build_command,
    execute_query,
    resolve_base_button_text,
)
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
    503: {"model": ErrorResponse, "description": "Módulo em manutenção ou indisponível no bot."},
    504: {"model": ErrorResponse, "description": "Timeout ao consultar o bot em todas as contas disponíveis."},
}


async def _execute_consulta(
    response: Response,
    pool: AccountPool,
    cache: ResultCache,
    runtime_state: RuntimeState,
    tipo: ConsultationType,
    query_input: str,
    base: str | None = None,
) -> dict[str, Any]:
    command = build_command(tipo, query_input)
    base_button_text = resolve_base_button_text(tipo, base)
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
                result_url = await execute_query(client, command, base_button_text)
                data = await scrape_result(result_url)
                payload = {
                    "status": "success",
                    "link": result_url,
                    "data": data,
                }
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
                        "attempt": attempt,
                        "retry_used": attempt > 1,
                        "timeout_failures": timeout_failures,
                        "duration_ms": round((time.monotonic() - started_at) * 1000, 2),
                        "cache": "MISS",
                    },
                )
                return payload
            except TimeoutError as exc:
                tried_labels.add(label)
                timeout_failures.append(
                    {
                        "account": label,
                        "message": str(exc),
                        "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                    }
                )
                logger.warning(
                    "Timeout ao consultar bot.",
                    extra={
                        "event": "consulta_timeout",
                        "tipo": tipo,
                        "input": query_input,
                        "base": base,
                        "account": label,
                        "attempt": attempt,
                        "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                    },
                )
            except BotResponseError as exc:
                await runtime_state.record_error(
                    exc.error_code,
                    str(exc),
                    tipo=tipo,
                    input=query_input,
                    base=base,
                    account=label,
                    attempt=attempt,
                )
                logger.warning(
                    "Bot retornou erro de negócio.",
                    extra={
                        "event": "bot_error",
                        "tipo": tipo,
                        "input": query_input,
                        "base": base,
                        "account": label,
                        "error_code": exc.error_code,
                        "status_code": exc.status_code,
                        "duration_ms": round((time.monotonic() - attempt_started_at) * 1000, 2),
                    },
                )
                raise HTTPException(
                    status_code=exc.status_code,
                    detail=str(exc),
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
    description="Executa a consulta de CPF no bot Black Consultas usando uma das sub-bases gratuitas disponíveis.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_cpf(
    response: Response,
    payload: ConsultaCPFRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="cpf", query_input=payload.query_input, base=payload.base)


@router.post(
    "/nome",
    response_model=ConsultaResponse,
    summary="Consultar nome completo",
    description="Envia um nome completo ao bot e seleciona a base NOME ou NOME DA MÃE para obter o resultado.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_nome(
    response: Response,
    payload: ConsultaNomeRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="nome", query_input=payload.query_input, base=payload.base)


@router.post(
    "/telefone",
    response_model=ConsultaResponse,
    summary="Consultar telefone",
    description="Consulta um telefone com DDD na base TELEFONE do bot e retorna os dados estruturados em JSON.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_telefone(
    response: Response,
    payload: ConsultaTelefoneRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="telefone", query_input=payload.query_input, base=payload.base)


@router.post(
    "/cep",
    response_model=ConsultaResponse,
    summary="Consultar CEP",
    description="Consulta um CEP diretamente no bot. O link de resultado é retornado sem exigir clique em botão de sub-base.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_cep(
    response: Response,
    payload: ConsultaCEPRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="cep", query_input=payload.query_input)


@router.post(
    "/email",
    response_model=ConsultaResponse,
    summary="Consultar e-mail",
    description="Consulta um endereço de e-mail na base EMAIL do bot e retorna o resultado parseado.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_email(
    response: Response,
    payload: ConsultaEmailRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="email", query_input=payload.query_input, base=payload.base)


@router.post(
    "/ip",
    response_model=ConsultaResponse,
    summary="Consultar IP",
    description="Consulta um endereço IPv4 diretamente no bot e devolve os dados estruturados do resultado.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_ip(
    response: Response,
    payload: ConsultaIPRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="ip", query_input=payload.query_input)


@router.post(
    "/titulo",
    response_model=ConsultaResponse,
    summary="Consultar título de eleitor",
    description="Consulta um título de eleitor no bot e seleciona a base TÍTULO DE ELEITOR.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_titulo(
    response: Response,
    payload: ConsultaTituloRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="titulo", query_input=payload.query_input, base="titulo")


@router.post(
    "/pix",
    response_model=ConsultaResponse,
    summary="Consultar PIX",
    description="Consulta PIX usando o formato nome completo|meio_cpf e seleciona a base PIX por padrão.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_pix(
    response: Response,
    payload: ConsultaPIXRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo="pix", query_input=payload.query_input, base="pix")


@router.post(
    "",
    response_model=ConsultaResponse,
    summary="Consultar via endpoint genérico",
    description="Recebe o tipo, o input e a base em um único payload e roteia internamente para o comando correto do bot.",
    responses=COMMON_ERROR_RESPONSES,
)
async def consulta_generica(
    response: Response,
    payload: ConsultaGenericaRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
    runtime_state: RuntimeState = Depends(get_runtime_state),
):
    tipo, query_input, base = _build_generic_execution_args(payload)
    return await _execute_consulta(response=response, cache=cache, pool=pool, runtime_state=runtime_state, tipo=tipo, query_input=query_input, base=base)