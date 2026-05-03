import asyncio
import re
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
        sent_message = await client.send_message(self.group_id, command)
        bot_reply = await self.wait_for_bot_reply(client, sent_message, timeout=settings.telegram_timeout)
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
            private_message = await asyncio.wait_for(private_future, timeout=settings.telegram_timeout)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"{self.name} não entregou o resultado no privado dentro de {settings.telegram_timeout} segundos."
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