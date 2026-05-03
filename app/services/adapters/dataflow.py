import asyncio
import re
import time
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings
from app.services.adapters.base import BotAdapter, BotResponseError


FIELD_PATTERN = re.compile(r"^(?P<key>[^:]+):\s*(?P<value>.+)$")
PERSON_PATTERN = re.compile(r"^\s*(?P<index>\d+)\.\s+(?P<name>.+)$")
MORE_ITEMS_PATTERN = re.compile(r"^e mais\s+(?P<count>\d+)\s+(?P<label>.+)$", re.IGNORECASE)


class DataFlowAdapter(BotAdapter):
    name = "dataflow"
    group_id = settings.group_dataflow
    bot_username = "wmhrbeiyyjnbot"
    supported_commands = (
        "cpf",
        "nome",
        "telefone",
        "email",
        "cep",
        "cnpj",
        "titulo",
        "bin",
        "endereco",
        "mae",
        "foto",
    )

    INLINE_GROUP_COMMANDS = {"bin"}

    def __init__(self):
        self.group_id = settings.group_dataflow

    _TRANSIENT_MARKERS = {"processando", "aguarde"}

    async def _setup_group_waiter(
        self, client: TelegramClient
    ) -> tuple[list[Message], Any]:
        """Registra handler para coletar mensagens do bot no grupo.

        Captura tanto mensagens novas quanto edições (bot edita "Processando..." → resultado).
        """
        collected: list[Message] = []
        new_event = events.NewMessage(chats=self.group_id)
        edit_event = events.MessageEdited(chats=self.group_id)

        bot_entity_id: int | None = None
        try:
            entity = await client.get_entity(self.bot_username)
            bot_entity_id = entity.id
        except Exception:
            pass

        async def handler(event) -> None:
            message = event.message
            is_from_bot = bot_entity_id and message.sender_id == bot_entity_id
            if is_from_bot:
                # Para edits, atualiza a msg existente na lista
                for i, existing in enumerate(collected):
                    if existing.id == message.id:
                        collected[i] = message
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
        sent_msg: Message,
        timeout: int = 15,
    ) -> Message:
        """Aguarda a resposta do bot verificando periodicamente as mensagens coletadas."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in collected:
                if msg.id <= sent_msg.id:
                    continue
                # Ignorar mensagens transitórias (ex: "Processando...", "Consultando...")
                text_lower = (msg.raw_text or "").lower()
                if any(m in text_lower for m in self._TRANSIENT_MARKERS) and not msg.buttons:
                    continue
                # Qualquer msg definitiva do bot (com ou sem reply_to)
                return msg
            await asyncio.sleep(0.3)

        raise TimeoutError(f"{self.name} não respondeu dentro de {timeout} segundos.")

    async def wait_for_bot_reply(
        self,
        client: TelegramClient,
        sent_msg: Message,
        timeout: int = 15,
    ) -> Message:
        """Fallback — não usado diretamente no execute mas mantido por compatibilidade."""
        collected, close = await self._setup_group_waiter(client)
        try:
            return await self._await_group_reply(collected, sent_msg, timeout=timeout)
        finally:
            close()

    def supports(self, tipo: str, base: str | None = None) -> bool:
        if tipo not in self.supported_commands:
            return False

        default_bases = {
            "cpf": {None, "completo"},
            "nome": {None, "nome"},
            "telefone": {None, "telefone"},
            "email": {None, "email"},
            "titulo": {None, "titulo"},
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

        # Registra handler ANTES de enviar para evitar race condition
        bot_reply_future, close_reply = await self._setup_group_waiter(client)
        try:
            sent_message = await self.send_group_message(client, command)
            bot_reply = await self._await_group_reply(
                bot_reply_future, sent_message, timeout=settings.telegram_timeout
            )
        finally:
            close_reply()

        self._raise_if_bot_error(bot_reply)

        if tipo in self.INLINE_GROUP_COMMANDS or not bot_reply.buttons:
            data = self._parse_result_text(tipo, bot_reply.raw_text or "")
            return {
                "adapter": self.name,
                "link": "",
                "data": data,
            }

        private_entity = await client.get_entity(self.bot_username)
        private_future, close_private_waiter = self._create_private_waiter(client, private_entity)

        try:
            await self._click_private_button(bot_reply)
            # DataFlow pode levar até 30s para consultar e enviar o resultado no privado
            private_timeout = max(settings.telegram_timeout * 2, 30)
            private_message = await asyncio.wait_for(private_future, timeout=private_timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"{self.name} não entregou o resultado no privado dentro de {private_timeout} segundos."
            ) from exc
        finally:
            close_private_waiter()

        self._raise_if_bot_error(private_message)

        data = self._parse_result_text(tipo, private_message.raw_text or "")
        if private_message.media is not None:
            data.setdefault("has_media", True)

        return {
            "adapter": self.name,
            "link": "",
            "data": data,
        }

    def _create_private_waiter(
        self,
        client: TelegramClient,
        private_entity: Any,
    ) -> tuple[asyncio.Future[Message], Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        event_builder = events.NewMessage(chats=private_entity)

        async def handler(event: events.NewMessage.Event) -> None:
            message = event.message
            text = (message.raw_text or "").strip()
            if not text and message.media is None:
                return

            if not future.done():
                future.set_result(message)

        client.add_event_handler(handler, event_builder)

        def close() -> None:
            client.remove_event_handler(handler, event_builder)

        return future, close

    async def _click_private_button(self, message: Message) -> None:
        normalized_target = "ver no privado"

        for row in message.buttons or []:
            for button in row:
                if normalized_target in self._normalize_text(button.text or ""):
                    await button.click()
                    return

        if message.buttons and message.buttons[0]:
            await message.buttons[0][0].click()
            return

        raise ValueError("Botão 'Ver no Privado' não encontrado na resposta do DataFlow.")

    def _parse_result_text(self, tipo: str, text: str) -> dict[str, Any]:
        parsed = self._parse_cep_result(text) if tipo == "cep" else self._parse_generic_result(text)
        if parsed:
            return parsed
        return {"raw_text": text.strip()}

    def _parse_generic_result(self, text: str) -> dict[str, Any]:
        result: dict[str, Any] = {}

        for raw_line in text.splitlines():
            line = self._clean_line(raw_line)
            if not line or self._is_divider(line):
                continue

            field = self._extract_field(line)
            if field is None:
                if line.upper().startswith("CONSULTA "):
                    result["consulta"] = line.split(None, 1)[1].strip().lower()
                continue

            key, value = field
            if self._normalize_text(value) == "sem informacao":
                continue
            self._assign_value(result, key, value)

        return result

    def _parse_cep_result(self, text: str) -> dict[str, Any]:
        result = self._parse_generic_result(text)
        moradores: list[dict[str, Any]] = []
        current_morador: dict[str, Any] | None = None

        for raw_line in text.splitlines():
            line = self._clean_line(raw_line)
            if not line:
                current_morador = None
                continue

            if self._is_divider(line):
                continue

            stripped = self._strip_leading_emoji(line)
            person_match = PERSON_PATTERN.match(stripped)
            if person_match is not None:
                current_morador = {
                    "nome": person_match.group("name").strip(),
                }
                moradores.append(current_morador)
                continue

            more_items_match = MORE_ITEMS_PATTERN.match(self._normalize_text(stripped))
            if more_items_match is not None:
                result["itens_ocultos"] = int(more_items_match.group("count"))
                continue

            if current_morador is None:
                continue

            field = self._extract_field(line)
            if field is None:
                continue

            key, value = field
            self._assign_value(current_morador, key, value)

        if moradores:
            result["moradores"] = moradores

        return result

    def _extract_field(self, line: str) -> tuple[str, str] | None:
        candidate = self._strip_leading_emoji(line)
        match = FIELD_PATTERN.match(candidate)
        if match is None:
            return None

        key = self._slugify(match.group("key"))
        value = match.group("value").strip()
        if not key or not value:
            return None

        return key, value

    def _raise_if_bot_error(self, message: Message) -> None:
        self._raise_common_bot_errors(message.raw_text or "")

    def _clean_line(self, line: str) -> str:
        return line.replace("\xa0", " ").strip()

    def _is_divider(self, line: str) -> bool:
        return all(char in {"━", "─", "-"} for char in line)

    def _strip_leading_emoji(self, line: str) -> str:
        cleaned = re.sub(r"^[^\w\d]+", "", line).strip()
        return cleaned

    def _assign_value(self, container: dict[str, Any], key: str, value: str) -> None:
        existing = container.get(key)
        if existing is None:
            container[key] = value
            return

        if isinstance(existing, list):
            existing.append(value)
            return

        container[key] = [existing, value]