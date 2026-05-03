import asyncio
import re
import time
from typing import Any

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings
from app.services.adapters.base import BotAdapter
from app.services.captcha_solver import CaptchaError, CaptchaSolver


FIELD_PATTERN = re.compile(r"^(?P<key>[^:]+):\s*(?P<value>.+)$")
PERSON_PATTERN = re.compile(r"^\s*(?P<index>\d+)\.\s+(?P<name>.+)$")
MORE_ITEMS_PATTERN = re.compile(r"^e mais\s+(?P<count>\d+)\s+(?P<label>.+)$", re.IGNORECASE)


class PrivateChatStartRequiredError(RuntimeError):
    pass


class WorkBotAdapter(BotAdapter):
    name = "work_bot"
    group_id = settings.group_tamaki
    bot_username = "WorkGrupoRBot"
    supported_commands = (
        "cpf",
        "nome",
        "telefone",
        "email",
        "titulo",
        "cep",
        "cnpj",
        "rg",
        "mae",
        "pai",
        "chave",
        "vizinhos",
        "parentes",
        "foto",
        "condutor",
        "placa",
        "proprietario",
        "pep",
        "cns",
        "frota",
        "processo_numero",
    )

    MODULE_MAP = {
        "cpf": "COMPLETA",
        "nome": "COMPLETA",
        "telefone": "COMPLETA",
        "email": "COMPLETA",
        "titulo": "COMPLETA",
        "cep": "COMPLETA",
        "cnpj": "COMPLETA",
        "rg": "BASEDATA",
        "mae": "BASEDATA",
        "pai": "BASEDATA",
        "chave": "BASEDATA",
        "vizinhos": "BASEDATA",
        "parentes": "BASEDATA",
        "foto": "PRO",
        "condutor": "PRO",
        "placa": "Proprietarios",
        "proprietario": "Proprietarios",
        "pep": "PEP",
        "cns": "BASEDATA",
        "frota": "Frota",
        "processo_numero": "PROCESSO",
    }

    _TRANSIENT_MARKERS = {"aguarde", "processando", "consultando"}
    _START_PRIVATE_MARKERS = {
        "voce precisa iniciar",
        "precisa iniciar",
        "inicie o chat no privado",
        "iniciar no privado",
    }

    def __init__(self, captcha_solver: CaptchaSolver | None = None):
        self.group_id = settings.group_tamaki
        self._captcha_solver = captcha_solver or CaptchaSolver()

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
        return await self._execute_internal(
            client,
            tipo,
            input_data,
            base,
            allow_private_start=True,
        )

    async def _execute_internal(
        self,
        client: TelegramClient,
        tipo: str,
        input_data: str,
        base: str | None,
        *,
        allow_private_start: bool,
    ) -> dict[str, Any]:
        if not self.supports(tipo, base):
            raise ValueError(f"Adapter '{self.name}' não suporta '{tipo}' com base '{base}'.")

        private_entity = await client.get_entity(self.bot_username)

        try:
            return await self._execute_once(client, private_entity, tipo, input_data)
        except PrivateChatStartRequiredError as exc:
            if not allow_private_start:
                raise RuntimeError("Chat privado do Work Bot ainda não foi iniciado.") from exc

            await self._start_private_chat(client, private_entity)
            return await self._execute_internal(
                client,
                tipo,
                input_data,
                base,
                allow_private_start=False,
            )

    async def _execute_once(
        self,
        client: TelegramClient,
        private_entity: Any,
        tipo: str,
        input_data: str,
    ) -> dict[str, Any]:
        command = f"/{tipo} {input_data}".strip()

        # Registra handler ANTES de enviar para evitar race condition
        # (Work Bot pode responder em <1s e sem reply_to)
        bot_entity_id = await self._resolve_bot_entity_id(client)
        collected, close_collector = self._setup_group_collector(client, bot_entity_id)

        try:
            sent_message = await client.send_message(self.group_id, command)
            module_reply = await self._await_group_reply(collected, sent_message, timeout=settings.telegram_timeout)
        finally:
            close_collector()

        if self._needs_private_start(module_reply.raw_text or ""):
            raise PrivateChatStartRequiredError(module_reply.raw_text or "")

        self._raise_if_bot_error(module_reply)
        module_name = self.MODULE_MAP[tipo]
        self._ensure_button_exists(module_reply, module_name)

        private_last_id = await self._get_last_private_message_id(client, private_entity)
        private_future, close_private_waiter = self._create_private_waiter(
            client,
            private_entity,
            private_last_id,
        )

        bot_entity_id = getattr(private_entity, "id", None)
        group_future, close_group_waiter = self._create_group_follow_up_waiter(
            client,
            bot_entity_id,
            related_reply_ids={sent_message.id, module_reply.id},
            editable_message_ids={module_reply.id},
        )

        try:
            await module_reply.click(text=module_name)
            group_follow_up = await asyncio.wait_for(group_future, timeout=settings.telegram_timeout)
            if self._needs_private_start(group_follow_up.raw_text or ""):
                raise PrivateChatStartRequiredError(group_follow_up.raw_text or "")

            self._raise_if_bot_error(group_follow_up)

            if self._is_captcha_challenge(group_follow_up):
                await self._handle_captcha_flow(
                    client,
                    group_follow_up,
                    bot_entity_id,
                    private_future,
                )

            if not private_future.done():
                private_message = await asyncio.wait_for(private_future, timeout=settings.telegram_timeout)
            else:
                private_message = private_future.result()

            # Work Bot pode pedir para escolher formato (PDF/TXT) antes de enviar o resultado
            private_message = await self._handle_format_selection(
                client, private_entity, private_message
            )
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"{self.name} não entregou o resultado no privado dentro de {settings.telegram_timeout} segundos."
            ) from exc
        finally:
            close_group_waiter()
            close_private_waiter()

        if self._needs_private_start(private_message.raw_text or ""):
            raise PrivateChatStartRequiredError(private_message.raw_text or "")

        self._raise_if_bot_error(private_message)

        data = self._parse_result_text(tipo, private_message.raw_text or "")
        if private_message.media is not None:
            data.setdefault("has_media", True)

        return {
            "adapter": self.name,
            "link": "",
            "data": data,
        }

    async def _handle_captcha_flow(
        self,
        client: TelegramClient,
        captcha_message: Message,
        bot_entity_id: int | None,
        private_future: asyncio.Future[Message],
    ) -> None:
        current_message = captcha_message

        for attempt in range(1, 3):
            if current_message.media is None:
                raise CaptchaError("Captcha recebido sem mídia para download.")

            options = self._extract_button_options(current_message)
            if not options:
                raise CaptchaError("Captcha recebido sem opções de resposta.")

            image_bytes = await client.download_media(current_message.media, bytes)
            if not isinstance(image_bytes, (bytes, bytearray)):
                raise CaptchaError("Falha ao baixar a imagem do captcha.")

            answer = await self._captcha_solver.solve(bytes(image_bytes), options)

            group_future, close_group_waiter = self._create_group_follow_up_waiter(
                client,
                bot_entity_id,
                related_reply_ids={current_message.id},
                editable_message_ids={current_message.id},
            )

            try:
                await current_message.click(text=answer)
                done, _ = await asyncio.wait(
                    {private_future, group_future},
                    timeout=settings.telegram_timeout,
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                close_group_waiter()

            if private_future in done:
                return

            if group_future in done:
                next_message = group_future.result()
                if self._needs_private_start(next_message.raw_text or ""):
                    raise PrivateChatStartRequiredError(next_message.raw_text or "")

                self._raise_if_bot_error(next_message)
                if self._is_captcha_challenge(next_message):
                    current_message = next_message
                    continue
                return

            raise TimeoutError(
                f"{self.name} não respondeu após o envio da solução do captcha."
            )

        raise CaptchaError("Captcha falhou após 2 tentativas no Work Bot.")

    def _create_group_follow_up_waiter(
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

            if not self._is_actionable_group_message(message):
                return

            if not future.done():
                future.set_result(message)

        async def on_edited_message(event: events.MessageEdited.Event) -> None:
            message = event.message
            if bot_entity_id is not None and message.sender_id != bot_entity_id:
                return

            if message.id not in editable_message_ids:
                return

            if not self._is_actionable_group_message(message):
                return

            if not future.done():
                future.set_result(message)

        client.add_event_handler(on_new_message, new_event)
        client.add_event_handler(on_edited_message, edit_event)

        def close() -> None:
            client.remove_event_handler(on_new_message, new_event)
            client.remove_event_handler(on_edited_message, edit_event)

        return future, close

    def _create_private_waiter(
        self,
        client: TelegramClient,
        private_entity: Any,
        min_message_id: int,
    ) -> tuple[asyncio.Future[Message], Any]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        event_builder = events.NewMessage(chats=private_entity)

        async def handler(event: events.NewMessage.Event) -> None:
            message = event.message
            if message.id <= min_message_id:
                return

            text = (message.raw_text or "").strip()
            if not text and message.media is None:
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

    async def _handle_format_selection(
        self,
        client: TelegramClient,
        private_entity: Any,
        message: Message,
    ) -> Message:
        """Se o bot pedir para escolher formato (PDF/TXT), clica em TXT e aguarda o resultado real."""
        text_lower = (message.raw_text or "").lower()
        if "formato do resultado" not in text_lower:
            return message

        # Clica no botão TXT para receber em texto
        try:
            await message.click(text="📝 TXT")
        except Exception:
            # Tenta pelo index caso texto não bata
            await message.click(1)

        # Aguarda a próxima mensagem privada (resultado real)
        result_msg = None
        deadline = time.monotonic() + settings.telegram_timeout
        while time.monotonic() < deadline:
            msgs = await client.get_messages(private_entity, min_id=message.id, limit=5)
            for m in msgs:
                if m.id > message.id and (m.raw_text or "").strip():
                    result_msg = m
                    break
            if result_msg:
                break
            await asyncio.sleep(0.5)

        if result_msg is None:
            raise TimeoutError(
                f"{self.name} não entregou o resultado após seleção de formato."
            )
        return result_msg

    async def _resolve_bot_entity_id(self, client: TelegramClient) -> int | None:
        try:
            entity = await client.get_entity(self.bot_username)
            return entity.id
        except Exception:
            return None

    def _setup_group_collector(
        self, client: TelegramClient, bot_entity_id: int | None
    ) -> tuple[list[Message], Any]:
        """Registra handlers para coletar mensagens do bot no grupo (NewMessage + Edited)."""
        collected: list[Message] = []
        new_event = events.NewMessage(chats=self.group_id)
        edit_event = events.MessageEdited(chats=self.group_id)

        async def handler(event) -> None:
            message = event.message
            is_from_bot = bot_entity_id and message.sender_id == bot_entity_id
            if is_from_bot:
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
        """Poll collected messages for a meaningful bot reply."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for msg in collected:
                if msg.id <= sent_msg.id:
                    continue
                text_lower = (msg.raw_text or "").lower()
                if any(m in text_lower for m in self._TRANSIENT_MARKERS) and not msg.buttons:
                    continue
                return msg
            await asyncio.sleep(0.3)

        raise TimeoutError(f"{self.name} não respondeu dentro de {timeout} segundos.")

    def _ensure_button_exists(self, message: Message, button_text: str) -> None:
        target = self._normalize_text(button_text)
        for row in message.buttons or []:
            for button in row:
                if self._normalize_text(button.text or "") == target:
                    return

        raise ValueError(f"Botão '{button_text}' não encontrado na resposta do Work Bot.")

    def _extract_button_options(self, message: Message) -> list[str]:
        options: list[str] = []
        for row in message.buttons or []:
            for button in row:
                text = (button.text or "").strip()
                if text and text not in options:
                    options.append(text)
        return options

    def _is_actionable_group_message(self, message: Message) -> bool:
        text = message.raw_text or ""
        normalized = self._normalize_text(text)

        if message.media is not None:
            return True
        if message.buttons:
            return True
        if any(marker in normalized for marker in self._TRANSIENT_MARKERS):
            return False
        return bool(text.strip())

    def _is_captcha_challenge(self, message: Message) -> bool:
        normalized = self._normalize_text(message.raw_text or "")
        return message.media is not None or "captcha" in normalized

    def _needs_private_start(self, text: str) -> bool:
        normalized = self._normalize_text(text)
        return any(marker in normalized for marker in self._START_PRIVATE_MARKERS)

    def _raise_if_bot_error(self, message: Message) -> None:
        self._raise_common_bot_errors(message.raw_text or "")

    def _parse_result_text(self, tipo: str, text: str) -> dict[str, Any]:
        parsed = self._parse_people_result(tipo, text)
        if parsed:
            return parsed
        return {"raw_text": text.strip()}

    def _parse_people_result(self, tipo: str, text: str) -> dict[str, Any]:
        result = self._parse_generic_result(text)
        collection_key_map = {
            "cep": "moradores",
            "vizinhos": "vizinhos",
            "parentes": "parentes",
        }
        collection_key = collection_key_map.get(tipo, "registros")

        items: list[dict[str, Any]] = []
        current_item: dict[str, Any] | None = None

        for raw_line in text.splitlines():
            line = self._clean_line(raw_line)
            if not line:
                current_item = None
                continue

            if self._is_divider(line):
                continue

            stripped = self._strip_leading_emoji(line)
            person_match = PERSON_PATTERN.match(stripped)
            if person_match is not None:
                current_item = {"nome": person_match.group("name").strip()}
                items.append(current_item)
                continue

            more_items_match = MORE_ITEMS_PATTERN.match(self._normalize_text(stripped))
            if more_items_match is not None:
                result["itens_ocultos"] = int(more_items_match.group("count"))
                continue

            if current_item is None:
                continue

            field = self._extract_field(line)
            if field is None:
                continue

            key, value = field
            self._assign_value(current_item, key, value)

        if items:
            result[collection_key] = items

        return result

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

    def _clean_line(self, line: str) -> str:
        return line.replace("\xa0", " ").strip()

    def _is_divider(self, line: str) -> bool:
        return all(char in {"━", "─", "-"} for char in line)

    def _strip_leading_emoji(self, line: str) -> str:
        return re.sub(r"^[^\w\d]+", "", line).strip()

    def _assign_value(self, container: dict[str, Any], key: str, value: str) -> None:
        existing = container.get(key)
        if existing is None:
            container[key] = value
            return

        if isinstance(existing, list):
            existing.append(value)
            return

        container[key] = [existing, value]