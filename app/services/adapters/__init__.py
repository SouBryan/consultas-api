"""Adapters para integrar diferentes bots de consulta via Telegram."""

from app.services.adapters.base import AllBotsFailedError, BotAdapter, BotResponseError, PaidOnlyError
from app.services.adapters.black_consultas import BlackConsultasAdapter
from app.services.adapters.dataflow import DataFlowAdapter
from app.services.adapters.unknowrealbot import UnknowrealbotAdapter
from app.services.adapters.unix_robot import UnixRobotAdapter
from app.services.adapters.voidsearch import VoidSearchAdapter
from app.services.adapters.work_bot import WorkBotAdapter

__all__ = [
    "AllBotsFailedError",
    "BotAdapter",
    "BotResponseError",
    "PaidOnlyError",
    "BlackConsultasAdapter",
    "DataFlowAdapter",
    "UnknowrealbotAdapter",
    "UnixRobotAdapter",
    "VoidSearchAdapter",
    "WorkBotAdapter",
]