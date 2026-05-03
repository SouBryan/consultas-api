import asyncio
import time
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings
from app.services.adapters.base import BotAdapter
from app.services.adapters.black_consultas import COMMAND_MAP as BLACK_COMMAND_MAP
from app.services.adapters.black_consultas import resolve_base_button_text
from app.services.scraper import scrape_result


RESULT_URL_FRAGMENT = "result-consultation"


class UnknowrealbotAdapter(BotAdapter):
    name = "unknowrealbot"
    group_id = settings.group_don
    bot_username = "Unknowrealbot"
    supported_commands = (
        "cpf",
        "nome",
        "telefone",
        "cep",
        "ip",
        "cnpj",
        "email",
        "titulo",
        "mae",
        "foto",
        "pai",
        "placa",
    )

    _TRANSIENT_MARKERS = {"processando", "aguarde", "consultando"}

    def __init__(
        self,
        scraper: Callable[[str], Awaitable[dict[str, Any]]] = scrape_result,
    ):
        self.group_id = settings.group_don
        self._scraper = scraper

    def supports(self, tipo: str, base: str | None = None) -> bool:
        if tipo not in self.supported_commands:
            return False

        definition = BLACK_COMMAND_MAP.get(tipo)
        if definition is None or not definition.has_sub_base:
            return base is None

        if base is None:
            return True

        return base in definition.button_map

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
            bot_reply = await self._await_group_reply(
                collected,
                sent_message.id,
                timeout=settings.telegram_timeout,
            )
        finally:
            close_collector()

        self._raise_if_bot_error(bot_reply)

        result_url = self._extract_result_url(bot_reply)
        if result_url is None and bot_reply.buttons:
            button_text = self._resolve_button_text(tipo, base, bot_reply)
            follow_up_future, close_waiter = self._create_result_waiter(
                client,
                bot_entity_id,
                related_reply_ids={sent_message.id, bot_reply.id},
                editable_message_ids={bot_reply.id},
            )
            try:
                if button_text is None:
                    await bot_reply.buttons[0][0].click()
                else:
                    await bot_reply.click(text=button_text)

                result_message = await asyncio.wait_for(
                    follow_up_future,
                    timeout=settings.telegram_timeout,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"{self.name} não retornou a URL do resultado dentro de {settings.telegram_timeout} segundos."
                ) from exc
            finally:
                close_waiter()

            self._raise_if_bot_error(result_message)
            result_url = self._extract_result_url(result_message)

        if result_url is None:
            raise ValueError("Não foi possível extrair a URL do resultado do Unknowrealbot.")

        data = await self._scraper(result_url)
        return {
            "adapter": self.name,
            "link": result_url,
            "data": data,
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
            if not self._is_from_this_bot(message, bot_entity_id):
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

    def _is_from_this_bot(self, message: Message, bot_entity_id: int | None) -> bool:
        if bot_entity_id is not None and message.sender_id == bot_entity_id:
            return True
        text = (message.raw_text or "").lower()
        return f"@{self.bot_username.lower()}" in text

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
                if not self._is_actionable_group_message(message):
                    continue
                return message
            await asyncio.sleep(0.3)

        raise TimeoutError(f"{self.name} não respondeu dentro de {timeout} segundos.")

    def _create_result_waiter(
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

            if not self._message_has_result_or_error(message):
                return

            if not future.done():
                future.set_result(message)

        async def on_edited_message(event: events.MessageEdited.Event) -> None:
            message = event.message
            if bot_entity_id is not None and message.sender_id != bot_entity_id:
                return

            if message.id not in editable_message_ids:
                return

            if not self._message_has_result_or_error(message):
                return

            if not future.done():
                future.set_result(message)

        client.add_event_handler(on_new_message, new_event)
        client.add_event_handler(on_edited_message, edit_event)

        def close() -> None:
            client.remove_event_handler(on_new_message, new_event)
            client.remove_event_handler(on_edited_message, edit_event)

        return future, close

    def _resolve_button_text(
        self,
        tipo: str,
        base: str | None,
        message: Message,
    ) -> str | None:
        if not message.buttons:
            return None

        if base is None:
            return (message.buttons[0][0].text or "").strip() or None

        button_text = resolve_base_button_text(tipo, base)
        normalized_target = self._normalize_text(button_text)
        for row in message.buttons:
            for button in row:
                if self._normalize_text(button.text or "") == normalized_target:
                    return button.text

        raise ValueError(f"Botão '{button_text}' não encontrado na resposta do Unknowrealbot.")

    def _extract_result_url(self, message: Message) -> str | None:
        for row in message.buttons or []:
            for button in row:
                button_url = getattr(button, "url", None)
                if button_url and RESULT_URL_FRAGMENT in button_url:
                    return button_url
        return None

    def _is_actionable_group_message(self, message: Message) -> bool:
        text = (message.raw_text or "").strip()
        if self._extract_result_url(message) is not None:
            return True
        if message.buttons:
            return True
        if any(marker in self._normalize_text(text) for marker in self._TRANSIENT_MARKERS):
            return False
        return bool(text)

    def _message_has_result_or_error(self, message: Message) -> bool:
        return self._extract_result_url(message) is not None or self._message_has_error(message)

    def _message_has_error(self, message: Message) -> bool:
        normalized = self._normalize_text(message.raw_text or "")
        return any(
            keyword in normalized
            for keyword in (
                "assinatura ativa",
                "planos privados",
                "manutencao",
                "invalido",
                "nao encontrado",
                "nenhum resultado",
                "sem resultados",
            )
        )

    def _raise_if_bot_error(self, message: Message) -> None:
        self._raise_common_bot_errors(message.raw_text or "")