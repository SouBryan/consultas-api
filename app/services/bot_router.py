from typing import Any

from telethon import TelegramClient

from app.services.adapters import (
    AllBotsFailedError,
    BlackConsultasAdapter,
    BotAdapter,
    BotResponseError,
    DataFlowAdapter,
    WorkBotAdapter,
)
from app.services.bot_health import BotHealth
from app.utils.logger import get_logger


FALLBACK_CHAINS = {
    "cpf": ["dataflow", "work_bot", "black_consultas"],
    "nome": ["dataflow", "work_bot", "black_consultas"],
    "telefone": ["dataflow", "work_bot", "black_consultas"],
    "email": ["dataflow", "work_bot", "black_consultas"],
    "cep": ["dataflow", "work_bot", "black_consultas"],
    "cnpj": ["dataflow", "work_bot"],
    "titulo": ["dataflow", "work_bot"],
    "bin": ["dataflow"],
    "endereco": ["dataflow"],
    "mae": ["work_bot", "dataflow"],
    "foto": ["work_bot", "dataflow"],
    "ip": ["black_consultas"],
    "rg": ["work_bot"],
    "pai": ["work_bot"],
    "placa": ["work_bot"],
    "proprietario": ["work_bot"],
    "cns": ["work_bot"],
    "chave": ["work_bot"],
    "vizinhos": ["work_bot"],
    "parentes": ["work_bot"],
    "pep": ["work_bot"],
    "condutor": ["work_bot"],
    "frota": ["work_bot"],
    "processo_numero": ["work_bot"],
    "pix": ["black_consultas"],
}


class BotRouter:
    def __init__(
        self,
        adapters: dict[str, BotAdapter] | None = None,
        health_tracker: BotHealth | None = None,
    ):
        self._adapters = adapters or {
            "dataflow": DataFlowAdapter(),
            "work_bot": WorkBotAdapter(),
            "black_consultas": BlackConsultasAdapter(),
        }
        self._health_tracker = health_tracker or BotHealth()
        self._logger = get_logger("services.bot_router")

    async def route_query(
        self,
        client: TelegramClient,
        tipo: str,
        input_data: str,
        base: str | None = None,
    ) -> dict[str, Any]:
        chain = FALLBACK_CHAINS.get(tipo)
        if not chain:
            raise ValueError(f"Tipo de consulta '{tipo}' não suportado.")

        failures: list[dict[str, Any]] = []
        preferred_exception: Exception | None = None

        for adapter_name in chain:
            adapter = self._adapters.get(adapter_name)
            if adapter is None:
                continue

            if not adapter.supports(tipo, base):
                self._logger.info(
                    "Adapter ignorado por incompatibilidade de base.",
                    extra={
                        "event": "bot_router_adapter_skipped",
                        "adapter": adapter_name,
                        "tipo": tipo,
                        "base": base,
                    },
                )
                continue

            if not self._health_tracker.is_healthy(adapter_name):
                self._logger.info(
                    "Adapter em cooldown, fallback para o próximo bot.",
                    extra={
                        "event": "bot_router_adapter_cooldown",
                        "adapter": adapter_name,
                        "tipo": tipo,
                    },
                )
                continue

            try:
                result = await adapter.execute(client, tipo, input_data, base)
                self._health_tracker.mark_healthy(adapter_name)
                result.setdefault("adapter", adapter_name)
                result.setdefault("link", "")
                result.setdefault("data", {})
                return result
            except TimeoutError as exc:
                self._health_tracker.mark_unhealthy(adapter_name, cooldown_seconds=60, reason="timeout")
                failures.append(self._build_failure(adapter_name, exc))
                preferred_exception = self._choose_preferred_exception(preferred_exception, exc)
                self._logger.warning(
                    "Adapter falhou por timeout.",
                    extra={
                        "event": "bot_router_timeout",
                        "adapter": adapter_name,
                        "tipo": tipo,
                        "base": base,
                    },
                )
            except BotResponseError as exc:
                if exc.error_code == "maintenance":
                    self._health_tracker.mark_unhealthy(
                        adapter_name,
                        cooldown_seconds=300,
                        reason="maintenance",
                    )

                failures.append(self._build_failure(adapter_name, exc))
                preferred_exception = self._choose_preferred_exception(preferred_exception, exc)
                self._logger.warning(
                    "Adapter retornou erro de negócio.",
                    extra={
                        "event": "bot_router_business_error",
                        "adapter": adapter_name,
                        "tipo": tipo,
                        "base": base,
                        "error_code": exc.error_code,
                        "status_code": exc.status_code,
                    },
                )
            except Exception as exc:
                failures.append(self._build_failure(adapter_name, exc))
                preferred_exception = self._choose_preferred_exception(preferred_exception, exc)
                self._logger.warning(
                    "Adapter falhou e o router seguirá para o próximo fallback.",
                    extra={
                        "event": "bot_router_adapter_failed",
                        "adapter": adapter_name,
                        "tipo": tipo,
                        "base": base,
                    },
                )

        raise AllBotsFailedError(tipo, failures, preferred_exception=preferred_exception)

    def _build_failure(self, adapter_name: str, exc: Exception) -> dict[str, Any]:
        error_type = type(exc).__name__
        if isinstance(exc, BotResponseError):
            error_type = exc.error_code

        return {
            "adapter": adapter_name,
            "error_type": error_type,
            "message": str(exc),
        }

    def _choose_preferred_exception(
        self,
        current: Exception | None,
        candidate: Exception,
    ) -> Exception:
        if current is None:
            return candidate

        if self._exception_priority(candidate) >= self._exception_priority(current):
            return candidate

        return current

    def _exception_priority(self, exc: Exception) -> int:
        if isinstance(exc, BotResponseError):
            return {
                "invalid": 50,
                "subscription": 40,
                "not_found": 30,
                "maintenance": 20,
            }.get(exc.error_code, 20)
        if isinstance(exc, ValueError):
            return 15
        if isinstance(exc, TimeoutError):
            return 10
        return 0