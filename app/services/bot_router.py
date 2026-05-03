from threading import Lock
from typing import Any

from telethon import TelegramClient

from app.services.adapters import (
    AllBotsFailedError,
    BlackConsultasAdapter,
    BotAdapter,
    BotResponseError,
    DataFlowAdapter,
    PaidOnlyError,
    UnknowrealbotAdapter,
    UnixRobotAdapter,
    VoidSearchAdapter,
    WorkBotAdapter,
)
from app.services.bot_health import BotHealth
from app.utils.logger import get_logger


FALLBACK_CHAINS = {
    "cpf": ["dataflow", "work_bot", "unknowrealbot", "voidsearch", "black_consultas"],
    "nome": ["dataflow", "work_bot", "unix_robot", "voidsearch", "black_consultas"],
    "telefone": ["dataflow", "work_bot", "unknowrealbot", "voidsearch", "black_consultas"],
    "email": ["dataflow", "work_bot", "unknowrealbot", "black_consultas"],
    "cep": ["unix_robot", "dataflow", "work_bot", "voidsearch", "black_consultas"],
    "cnpj": ["dataflow", "work_bot", "voidsearch"],
    "titulo": ["dataflow", "work_bot", "unknowrealbot"],
    "bin": ["dataflow"],
    "rg": ["work_bot", "unix_robot"],
    "mae": ["work_bot", "dataflow", "unknowrealbot"],
    "pai": ["work_bot", "unknowrealbot"],
    "foto": ["work_bot", "dataflow", "unknowrealbot"],
    "placa": ["work_bot", "unknowrealbot", "voidsearch"],
    "endereco": ["dataflow"],
    "ip": ["unknowrealbot", "voidsearch", "black_consultas"],
    "ddd": ["voidsearch"],
    "proprietario": ["work_bot"],
    "cns": ["work_bot"],
    "chave": ["work_bot"],
    "vizinhos": ["work_bot", "black_consultas"],
    "parentes": ["work_bot", "black_consultas"],
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
            "unknowrealbot": UnknowrealbotAdapter(),
            "unix_robot": UnixRobotAdapter(),
            "voidsearch": VoidSearchAdapter(),
            "black_consultas": BlackConsultasAdapter(),
        }
        self._health_tracker = health_tracker or BotHealth()
        self._logger = get_logger("services.bot_router")
        self._paid_only_by_type: dict[str, set[str]] = {}
        self._paid_only_lock = Lock()

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
        paid_only_exception: PaidOnlyError | None = None
        skipped_paid_only = False

        for adapter_name in chain:
            if self._is_paid_only(tipo, adapter_name):
                skipped_paid_only = True
                self._logger.info(
                    "Adapter ignorado por bloqueio session-level após resposta de assinatura.",
                    extra={
                        "event": "bot_router_adapter_paid_only_skip",
                        "adapter": adapter_name,
                        "tipo": tipo,
                    },
                )
                continue

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
            except PaidOnlyError as exc:
                self._mark_paid_only(tipo, adapter_name)
                failures.append(self._build_failure(adapter_name, exc))
                paid_only_exception = paid_only_exception or exc
                self._logger.info(
                    "Adapter marcado como pago para este tipo e removido do chain em runtime.",
                    extra={
                        "event": "bot_router_paid_only",
                        "adapter": adapter_name,
                        "tipo": tipo,
                        "base": base,
                    },
                )
                continue
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

        if preferred_exception is None and paid_only_exception is not None:
            preferred_exception = paid_only_exception
        elif preferred_exception is None and skipped_paid_only:
            preferred_exception = PaidOnlyError("Todos os bots compatíveis deste tipo exigem assinatura.")

        raise AllBotsFailedError(tipo, failures, preferred_exception=preferred_exception)

    def _is_paid_only(self, tipo: str, adapter_name: str) -> bool:
        with self._paid_only_lock:
            return adapter_name in self._paid_only_by_type.get(tipo, set())

    def _mark_paid_only(self, tipo: str, adapter_name: str) -> None:
        with self._paid_only_lock:
            blocked = self._paid_only_by_type.setdefault(tipo, set())
            blocked.add(adapter_name)

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