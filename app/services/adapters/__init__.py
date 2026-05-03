"""Adapters para integrar diferentes bots de consulta via Telegram."""

from app.services.adapters.base import AllBotsFailedError, BotAdapter, BotResponseError
from app.services.adapters.black_consultas import BlackConsultasAdapter
from app.services.adapters.dataflow import DataFlowAdapter

__all__ = [
    "AllBotsFailedError",
    "BotAdapter",
    "BotResponseError",
    "BlackConsultasAdapter",
    "DataFlowAdapter",
]