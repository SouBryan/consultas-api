from telethon import TelegramClient

from app.services.adapters.black_consultas import (
    BlackConsultasAdapter,
    build_command,
    resolve_base_button_text,
)
from app.services.adapters.base import BotResponseError


_default_adapter = BlackConsultasAdapter()


async def execute_query(
    client: TelegramClient,
    command: str,
    base_button_text: str | None,
) -> str:
    return await _default_adapter.execute_query(client, command, base_button_text)