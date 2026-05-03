import asyncio
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings
from app.services.adapters.base import BotAdapter, BotResponseError
from app.services.scraper import scrape_result


RESULT_URL_FRAGMENT = "result-consultation"
TIMEOUT_SECONDS = settings.telegram_timeout


@dataclass(frozen=True)
class CommandDefinition:
    template: str
    button_map: dict[str, str] = field(default_factory=dict)
    has_sub_base: bool = False


COMMAND_MAP: dict[str, CommandDefinition] = {
    "cpf": CommandDefinition(
        template="/cpf {input}",
        button_map={
            "completo": "CPF | COMPLETO",
            "fotos": "FOTOS",
            "vizinhos": "VIZINHOS",
            "empregos": "EMPREGOS",
            "vacinas": "VACINAS",
            "beneficios": "BENEFÍCIOS",
            "internet": "INTERNET",
            "obito": "ÓBITO",
            "compras": "COMPRAS",
        },
        has_sub_base=True,
    ),
    "nome": CommandDefinition(
        template="/nome {input}",
        button_map={
            "nome": "NOME",
            "nome_mae": "NOME DA MÃE",
        },
        has_sub_base=True,
    ),
    "telefone": CommandDefinition(
        template="/telefone {input}",
        button_map={
            "telefone": "TELEFONE",
        },
        has_sub_base=True,
    ),
    "email": CommandDefinition(
        template="/email {input}",
        button_map={
            "email": "EMAIL",
        },
        has_sub_base=True,
    ),
    "titulo": CommandDefinition(
        template="/titulo {input}",
        button_map={
            "titulo": "TÍTULO DE ELEITOR",
        },
        has_sub_base=True,
    ),
    "pix": CommandDefinition(
        template="/pix {input}",
        button_map={
            "pix": "PIX",
            "pix2": "PIX2",
        },
        has_sub_base=True,
    ),
    "cep": CommandDefinition(template="/cep {input}"),
    "ip": CommandDefinition(template="/ip {input}"),
}


class BlackConsultasAdapter(BotAdapter):
    name = "black_consultas"
    group_id = settings.telegram_group_id
    bot_username = "BlackConsultaasBot"
    supported_commands = ("cpf", "nome", "telefone", "email", "cep", "ip", "titulo", "pix")

    def __init__(
        self,
        scraper: Callable[[str], Awaitable[dict[str, Any]]] = scrape_result,
    ):
        self.group_id = settings.telegram_group_id
        self._scraper = scraper

    def supports(self, tipo: str, base: str | None = None) -> bool:
        if tipo not in self.supported_commands:
            return False

        definition = COMMAND_MAP[tipo]
        if not definition.has_sub_base:
            return base is None

        return base in definition.button_map

    async def execute(
        self,
        client: TelegramClient,
        tipo: str,
        input_data: str,
        base: str | None = None,
    ) -> dict[str, Any]:
        command = build_command(tipo, input_data)
        base_button_text = resolve_base_button_text(tipo, base)
        result_url = await self.execute_query(client, command, base_button_text)
        data = await self._scraper(result_url)
        return {
            "adapter": self.name,
            "link": result_url,
            "data": data,
        }

    async def execute_query(
        self,
        client: TelegramClient,
        command: str,
        base_button_text: str | None,
    ) -> str:
        sent_message = await self.send_group_message(client, command)
        bot_reply = await self.wait_for_bot_reply(client, sent_message, timeout=TIMEOUT_SECONDS)
        self._raise_if_bot_error(bot_reply)

        existing_url = self._extract_result_url(bot_reply)
        if existing_url is not None:
            return existing_url

        if base_button_text is not None:
            self._ensure_button_exists(bot_reply, base_button_text)

            follow_up_future, close_follow_up_waiter = self._create_post_click_waiter(client, bot_reply.id)
            try:
                await bot_reply.click(text=base_button_text)
                updated_message = await asyncio.wait_for(
                    follow_up_future,
                    timeout=TIMEOUT_SECONDS,
                )
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Bot não retornou o resultado dentro de {TIMEOUT_SECONDS} segundos."
                ) from exc
            finally:
                close_follow_up_waiter()
        else:
            edit_future, close_edit_waiter = self._create_edit_waiter(client, bot_reply.id)
            try:
                updated_message = await asyncio.wait_for(edit_future, timeout=TIMEOUT_SECONDS)
            except asyncio.TimeoutError as exc:
                raise TimeoutError(
                    f"Bot não retornou o resultado dentro de {TIMEOUT_SECONDS} segundos."
                ) from exc
            finally:
                close_edit_waiter()

        self._raise_if_bot_error(updated_message)

        result_url = self._extract_result_url(updated_message)
        if result_url is None:
            raise ValueError("Não foi possível extrair a URL do resultado.")

        return result_url

    def _create_edit_waiter(
        self,
        client: TelegramClient,
        message_id: int,
    ) -> tuple[asyncio.Future[Message], Callable[[], None]]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        event_builder = events.MessageEdited(chats=self.group_id)

        async def handler(event: events.MessageEdited.Event) -> None:
            message = event.message
            if message.id != message_id:
                return

            if self._extract_result_url(message) is not None or self._message_has_error(message):
                if not future.done():
                    future.set_result(message)

        client.add_event_handler(handler, event_builder)

        def close() -> None:
            client.remove_event_handler(handler, event_builder)

        return future, close

    def _create_post_click_waiter(
        self,
        client: TelegramClient,
        message_id: int,
    ) -> tuple[asyncio.Future[Message], Callable[[], None]]:
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Message] = loop.create_future()
        new_message_builder = events.NewMessage(chats=self.group_id)
        edited_message_builder = events.MessageEdited(chats=self.group_id)

        async def on_new_message(event: events.NewMessage.Event) -> None:
            message = event.message
            if self._extract_reply_to_msg_id(message) != message_id:
                return

            if self._extract_result_url(message) is not None or self._message_has_error(message):
                if not future.done():
                    future.set_result(message)

        async def on_edited_message(event: events.MessageEdited.Event) -> None:
            message = event.message
            if message.id != message_id:
                return

            if self._extract_result_url(message) is not None or self._message_has_error(message):
                if not future.done():
                    future.set_result(message)

        client.add_event_handler(on_new_message, new_message_builder)
        client.add_event_handler(on_edited_message, edited_message_builder)

        def close() -> None:
            client.remove_event_handler(on_new_message, new_message_builder)
            client.remove_event_handler(on_edited_message, edited_message_builder)

        return future, close

    def _ensure_button_exists(self, message: Message, base_button_text: str) -> None:
        for row in message.buttons or []:
            for button in row:
                if button.text == base_button_text:
                    return

        raise ValueError(f"Botão '{base_button_text}' não encontrado na resposta do bot.")

    def _extract_result_url(self, message: Message) -> str | None:
        for row in message.buttons or []:
            for button in row:
                button_url = getattr(button, "url", None)
                if button_url and RESULT_URL_FRAGMENT in button_url:
                    return button_url
        return None

    def _raise_if_bot_error(self, message: Message) -> None:
        self._raise_common_bot_errors(message.raw_text or "")

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
            )
        )


def build_command(command_type: str, query_input: str) -> str:
    try:
        definition = COMMAND_MAP[command_type]
    except KeyError as exc:
        raise ValueError(f"Tipo de consulta '{command_type}' não suportado.") from exc

    return definition.template.format(input=query_input)


def resolve_base_button_text(command_type: str, base: str | None) -> str | None:
    try:
        definition = COMMAND_MAP[command_type]
    except KeyError as exc:
        raise ValueError(f"Tipo de consulta '{command_type}' não suportado.") from exc

    if not definition.has_sub_base:
        if base is not None:
            raise ValueError(f"Tipo de consulta '{command_type}' não aceita base.")
        return None

    if base is None:
        raise ValueError(f"Tipo de consulta '{command_type}' exige base.")

    try:
        return definition.button_map[base]
    except KeyError as exc:
        raise ValueError(
            f"Base '{base}' inválida para o tipo de consulta '{command_type}'."
        ) from exc