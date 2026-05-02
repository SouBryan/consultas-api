from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from app.main import get_pool
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
from app.services.scraper import scrape_result
from app.services.telegram_worker import build_command, execute_query, resolve_base_button_text


router = APIRouter(prefix="/api/consulta", tags=["consultas"])


async def _execute_consulta(
    pool: AccountPool,
    tipo: ConsultationType,
    query_input: str,
    base: str | None = None,
) -> dict[str, Any]:
    label, client = await pool.acquire()

    try:
        command = build_command(tipo, query_input)
        base_button_text = resolve_base_button_text(tipo, base)
        result_url = await execute_query(client, command, base_button_text)
        data = await scrape_result(result_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    finally:
        pool.release(label)

    return {
        "status": "success",
        "link": result_url,
        "data": data,
    }


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
):
    return await _execute_consulta(pool, "cpf", payload.query_input, payload.base)


@router.post("/nome")
async def consulta_nome(
    payload: ConsultaNomeRequest,
    pool: AccountPool = Depends(get_pool),
):
    return await _execute_consulta(pool, "nome", payload.query_input, payload.base)


@router.post("/telefone")
async def consulta_telefone(
    payload: ConsultaTelefoneRequest,
    pool: AccountPool = Depends(get_pool),
):
    return await _execute_consulta(pool, "telefone", payload.query_input, payload.base)


@router.post("/cep")
async def consulta_cep(
    payload: ConsultaCEPRequest,
    pool: AccountPool = Depends(get_pool),
):
    return await _execute_consulta(pool, "cep", payload.query_input)


@router.post("/email")
async def consulta_email(
    payload: ConsultaEmailRequest,
    pool: AccountPool = Depends(get_pool),
):
    return await _execute_consulta(pool, "email", payload.query_input, payload.base)


@router.post("/ip")
async def consulta_ip(
    payload: ConsultaIPRequest,
    pool: AccountPool = Depends(get_pool),
):
    return await _execute_consulta(pool, "ip", payload.query_input)


@router.post("/titulo")
async def consulta_titulo(
    payload: ConsultaTituloRequest,
    pool: AccountPool = Depends(get_pool),
):
    return await _execute_consulta(pool, "titulo", payload.query_input, "titulo")


@router.post("/pix")
async def consulta_pix(
    payload: ConsultaPIXRequest,
    pool: AccountPool = Depends(get_pool),
):
    return await _execute_consulta(pool, "pix", payload.query_input, "pix")


@router.post("")
async def consulta_generica(
    payload: ConsultaGenericaRequest,
    pool: AccountPool = Depends(get_pool),
):
    tipo, query_input, base = _build_generic_execution_args(payload)
    return await _execute_consulta(pool, tipo, query_input, base)