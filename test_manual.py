import asyncio
import json

import httpx

from app.config import settings


async def main() -> None:
    payload = {
        "cpf": "12974572936",
        "base": "completo",
    }
    api_key = settings.primary_api_key
    headers = {}
    if api_key:
        headers["X-API-Key"] = api_key

    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            "http://localhost:8000/api/consulta/cpf",
            json=payload,
            headers=headers,
        )

    print(f"status_code={response.status_code}")
    print(json.dumps(response.json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())