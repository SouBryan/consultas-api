import asyncio
import time
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings
from app.services.adapters.base import BotAdapter
from app.services.scraper import parse_consultation_text


class PrivateChatStartRequiredError(RuntimeError):
    pass


class UnixRobotAdapter(BotAdapter):
    name = "unix_robot"
    group_id = settings.group_don
    bot_username = "MkBuscasRBot"
    supported_commands = (
        "cpf",
        "nome",
        "telefone",
        "cep",
        "rg",
        "email",
        "mae",
        "site",
    )

    _TRANSIENT_MARKERS = {"processando", "aguarde", "consultando"}
    _START_PRIVATE_MARKERS = {
        "inicie primeiro no privado",
        "iniciar no privado",
        "inicie o bot no privado",
        "inicie no privado",
        "privado para continuar",
    }

    def __init__(self):
        self.group_id = settings.group_don

    def supports(self, tipo: str, base: str | None = None) -> bool:
        if tipo not in self.supported_commands:
            return False

        default_bases = {
            "nome": {None, "nome"},
            "telefone": {None, "telefone"},
            "email": {None, "email"},
        }

        if tipo in default_bases:
            return base in default_bases[tipo]

        return base is None

    async def execute(
        self,
        client: TelegramClient,
        tipo: str,
        input_data: str,
        base: str | None = None,
    ) -> dict[str, Any]:
        if not self.supports(tipo, base):
            raise ValueError(f"Adapter '{self.name}' não suporta '{tipo}' com base '{base}'.")

        command = f"/{tipo} {input_data}".strip()
        return await self._execute_internal(client, command, allow_private_start=True)

    async def _execute_internal(
        self,
        client: TelegramClient,
        command: str,
        *,
        allow_private_start: bool,
    ) -> dict[str, Any]:
        bot_entity_id = await self._resolve_bot_entity_id(client)
        collected, close_collector = self._setup_group_collector(client, bot_entity_id)

        try:
            sent_message = await client.send_message(self.group_id, command)
            bot_reply = await self._await_group_reply(
                collected,
                sent_message.id,
                timeout=settings.telegram_timeout,
            )
        finally:
            close_collector()

        if self._needs_private_start(bot_reply.raw_text or ""):
            if not allow_private_start:
                raise PrivateChatStartRequiredError("Unix Robot ainda exige início do chat privado.")

            private_entity = await client.get_entity(self.bot_username)
            await self._start_private_chat(client, private_entity)
            return await self._execute_private(client, private_entity, bot_entity_id, command)

        self._raise_if_bot_error(bot_reply)
        return {
            "adapter": self.name,
            "link": "",
            "data": self._parse_message(bot_reply),
        }

    async def _execute_private(
        self,
        client: TelegramClient,
        private_entity: Any,
        bot_entity_id: int | None,
        command: str,
    ) -> dict[str, Any]:
        min_message_id = await self._get_last_private_message_id(client, private_entity)
        private_future, close_waiter = self._create_private_waiter(
            client,
            private_entity,
            bot_entity_id,
            min_message_id,
        )

        try:
            await client.send_message(private_entity, command)
            private_message = await asyncio.wait_for(
                private_future,
                timeout=settings.telegram_timeout,
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"{self.name} não entregou o resultado no privado dentro de {settings.telegram_timeout} segundos."
            ) from exc
        finally:
            close_waiter()

        if self._needs_private_start(private_message.raw_text or ""):
            raise PrivateChatStartRequiredError("Unix Robot ainda exige início do chat privado.")

        self._raise_if_bot_error(private_message)
        return {
            "adapter": self.name,
            "link": "",
            "data": self._parse_message(private_message),
        }

    async def _resolve_bot_entity_id(self, client: TelegramClient) -> int | None:
        try:
            entity = await client.get_entity(self.bot_username)
            return entity.id
        except Exception:
            return None

    def _setup_group_collector(
        self,
        client: TelegramClient,
        bot_entity_id: int | None,
    ) -> tuple[list[Message], Any]:
        collected: list[Message] = []
        new_event = events.NewMessage(chats=self.group_id)
        edit_event = events.MessageEdited(chats=self.group_id)

        async def handler(event) -> None:
            message = event.message
            if bot_entity_id is not None and message.sender_id != bot_entity_id:
                return

            for index, existing in enumerate(collected):
                if existing.id == message.id:
                    collected[index] = message
                    return

            collected.append(message)

        client.add_event_handler(handler, new_event)
        client.add_event_handler(handler, edit_event)

        def close() -> None:
            client.remove_event_handler(handler, new_event)
            client.remove_event_handler(handler, edit_event)

        return collected, close

    async def _await_group_reply(
        self,
        collected: list[Message],
        min_message_id: int,
        timeout: int,
    ) -> Message:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for message in collected:
                if message.id <= min_message_id:
                    continue
                if not self._is_actionable_message(message):
                    continue
                return message
            await asyncio.sleep(0.3)

        raise TimeoutError(f"{self.name} não respondeu dentro de {timeout} segundos.")

    def _create_private_waiter(
        self,
        client: TelegramClient,
        private_entity: Any,
        bot_entity_id: int | None,
        min_message_id: int,
    ) -> tuple[asyncio.Future[Message], Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        event_builder = events.NewMessage(chats=private_entity)

        async def handler(event: events.NewMessage.Event) -> None:
            message = event.message
            if message.id <= min_message_id:
                return
            if bot_entity_id is not None and message.sender_id != bot_entity_id:
                return
            if not self._is_actionable_message(message):
                return

            if not future.done():
                future.set_result(message)

        client.add_event_handler(handler, event_builder)

        def close() -> None:
            client.remove_event_handler(handler, event_builder)

        return future, close

    async def _get_last_private_message_id(
        self,
        client: TelegramClient,
        private_entity: Any,
    ) -> int:
        history = await client.get_messages(private_entity, limit=1)
        if history:
            return history[0].id
        return 0

    async def _start_private_chat(self, client: TelegramClient, private_entity: Any) -> None:
        await client.send_message(private_entity, "/start")

    def _parse_message(self, message: Message) -> dict[str, Any]:
        text = (message.raw_text or "").strip()
        parsed = parse_consultation_text(text) if text else {}
        if parsed:
            if message.media is not None:
                parsed.setdefault("has_media", True)
            return parsed

        if message.media is not None:
            return {"has_media": True, "raw_text": text}

        return {"raw_text": text}

    def _is_actionable_message(self, message: Message) -> bool:
        text = (message.raw_text or "").strip()
        if message.media is not None:
            return True
        if any(marker in self._normalize_text(text) for marker in self._TRANSIENT_MARKERS):
            return False
        return bool(text)

    def _needs_private_start(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        return any(marker in normalized for marker in self._START_PRIVATE_MARKERS)

    def _raise_if_bot_error(self, message: Message) -> None:
        self._raise_common_bot_errors(message.raw_text or "")