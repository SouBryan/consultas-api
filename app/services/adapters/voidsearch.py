import asyncio
import time
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings
from app.services.adapters.base import BotAdapter
from app.services.scraper import parse_consultation_text


class VoidSearchAdapter(BotAdapter):
    name = "voidsearch"
    group_id = settings.group_don
    bot_username = "VoidSearch03Bot"
    supported_commands = (
        "cpf",
        "nome",
        "telefone",
        "cep",
        "ip",
        "cnpj",
        "placa",
        "ddd",
    )

    _TRANSIENT_MARKERS = {"processando", "aguarde", "consultando"}
    _BASE_SELECTION_MARKERS = {"selecione uma base", "escolha uma base"}

    def __init__(self):
        self.group_id = settings.group_don

    def supports(self, tipo: str, base: str | None = None) -> bool:
        if tipo not in self.supported_commands:
            return False

        default_bases = {
            "cpf": {None, "completo"},
            "nome": {None, "nome"},
            "telefone": {None, "telefone"},
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
        bot_entity_id = await self._resolve_bot_entity_id(client)
        collected, close_collector = self._setup_group_collector(client, bot_entity_id)

        try:
            sent_message = await client.send_message(self.group_id, command)
            initial_reply = await self._await_group_reply(
                collected,
                sent_message.id,
                timeout=settings.telegram_timeout,
            )
        finally:
            close_collector()

        self._raise_if_bot_error(initial_reply)
        result_message = initial_reply

        if initial_reply.buttons:
            selected_button = self._resolve_button_text(initial_reply, base)
            follow_up_future, close_waiter = self._create_follow_up_waiter(
                client,
                bot_entity_id,
                related_reply_ids={sent_message.id, initial_reply.id},
                editable_message_ids={initial_reply.id},
            )
            try:
                await initial_reply.click(text=selected_button)
                result_message = await asyncio.wait_for(
                    follow_up_future,
                    timeout=settings.telegram_timeout,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"{self.name} não retornou o resultado dentro de {settings.telegram_timeout} segundos."
                ) from exc
            finally:
                close_waiter()

        self._raise_if_bot_error(result_message)
        return {
            "adapter": self.name,
            "link": "",
            "data": self._parse_message(result_message),
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

    def _create_follow_up_waiter(
        self,
        client: TelegramClient,
        bot_entity_id: int | None,
        *,
        related_reply_ids: set[int],
        editable_message_ids: set[int],
    ) -> tuple[asyncio.Future[Message], Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        new_event = events.NewMessage(chats=self.group_id)
        edit_event = events.MessageEdited(chats=self.group_id)

        async def on_new_message(event: events.NewMessage.Event) -> None:
            message = event.message
            if bot_entity_id is not None and message.sender_id != bot_entity_id:
                return

            reply_to = self._extract_reply_to_msg_id(message)
            if reply_to is not None and reply_to not in related_reply_ids:
                return
            if not self._is_follow_up_message(message):
                return

            if not future.done():
                future.set_result(message)

        async def on_edited_message(event: events.MessageEdited.Event) -> None:
            message = event.message
            if bot_entity_id is not None and message.sender_id != bot_entity_id:
                return
            if message.id not in editable_message_ids:
                return
            if not self._is_follow_up_message(message):
                return

            if not future.done():
                future.set_result(message)

        client.add_event_handler(on_new_message, new_event)
        client.add_event_handler(on_edited_message, edit_event)

        def close() -> None:
            client.remove_event_handler(on_new_message, new_event)
            client.remove_event_handler(on_edited_message, edit_event)

        return future, close

    def _resolve_button_text(self, message: Message, base: str | None) -> str:
        options: list[str] = []
        for row in message.buttons or []:
            for button in row:
                text = (button.text or "").strip()
                if text:
                    options.append(text)

        if not options:
            raise ValueError("VoidSearch retornou seleção de base sem botões disponíveis.")

        if base is not None:
            normalized_base = self._normalize_text(base)
            for option in options:
                normalized_option = self._normalize_text(option)
                if normalized_base in normalized_option or normalized_option in normalized_base:
                    return option

        return options[0]

    def _parse_message(self, message: Message) -> dict[str, Any]:
        text = (message.raw_text or "").strip()
        parsed = parse_consultation_text(text) if text else {}
        if parsed:
            return parsed

        data: dict[str, Any] = {"raw_text": text}
        button_texts = [
            (button.text or "").strip()
            for row in message.buttons or []
            for button in row
            if (button.text or "").strip()
        ]
        if button_texts:
            data["buttons"] = button_texts
        return data

    def _is_actionable_message(self, message: Message) -> bool:
        text = (message.raw_text or "").strip()
        if message.media is not None:
            return True
        if message.buttons:
            return True
        if any(marker in self._normalize_text(text) for marker in self._TRANSIENT_MARKERS):
            return False
        return bool(text)

    def _is_follow_up_message(self, message: Message) -> bool:
        normalized = self._normalize_text(message.raw_text or "")
        if any(marker in normalized for marker in self._BASE_SELECTION_MARKERS) and message.buttons:
            return False
        return self._is_actionable_message(message)

    def _raise_if_bot_error(self, message: Message) -> None:
        self._raise_common_bot_errors(message.raw_text or "")