import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.main import get_cache, get_pool
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
from app.services.account_pool import AccountPool
from app.services.cache import ResultCache
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


async def _execute_consulta(
    pool: AccountPool,
    cache: ResultCache,
    tipo: ConsultationType,
    query_input: str,
    base: str | None = None,
) -> JSONResponse:
    command = build_command(tipo, query_input)
    base_button_text = resolve_base_button_text(tipo, base)
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
        return JSONResponse(content=cached_payload, headers={"X-Cache": "HIT"})

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
            return JSONResponse(content=payload, headers={"X-Cache": "MISS"})
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


@router.post("/cpf")
async def consulta_cpf(
    payload: ConsultaCPFRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="cpf", query_input=payload.query_input, base=payload.base)


@router.post("/nome")
async def consulta_nome(
    payload: ConsultaNomeRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="nome", query_input=payload.query_input, base=payload.base)


@router.post("/telefone")
async def consulta_telefone(
    payload: ConsultaTelefoneRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="telefone", query_input=payload.query_input, base=payload.base)


@router.post("/cep")
async def consulta_cep(
    payload: ConsultaCEPRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="cep", query_input=payload.query_input)


@router.post("/email")
async def consulta_email(
    payload: ConsultaEmailRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="email", query_input=payload.query_input, base=payload.base)


@router.post("/ip")
async def consulta_ip(
    payload: ConsultaIPRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="ip", query_input=payload.query_input)


@router.post("/titulo")
async def consulta_titulo(
    payload: ConsultaTituloRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="titulo", query_input=payload.query_input, base="titulo")


@router.post("/pix")
async def consulta_pix(
    payload: ConsultaPIXRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    return await _execute_consulta(cache=cache, pool=pool, tipo="pix", query_input=payload.query_input, base="pix")


@router.post("")
async def consulta_generica(
    payload: ConsultaGenericaRequest,
    pool: AccountPool = Depends(get_pool),
    cache: ResultCache = Depends(get_cache),
):
    tipo, query_input, base = _build_generic_execution_args(payload)
    return await _execute_consulta(cache=cache, pool=pool, tipo=tipo, query_input=query_input, base=base)