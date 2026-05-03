from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
import time
from threading import Lock
from typing import Any

from app.utils.logger import get_logger


@dataclass
class AdapterHealthState:
    healthy: bool = True
    cooldown_until: float | None = None
    reason: str | None = None
    last_failure: float | None = None
    last_success: float | None = None
    history: deque[bool] = field(default_factory=lambda: deque(maxlen=20))
    response_times: deque[float] = field(default_factory=lambda: deque(maxlen=20))
    consecutive_failures: int = 0
    circuit_state: str = "closed"
    trial_in_progress: bool = False
    last_state_change: float | None = None


class BotHealth:
    def __init__(
        self,
        history_window: int = 20,
        low_success_threshold: float = 0.3,
        low_success_cooldown_seconds: int = 600,
        circuit_breaker_failures: int = 5,
        circuit_breaker_cooldown_seconds: int = 1800,
    ):
        self._states: dict[str, AdapterHealthState] = {}
        self._lock = Lock()
        self._history_window = max(1, history_window)
        self._low_success_threshold = max(0.0, min(1.0, low_success_threshold))
        self._low_success_cooldown_seconds = max(0, low_success_cooldown_seconds)
        self._circuit_breaker_failures = max(1, circuit_breaker_failures)
        self._circuit_breaker_cooldown_seconds = max(0, circuit_breaker_cooldown_seconds)
        self._logger = get_logger("services.bot_health")

    def can_execute(self, adapter_name: str) -> bool:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            self._refresh_state_locked(adapter_name, state)

            if state.circuit_state == "open":
                return False

            if not state.healthy:
                return False

            if state.circuit_state == "half_open":
                if state.trial_in_progress:
                    return False
                state.trial_in_progress = True
                return True

            return True

    def is_healthy(self, adapter_name: str) -> bool:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            self._refresh_state_locked(adapter_name, state)
            if state.circuit_state == "half_open" and state.trial_in_progress:
                return False
            return state.healthy and state.circuit_state != "open"

    def mark_unhealthy(self, adapter_name: str, cooldown_seconds: int, reason: str | None = None) -> None:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            self._set_temporary_cooldown_locked(
                adapter_name,
                state,
                cooldown_seconds=max(0, cooldown_seconds),
                reason=reason or "temporary_unhealthy",
            )

    def mark_healthy(self, adapter_name: str) -> None:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            state.healthy = True
            state.cooldown_until = None
            state.reason = None
            state.trial_in_progress = False
            if state.circuit_state == "half_open":
                self._transition_circuit_locked(adapter_name, state, "closed", reason="half_open_success")

    def record_success(self, adapter_name: str, response_time_seconds: float) -> None:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            self._refresh_state_locked(adapter_name, state)
            state.history.append(True)
            state.response_times.append(max(0.0, response_time_seconds))
            state.last_success = time.time()
            state.consecutive_failures = 0
            state.trial_in_progress = False
            state.healthy = True
            state.cooldown_until = None
            state.reason = None

            if state.circuit_state != "closed":
                self._transition_circuit_locked(adapter_name, state, "closed", reason="success")

            self._apply_low_success_rate_locked(adapter_name, state)

    def record_failure(
        self,
        adapter_name: str,
        *,
        reason: str,
        response_time_seconds: float | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            self._refresh_state_locked(adapter_name, state)
            state.history.append(False)
            if response_time_seconds is not None:
                state.response_times.append(max(0.0, response_time_seconds))
            state.last_failure = time.time()
            state.consecutive_failures += 1
            state.trial_in_progress = False

            if state.circuit_state == "half_open":
                self._open_circuit_locked(adapter_name, state, reason="half_open_failure")
                return

            if state.consecutive_failures >= self._circuit_breaker_failures:
                self._open_circuit_locked(adapter_name, state, reason="consecutive_failures")
                return

            if cooldown_seconds:
                self._set_temporary_cooldown_locked(
                    adapter_name,
                    state,
                    cooldown_seconds=max(0, cooldown_seconds),
                    reason=reason,
                )

            self._apply_low_success_rate_locked(adapter_name, state)

    def cancel_attempt(self, adapter_name: str) -> None:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            state.trial_in_progress = False

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {
                name: self._serialize_state_locked(name, state)
                for name, state in self._states.items()
            }

    def get_status(self, adapter_name: str) -> dict[str, Any]:
        with self._lock:
            state = self._ensure_state_locked(adapter_name)
            self._refresh_state_locked(adapter_name, state)
            return self._serialize_state_locked(adapter_name, state)

    def get_statuses(self, adapter_names: list[str]) -> dict[str, dict[str, Any]]:
        with self._lock:
            statuses: dict[str, dict[str, Any]] = {}
            for adapter_name in adapter_names:
                state = self._ensure_state_locked(adapter_name)
                self._refresh_state_locked(adapter_name, state)
                statuses[adapter_name] = self._serialize_state_locked(adapter_name, state)
            return statuses

    def _ensure_state_locked(self, adapter_name: str) -> AdapterHealthState:
        state = self._states.get(adapter_name)
        if state is not None:
            return state

        state = AdapterHealthState(
            history=deque(maxlen=self._history_window),
            response_times=deque(maxlen=self._history_window),
            last_state_change=time.time(),
        )
        self._states[adapter_name] = state
        return state

    def _refresh_state_locked(self, adapter_name: str, state: AdapterHealthState) -> None:
        now = time.monotonic()

        if state.circuit_state == "open" and state.cooldown_until is not None and now >= state.cooldown_until:
            self._transition_circuit_locked(adapter_name, state, "half_open", reason="cooldown_expired")
            state.healthy = True
            state.cooldown_until = None
            state.reason = "half_open"
            state.trial_in_progress = False
            return

        if state.circuit_state == "closed" and state.cooldown_until is not None and now >= state.cooldown_until:
            state.healthy = True
            state.cooldown_until = None
            state.reason = None

    def _apply_low_success_rate_locked(self, adapter_name: str, state: AdapterHealthState) -> None:
        if len(state.history) < self._history_window:
            return

        success_rate = self._calculate_success_rate_locked(state)
        if success_rate >= self._low_success_threshold:
            return

        self._set_temporary_cooldown_locked(
            adapter_name,
            state,
            cooldown_seconds=self._low_success_cooldown_seconds,
            reason="low_success_rate",
        )

    def _set_temporary_cooldown_locked(
        self,
        adapter_name: str,
        state: AdapterHealthState,
        *,
        cooldown_seconds: int,
        reason: str,
    ) -> None:
        state.healthy = False
        state.reason = reason
        state.last_failure = time.time()
        state.cooldown_until = time.monotonic() + max(0, cooldown_seconds)
        self._logger.warning(
            "Adapter marcado temporariamente como unhealthy.",
            extra={
                "event": "bot_health_unhealthy",
                "adapter": adapter_name,
                "reason": reason,
                "cooldown_seconds": cooldown_seconds,
            },
        )

    def _open_circuit_locked(
        self,
        adapter_name: str,
        state: AdapterHealthState,
        *,
        reason: str,
    ) -> None:
        state.healthy = False
        state.cooldown_until = time.monotonic() + self._circuit_breaker_cooldown_seconds
        state.reason = reason
        state.trial_in_progress = False
        self._transition_circuit_locked(adapter_name, state, "open", reason=reason)

    def _transition_circuit_locked(
        self,
        adapter_name: str,
        state: AdapterHealthState,
        new_state: str,
        *,
        reason: str | None,
    ) -> None:
        old_state = state.circuit_state
        state.circuit_state = new_state
        state.last_state_change = time.time()
        self._logger.info(
            "Estado do circuit breaker atualizado.",
            extra={
                "event": "bot_circuit_state_changed",
                "adapter": adapter_name,
                "old_state": old_state,
                "new_state": new_state,
                "reason": reason,
            },
        )

    def _serialize_state_locked(self, adapter_name: str, state: AdapterHealthState) -> dict[str, Any]:
        self._refresh_state_locked(adapter_name, state)
        avg_time_seconds = self._calculate_avg_time_locked(state)
        cooldown_remaining = None
        if state.cooldown_until is not None and state.circuit_state == "open":
            cooldown_remaining = max(0.0, state.cooldown_until - time.monotonic())

        if state.cooldown_until is not None and state.circuit_state == "closed":
            cooldown_remaining = max(0.0, state.cooldown_until - time.monotonic())

        return {
            "healthy": state.healthy and state.circuit_state != "open" and not state.trial_in_progress,
            "success_rate": round(self._calculate_success_rate_locked(state), 4),
            "avg_time_seconds": None if avg_time_seconds is None else round(avg_time_seconds, 3),
            "avg_time": None if avg_time_seconds is None else f"{avg_time_seconds:.1f}s",
            "last_success": self._relative_time(state.last_success),
            "last_success_at": self._to_isoformat(state.last_success),
            "last_failure_at": self._to_isoformat(state.last_failure),
            "consecutive_failures": state.consecutive_failures,
            "circuit_state": state.circuit_state,
            "reason": state.reason,
            "cooldown_remaining_seconds": None if cooldown_remaining is None else round(cooldown_remaining, 3),
        }

    @staticmethod
    def _calculate_success_rate_locked(state: AdapterHealthState) -> float:
        if not state.history:
            return 1.0
        successes = sum(1 for item in state.history if item)
        return successes / len(state.history)

    @staticmethod
    def _calculate_avg_time_locked(state: AdapterHealthState) -> float | None:
        if not state.response_times:
            return None
        return sum(state.response_times) / len(state.response_times)

    @staticmethod
    def _relative_time(timestamp: float | None) -> str | None:
        if timestamp is None:
            return None

        elapsed = max(0, int(time.time() - timestamp))
        if elapsed < 60:
            return f"{elapsed}s ago"
        if elapsed < 3600:
            return f"{elapsed // 60}min ago"
        if elapsed < 86400:
            return f"{elapsed // 3600}h ago"
        return f"{elapsed // 86400}d ago"

    @staticmethod
    def _to_isoformat(timestamp: float | None) -> str | None:
        if timestamp is None:
            return None
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()