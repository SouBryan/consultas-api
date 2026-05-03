import time
from threading import Lock
from typing import Any


class BotHealth:
    def __init__(self):
        self._states: dict[str, dict[str, Any]] = {}
        self._lock = Lock()

    def is_healthy(self, adapter_name: str) -> bool:
        with self._lock:
            state = self._states.get(adapter_name)
            if state is None:
                return True

            cooldown_until = state.get("cooldown_until")
            if cooldown_until is None:
                return bool(state.get("healthy", True))

            if time.monotonic() >= cooldown_until:
                self._states[adapter_name] = {
                    "healthy": True,
                    "last_failure": state.get("last_failure"),
                    "cooldown_until": None,
                    "reason": None,
                }
                return True

            return False

    def mark_unhealthy(self, adapter_name: str, cooldown_seconds: int, reason: str | None = None) -> None:
        with self._lock:
            self._states[adapter_name] = {
                "healthy": False,
                "last_failure": time.time(),
                "cooldown_until": time.monotonic() + max(0, cooldown_seconds),
                "reason": reason,
            }

    def mark_healthy(self, adapter_name: str) -> None:
        with self._lock:
            current_state = self._states.get(adapter_name, {})
            self._states[adapter_name] = {
                "healthy": True,
                "last_failure": current_state.get("last_failure"),
                "cooldown_until": None,
                "reason": None,
            }

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {name: state.copy() for name, state in self._states.items()}