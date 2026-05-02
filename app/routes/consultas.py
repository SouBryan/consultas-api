from fastapi import APIRouter, Depends, HTTPException

from app.main import get_pool
from app.models.requests import ConsultaCPFRequest
from app.services.account_pool import AccountPool
from app.services.scraper import scrape_result
from app.services.telegram_worker import execute_query


router = APIRouter(prefix="/api/consulta", tags=["consultas"])


@router.post("/cpf")
async def consulta_cpf(
    payload: ConsultaCPFRequest,
    pool: AccountPool = Depends(get_pool),
):
    label, client = await pool.acquire()

    try:
        command = f"/cpf {payload.cpf}"
        result_url = await execute_query(client, command, payload.base_button_text)
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