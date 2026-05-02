from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ErrorResponse(BaseModel):
    detail: Any = Field(description="Detalhe do erro retornado pela API.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "detail": "Base requer assinatura"
            }
        }
    )


class ConsultaResponse(BaseModel):
    status: Literal["success"] = Field(description="Status fixo de sucesso da consulta.")
    link: str = Field(description="Link público do resultado retornado pelo bot.")
    data: dict[str, Any] = Field(default_factory=dict, description="Dados estruturados extraídos do resultado.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "success",
                "link": "https://blackconsultas.com/result-consultation/abc123?bot=",
                "data": {
                    "dados_basicos": {
                        "nome": "LORENZO CRISTIANINI DE OLIVEIRA",
                        "cpf": "12974572936",
                        "situacao_cadastral": "REGULAR"
                    },
                    "enderecos": [
                        {
                            "logradouro": "PENHA, 127",
                            "bairro": "ATLANTICO II",
                            "cidade_uf": "CIANORTE/PR",
                            "cep": "87202040"
                        }
                    ]
                }
            }
        }
    )


class HealthResponse(BaseModel):
    status: Literal["ok"] = Field(description="Status do serviço.")
    accounts: int = Field(description="Quantidade de contas Telegram configuradas.")
    uptime: float = Field(description="Tempo de atividade da API em segundos.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "status": "ok",
                "accounts": 2,
                "uptime": 125.381
            }
        }
    )


class AccountStatusResponse(BaseModel):
    label: str = Field(description="Rótulo configurado para a conta Telegram.")
    status: Literal["connected", "disconnected"] = Field(description="Estado atual da conexão Telethon.")


class LastErrorResponse(BaseModel):
    timestamp: str = Field(description="Timestamp ISO-8601 do último erro registrado.")
    type: str = Field(description="Categoria do erro registrado.")
    message: str = Field(description="Mensagem resumida do erro.")
    context: dict[str, Any] = Field(default_factory=dict, description="Contexto adicional do erro.")


class StatusResponse(BaseModel):
    accounts: list[AccountStatusResponse] = Field(description="Lista de contas Telegram e seus estados atuais.")
    cache_size: int = Field(description="Quantidade de entradas válidas atualmente no cache.")
    uptime: float = Field(description="Tempo de atividade da API em segundos.")
    queries_today: int = Field(description="Contador de consultas recebidas no dia corrente.")
    last_error: LastErrorResponse | None = Field(default=None, description="Último erro conhecido registrado pela API.")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "accounts": [
                    {"label": "bryan", "status": "connected"},
                    {"label": "bryan2", "status": "connected"}
                ],
                "cache_size": 3,
                "uptime": 532.204,
                "queries_today": 27,
                "last_error": {
                    "timestamp": "2026-05-02T18:00:00+00:00",
                    "type": "subscription",
                    "message": "Base requer assinatura",
                    "context": {
                        "tipo": "pix",
                        "account": "bryan"
                    }
                }
            }
        }
    )