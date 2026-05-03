import asyncio
import time
from dataclasses import dataclass
from threading import Lock
from typing import Any

from telethon import TelegramClient

from app.services.account_pool import AccountPool
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


FAST_FAILOVER_TIMEOUT_SECONDS = 10.0
MAX_PARALLEL_ATTEMPTS = 2

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
}


@dataclass
class RunningAttempt:
    adapter_name: str
    account_label: str
    started_at: float


class BotRouter:
    def __init__(
        self,
        adapters: dict[str, BotAdapter] | None = None,
        health_tracker: BotHealth | None = None,
        group_rate_limiter: Any | None = None,
    ):
        self._adapters = adapters or {
            "dataflow": DataFlowAdapter(),
            "work_bot": WorkBotAdapter(),
            "unknowrealbot": UnknowrealbotAdapter(),
            "unix_robot": UnixRobotAdapter(),
            "voidsearch": VoidSearchAdapter(),
            "black_consultas": BlackConsultasAdapter(),
        }
        if group_rate_limiter is not None:
            for adapter in self._adapters.values():
                adapter.set_group_rate_limiter(group_rate_limiter)

        self._health_tracker = health_tracker or BotHealth()
        self._logger = get_logger("services.bot_router")
        self._paid_only_by_type: dict[str, set[str]] = {}
        self._paid_only_lock = Lock()

    @property
    def adapter_names(self) -> list[str]:
        return list(self._adapters.keys())

    async def route_query(
        self,
        client: TelegramClient,
        tipo: str,
        input_data: str,
        base: str | None = None,
    ) -> dict[str, Any]:
        chain = self._get_chain(tipo)
        failures: list[dict[str, Any]] = []
        preferred_exception: Exception | None = None
        paid_only_exception: PaidOnlyError | None = None
        skipped_paid_only = False

        for adapter_name in chain:
            adapter = self._prepare_adapter_attempt(adapter_name, tipo, base)
            if adapter is None:
                if self._is_paid_only(tipo, adapter_name):
                    skipped_paid_only = True
                continue

            try:
                result = await self._execute_single_adapter(
                    client,
                    adapter_name,
                    tipo,
                    input_data,
                    base,
                )
                result.setdefault("adapter", adapter_name)
                result.setdefault("link", "")
                result.setdefault("data", {})
                return result
            except PaidOnlyError as exc:
                failures.append(self._build_failure(adapter_name, exc))
                paid_only_exception = paid_only_exception or exc
            except Exception as exc:
                failures.append(self._build_failure(adapter_name, exc))
                preferred_exception = self._choose_preferred_exception(preferred_exception, exc)

        if preferred_exception is None and paid_only_exception is not None:
            preferred_exception = paid_only_exception
        elif preferred_exception is None and skipped_paid_only:
            preferred_exception = PaidOnlyError("Todos os bots compatíveis deste tipo exigem assinatura.")

        raise AllBotsFailedError(tipo, failures, preferred_exception=preferred_exception)

    async def route_query_with_pool(
        self,
        pool: AccountPool,
        tipo: str,
        input_data: str,
        base: str | None = None,
    ) -> dict[str, Any]:
        self._get_chain(tipo)
        remaining_adapters = list(FALLBACK_CHAINS[tipo])
        failures: list[dict[str, Any]] = []
        preferred_exception: Exception | None = None
        paid_only_exception: PaidOnlyError | None = None
        skipped_paid_only = False
        active_tasks: dict[asyncio.Task[dict[str, Any]], RunningAttempt] = {}
        parallel_triggered = False

        async def schedule_next(wait_for_account: bool) -> bool:
            nonlocal skipped_paid_only

            while remaining_adapters:
                adapter_name = remaining_adapters[0]

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
                    remaining_adapters.pop(0)
                    continue

                adapter = self._adapters.get(adapter_name)
                if adapter is None:
                    remaining_adapters.pop(0)
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
                    remaining_adapters.pop(0)
                    continue

                excluded_labels = {meta.account_label for meta in active_tasks.values()}
                acquired = (
                    await pool.acquire(exclude_labels=excluded_labels)
                    if wait_for_account
                    else await pool.try_acquire(exclude_labels=excluded_labels)
                )
                if acquired is None:
                    return False

                account_label, client = acquired
                if not self._health_tracker.can_execute(adapter_name):
                    pool.release(account_label)
                    status = self._health_tracker.get_status(adapter_name)
                    self._logger.info(
                        "Adapter em cooldown ou circuit breaker, fallback para o próximo bot.",
                        extra={
                            "event": "bot_router_adapter_cooldown",
                            "adapter": adapter_name,
                            "tipo": tipo,
                            "reason": status.get("reason"),
                            "circuit_state": status.get("circuit_state"),
                        },
                    )
                    remaining_adapters.pop(0)
                    continue

                remaining_adapters.pop(0)
                task = asyncio.create_task(
                    self._run_adapter_with_pool(
                        pool,
                        account_label,
                        client,
                        adapter_name,
                        tipo,
                        input_data,
                        base,
                    )
                )
                active_tasks[task] = RunningAttempt(
                    adapter_name=adapter_name,
                    account_label=account_label,
                    started_at=time.monotonic(),
                )
                return True

            return False

        scheduled = await schedule_next(wait_for_account=True)
        if not scheduled:
            if preferred_exception is None and skipped_paid_only:
                preferred_exception = PaidOnlyError("Todos os bots compatíveis deste tipo exigem assinatura.")
            raise AllBotsFailedError(tipo, failures, preferred_exception=preferred_exception)

        while active_tasks or remaining_adapters:
            if not active_tasks:
                started = await schedule_next(wait_for_account=True)
                if not started:
                    break
                parallel_triggered = False
                continue

            wait_timeout: float | None = None
            if not parallel_triggered and len(active_tasks) == 1 and remaining_adapters:
                first_attempt = next(iter(active_tasks.values()))
                elapsed = time.monotonic() - first_attempt.started_at
                wait_timeout = max(0.0, FAST_FAILOVER_TIMEOUT_SECONDS - elapsed)

            done, _ = await asyncio.wait(
                active_tasks.keys(),
                timeout=wait_timeout,
                return_when=asyncio.FIRST_COMPLETED,
            )

            if not done:
                parallel_triggered = True
                if await schedule_next(wait_for_account=False):
                    self._logger.info(
                        "Failover paralelo ativado por lentidão do adapter primário.",
                        extra={
                            "event": "bot_router_parallel_failover",
                            "tipo": tipo,
                            "active_adapters": [meta.adapter_name for meta in active_tasks.values()],
                        },
                    )
                continue

            success_result: dict[str, Any] | None = None
            for task in done:
                meta = active_tasks.pop(task)
                try:
                    result = task.result()
                except PaidOnlyError as exc:
                    failures.append(self._build_failure(meta.adapter_name, exc))
                    paid_only_exception = paid_only_exception or exc
                except Exception as exc:
                    failures.append(self._build_failure(meta.adapter_name, exc))
                    preferred_exception = self._choose_preferred_exception(preferred_exception, exc)
                else:
                    success_result = result

            if success_result is not None:
                if active_tasks:
                    await self._cancel_pending_tasks(active_tasks)
                success_result.setdefault("link", "")
                success_result.setdefault("data", {})
                return success_result

            while active_tasks and len(active_tasks) < min(MAX_PARALLEL_ATTEMPTS, pool.size) and remaining_adapters:
                started_parallel = await schedule_next(wait_for_account=False)
                if not started_parallel:
                    break

        if preferred_exception is None and paid_only_exception is not None:
            preferred_exception = paid_only_exception
        elif preferred_exception is None and skipped_paid_only:
            preferred_exception = PaidOnlyError("Todos os bots compatíveis deste tipo exigem assinatura.")

        raise AllBotsFailedError(tipo, failures, preferred_exception=preferred_exception)

    def get_bot_statuses(self) -> dict[str, dict[str, Any]]:
        return self._health_tracker.get_statuses(self.adapter_names)

    def get_bot_metrics(self) -> dict[str, dict[str, Any]]:
        return self._health_tracker.get_metrics(self.adapter_names)

    def describe_chain(self, tipo: str, base: str | None = None) -> dict[str, Any]:
        chain = self._get_chain(tipo)
        health_statuses = self._health_tracker.get_statuses(list(dict.fromkeys(chain)))
        described_chain = []

        for position, adapter_name in enumerate(chain, start=1):
            adapter = self._adapters.get(adapter_name)
            health_status = health_statuses.get(adapter_name, {})
            described_chain.append(
                {
                    "position": position,
                    "adapter": adapter_name,
                    "supported": adapter.supports(tipo, base) if adapter is not None else False,
                    "paid_only_blocked": self._is_paid_only(tipo, adapter_name),
                    **health_status,
                }
            )

        return {
            "tipo": tipo,
            "base": base,
            "chain": described_chain,
        }

    async def _run_adapter_with_pool(
        self,
        pool: AccountPool,
        account_label: str,
        client: TelegramClient,
        adapter_name: str,
        tipo: str,
        input_data: str,
        base: str | None,
    ) -> dict[str, Any]:
        try:
            result = await self._execute_single_adapter(
                client,
                adapter_name,
                tipo,
                input_data,
                base,
                account_label=account_label,
            )
            result.setdefault("account", account_label)
            return result
        finally:
            pool.release(account_label)

    async def _execute_single_adapter(
        self,
        client: TelegramClient,
        adapter_name: str,
        tipo: str,
        input_data: str,
        base: str | None,
        *,
        account_label: str | None = None,
    ) -> dict[str, Any]:
        adapter = self._adapters[adapter_name]
        started_at = time.monotonic()

        self._logger.info(
            "Tentando adapter para a consulta.",
            extra={
                "event": "bot_router_attempt",
                "adapter": adapter_name,
                "tipo": tipo,
                "base": base,
                "account": account_label,
            },
        )

        try:
            result = await adapter.execute(client, tipo, input_data, base)
            duration_seconds = time.monotonic() - started_at
            self._health_tracker.record_success(adapter_name, duration_seconds)
            self._logger.info(
                "Adapter retornou sucesso.",
                extra={
                    "event": "bot_router_success",
                    "adapter": adapter_name,
                    "tipo": tipo,
                    "base": base,
                    "account": account_label,
                    "duration_ms": round(duration_seconds * 1000, 2),
                },
            )
            result.setdefault("adapter", adapter_name)
            return result
        except PaidOnlyError:
            duration_seconds = time.monotonic() - started_at
            self._mark_paid_only(tipo, adapter_name)
            self._health_tracker.record_failure(
                adapter_name,
                reason="subscription",
                response_time_seconds=duration_seconds,
            )
            self._logger.info(
                "Adapter marcado como pago para este tipo e removido do chain em runtime.",
                extra={
                    "event": "bot_router_paid_only",
                    "adapter": adapter_name,
                    "tipo": tipo,
                    "base": base,
                    "account": account_label,
                    "duration_ms": round(duration_seconds * 1000, 2),
                },
            )
            raise
        except TimeoutError:
            duration_seconds = time.monotonic() - started_at
            self._health_tracker.record_failure(
                adapter_name,
                reason="timeout",
                response_time_seconds=duration_seconds,
                cooldown_seconds=60,
            )
            self._logger.warning(
                "Adapter falhou por timeout.",
                extra={
                    "event": "bot_router_timeout",
                    "adapter": adapter_name,
                    "tipo": tipo,
                    "base": base,
                    "account": account_label,
                    "duration_ms": round(duration_seconds * 1000, 2),
                },
            )
            raise
        except BotResponseError as exc:
            duration_seconds = time.monotonic() - started_at
            cooldown_seconds = 300 if exc.error_code == "maintenance" else None
            self._health_tracker.record_failure(
                adapter_name,
                reason=exc.error_code,
                response_time_seconds=duration_seconds,
                cooldown_seconds=cooldown_seconds,
            )
            self._logger.warning(
                "Adapter retornou erro de negócio.",
                extra={
                    "event": "bot_router_business_error",
                    "adapter": adapter_name,
                    "tipo": tipo,
                    "base": base,
                    "account": account_label,
                    "error_code": exc.error_code,
                    "status_code": exc.status_code,
                    "duration_ms": round(duration_seconds * 1000, 2),
                },
            )
            raise
        except asyncio.CancelledError:
            duration_seconds = time.monotonic() - started_at
            self._health_tracker.cancel_attempt(adapter_name)
            self._logger.info(
                "Tentativa cancelada após sucesso de outro adapter.",
                extra={
                    "event": "bot_router_attempt_cancelled",
                    "adapter": adapter_name,
                    "tipo": tipo,
                    "base": base,
                    "account": account_label,
                    "duration_ms": round(duration_seconds * 1000, 2),
                },
            )
            raise
        except Exception:
            duration_seconds = time.monotonic() - started_at
            self._health_tracker.record_failure(
                adapter_name,
                reason="adapter_failure",
                response_time_seconds=duration_seconds,
            )
            self._logger.warning(
                "Adapter falhou e o router seguirá para o próximo fallback.",
                extra={
                    "event": "bot_router_adapter_failed",
                    "adapter": adapter_name,
                    "tipo": tipo,
                    "base": base,
                    "account": account_label,
                    "duration_ms": round(duration_seconds * 1000, 2),
                },
            )
            raise

    async def _cancel_pending_tasks(
        self,
        active_tasks: dict[asyncio.Task[dict[str, Any]], RunningAttempt],
    ) -> None:
        pending_tasks = list(active_tasks.keys())
        active_tasks.clear()
        for task in pending_tasks:
            task.cancel()
        if pending_tasks:
            await asyncio.gather(*pending_tasks, return_exceptions=True)

    def _prepare_adapter_attempt(
        self,
        adapter_name: str,
        tipo: str,
        base: str | None,
    ) -> BotAdapter | None:
        if self._is_paid_only(tipo, adapter_name):
            self._logger.info(
                "Adapter ignorado por bloqueio session-level após resposta de assinatura.",
                extra={
                    "event": "bot_router_adapter_paid_only_skip",
                    "adapter": adapter_name,
                    "tipo": tipo,
                },
            )
            return None

        adapter = self._adapters.get(adapter_name)
        if adapter is None:
            return None

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
            return None

        if not self._health_tracker.can_execute(adapter_name):
            status = self._health_tracker.get_status(adapter_name)
            self._logger.info(
                "Adapter em cooldown ou circuit breaker, fallback para o próximo bot.",
                extra={
                    "event": "bot_router_adapter_cooldown",
                    "adapter": adapter_name,
                    "tipo": tipo,
                    "reason": status.get("reason"),
                    "circuit_state": status.get("circuit_state"),
                },
            )
            return None

        return adapter

    def _get_chain(self, tipo: str) -> list[str]:
        chain = FALLBACK_CHAINS.get(tipo)
        if not chain:
            raise ValueError(f"Tipo de consulta '{tipo}' não suportado.")
        return chain

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
