"""Adapters para integrar diferentes bots de consulta via Telegram."""

from app.services.adapters.base import AllBotsFailedError, BotAdapter, BotResponseError, PaidOnlyError
from app.services.adapters.black_consultas import BlackConsultasAdapter
from app.services.adapters.dataflow import DataFlowAdapter
from app.services.adapters.work_bot import WorkBotAdapter

__all__ = [
    "AllBotsFailedError",
    "BotAdapter",
    "BotResponseError",
    "PaidOnlyError",
    "BlackConsultasAdapter",
    "DataFlowAdapter",
    "WorkBotAdapter",
]