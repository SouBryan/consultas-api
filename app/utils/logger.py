import contextvars
from contextlib import contextmanager
import logging
import sys
from typing import Any, Iterator

from pythonjsonlogger.json import JsonFormatter


BASE_LOGGER_NAME = "consultas_api"
_IS_CONFIGURED = False
_LOG_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "consultas_api_log_context",
    default={},
)


class _LogContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        context = _LOG_CONTEXT.get({})
        for key, value in context.items():
            if not hasattr(record, key):
                setattr(record, key, value)

        if not hasattr(record, "request_id"):
            record.request_id = None

        return True


def configure_logging() -> None:
    global _IS_CONFIGURED
    if _IS_CONFIGURED:
        return

    logger = logging.getLogger(BASE_LOGGER_NAME)
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s %(request_id)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
        static_fields={"service": "consultas-api"},
    )
    handler.setFormatter(formatter)
    handler.addFilter(_LogContextFilter())

    logger.handlers.clear()
    logger.addHandler(handler)
    logger.propagate = False
    _IS_CONFIGURED = True


def get_logger(name: str | None = None) -> logging.Logger:
    configure_logging()
    base_logger = logging.getLogger(BASE_LOGGER_NAME)
    if not name:
        return base_logger
    return base_logger.getChild(name)


@contextmanager
def log_context(**fields: Any) -> Iterator[None]:
    current = dict(_LOG_CONTEXT.get({}))
    current.update({key: value for key, value in fields.items() if value is not None})
    token = _LOG_CONTEXT.set(current)
    try:
        yield
    finally:
        _LOG_CONTEXT.reset(token)


def get_log_context() -> dict[str, Any]:
    return dict(_LOG_CONTEXT.get({}))


def get_request_id() -> str | None:
    request_id = _LOG_CONTEXT.get({}).get("request_id")
    if request_id is None:
        return None
    return str(request_id)