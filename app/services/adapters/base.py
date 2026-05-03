import asyncio
import re
import unicodedata
from abc import ABC, abstractmethod
from http import HTTPStatus
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message


class BotResponseError(ValueError):
    STATUS_MAP = {
        "subscription": HTTPStatus.FORBIDDEN,
        "not_found": HTTPStatus.NOT_FOUND,
        "maintenance": HTTPStatus.SERVICE_UNAVAILABLE,
        "invalid": HTTPStatus.UNPROCESSABLE_ENTITY,
    }

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code

    @property
    def status_code(self) -> int:
        return int(self.STATUS_MAP[self.error_code])


class PaidOnlyError(BotResponseError):
    def __init__(self, message: str = "Base requer assinatura"):
        super().__init__("subscription", message)


class AllBotsFailedError(RuntimeError):
    def __init__(
        self,
        tipo: str,
        failures: list[dict[str, Any]],
        preferred_exception: Exception | None = None,
    ):
        super().__init__(f"Nenhum bot disponível conseguiu responder à consulta '{tipo}'.")
        self.tipo = tipo
        self.failures = failures
        self.preferred_exception = preferred_exception


class BotAdapter(ABC):
    name: str = ""
    group_id: int = 0
    bot_username: str = ""
    supported_commands: tuple[str, ...] = ()

    def supports(self, tipo: str, base: str | None = None) -> bool:
        return tipo in self.supported_commands

    @abstractmethod
    async def execute(
        self,
        client: TelegramClient,
        tipo: str,
        input_data: str,
        base: str | None = None,
    ) -> dict[str, Any]:
        """Executa a consulta e retorna resultado estruturado."""

    async def wait_for_bot_reply(
        self,
        client: TelegramClient,
        sent_msg: Message,
        timeout: int = 15,
    ) -> Message:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        event_builder = events.NewMessage(chats=self.group_id)

        async def handler(event: events.NewMessage.Event) -> None:
            message = event.message
            if self._extract_reply_to_msg_id(message) != sent_msg.id:
                return

            if not future.done():
                future.set_result(message)

        client.add_event_handler(handler, event_builder)
        try:
            return await asyncio.wait_for(future, timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(f"{self.name} não respondeu dentro de {timeout} segundos.") from exc
        finally:
            client.remove_event_handler(handler, event_builder)

    @staticmethod
    def _extract_reply_to_msg_id(message: Message) -> int | None:
        direct_reply_id = getattr(message, "reply_to_msg_id", None)
        if direct_reply_id is not None:
            return direct_reply_id

        reply_header = getattr(message, "reply_to", None)
        return getattr(reply_header, "reply_to_msg_id", None)

    @staticmethod
    def _normalize_text(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.casefold())
        return "".join(char for char in decomposed if not unicodedata.combining(char))

    @classmethod
    def _slugify(cls, value: str) -> str:
        normalized = cls._normalize_text(value)
        normalized = re.sub(r"[^a-z0-9]+", "_", normalized)
        return normalized.strip("_")

    @classmethod
    def _raise_common_bot_errors(cls, text: str) -> None:
        normalized = cls._normalize_text(text)

        if "assinatura ativa" in normalized or "planos privados" in normalized:
            raise PaidOnlyError("Base requer assinatura")
        if "nao tem acesso" in normalized or "entre em contato com o administrador" in normalized:
            raise PaidOnlyError("Módulo requer acesso/assinatura")
        if "manutencao" in normalized:
            raise BotResponseError("maintenance", "Em manutenção")
        if "invalido" in normalized:
            raise BotResponseError("invalid", text or "O bot retornou input inválido.")
        if (
            "nao encontrado" in normalized
            or "nenhum resultado" in normalized
            or "sem resultados" in normalized
            or "nenhum dado" in normalized
        ):
            raise BotResponseError("not_found", "Não encontrado")