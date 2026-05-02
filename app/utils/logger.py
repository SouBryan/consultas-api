import logging
import sys

from pythonjsonlogger.json import JsonFormatter


BASE_LOGGER_NAME = "consultas_api"
_IS_CONFIGURED = False


def configure_logging() -> None:
    global _IS_CONFIGURED
    if _IS_CONFIGURED:
        return

    logger = logging.getLogger(BASE_LOGGER_NAME)
    logger.setLevel(logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    formatter = JsonFormatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        rename_fields={"asctime": "timestamp", "levelname": "level"},
        static_fields={"service": "consultas-api"},
    )
    handler.setFormatter(formatter)

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