import asyncio
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings


RESULT_URL_FRAGMENT = "result-consultation"
TIMEOUT_SECONDS = settings.telegram_timeout


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


async def execute_query(
    client: TelegramClient,
    command: str,
    base_button_text: str | None,
) -> str:
    reply_future, arm_reply_waiter, close_reply_waiter = _create_reply_waiter(client)

    try:
        sent_message = await client.send_message(settings.telegram_group_id, command)
        arm_reply_waiter(sent_message.id)
        bot_reply = await asyncio.wait_for(reply_future, timeout=TIMEOUT_SECONDS)
    except asyncio.TimeoutError as exc:
        raise TimeoutError(f"Bot não respondeu dentro de {TIMEOUT_SECONDS} segundos.") from exc
    finally:
        close_reply_waiter()

    _raise_if_bot_error(bot_reply)

    existing_url = _extract_result_url(bot_reply)
    if existing_url is not None:
        return existing_url

    if base_button_text is not None:
        _ensure_button_exists(bot_reply, base_button_text)

        follow_up_future, close_follow_up_waiter = _create_post_click_waiter(client, bot_reply.id)
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
        edit_future, close_edit_waiter = _create_edit_waiter(client, bot_reply.id)
        try:
            updated_message = await asyncio.wait_for(edit_future, timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TimeoutError(
                f"Bot não retornou o resultado dentro de {TIMEOUT_SECONDS} segundos."
            ) from exc
        finally:
            close_edit_waiter()

    _raise_if_bot_error(updated_message)

    result_url = _extract_result_url(updated_message)
    if result_url is None:
        raise ValueError("Não foi possível extrair a URL do resultado.")

    return result_url


def _create_reply_waiter(
    client: TelegramClient,
) -> tuple[asyncio.Future[Message], Callable[[int], None], Callable[[], None]]:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Message] = loop.create_future()
    target_message_id: dict[str, int | None] = {"value": None}
    event_builder = events.NewMessage(chats=settings.telegram_group_id)

    async def handler(event: events.NewMessage.Event) -> None:
        expected_message_id = target_message_id["value"]
        if expected_message_id is None:
            return

        message = event.message
        if _extract_reply_to_msg_id(message) != expected_message_id:
            return

        if not future.done():
            future.set_result(message)

    client.add_event_handler(handler, event_builder)

    def arm(message_id: int) -> None:
        target_message_id["value"] = message_id

    def close() -> None:
        client.remove_event_handler(handler, event_builder)

    return future, arm, close


def _create_edit_waiter(
    client: TelegramClient,
    message_id: int,
) -> tuple[asyncio.Future[Message], Callable[[], None]]:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Message] = loop.create_future()
    event_builder = events.MessageEdited(chats=settings.telegram_group_id)

    async def handler(event: events.MessageEdited.Event) -> None:
        message = event.message
        if message.id != message_id:
            return

        if _extract_result_url(message) is not None or _message_has_error(message):
            if not future.done():
                future.set_result(message)

    client.add_event_handler(handler, event_builder)

    def close() -> None:
        client.remove_event_handler(handler, event_builder)

    return future, close


def _create_post_click_waiter(
    client: TelegramClient,
    message_id: int,
) -> tuple[asyncio.Future[Message], Callable[[], None]]:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Message] = loop.create_future()
    new_message_builder = events.NewMessage(chats=settings.telegram_group_id)
    edited_message_builder = events.MessageEdited(chats=settings.telegram_group_id)

    async def on_new_message(event: events.NewMessage.Event) -> None:
        message = event.message
        if _extract_reply_to_msg_id(message) != message_id:
            return

        if _extract_result_url(message) is not None or _message_has_error(message):
            if not future.done():
                future.set_result(message)

    async def on_edited_message(event: events.MessageEdited.Event) -> None:
        message = event.message
        if message.id != message_id:
            return

        if _extract_result_url(message) is not None or _message_has_error(message):
            if not future.done():
                future.set_result(message)

    client.add_event_handler(on_new_message, new_message_builder)
    client.add_event_handler(on_edited_message, edited_message_builder)

    def close() -> None:
        client.remove_event_handler(on_new_message, new_message_builder)
        client.remove_event_handler(on_edited_message, edited_message_builder)

    return future, close


def _ensure_button_exists(message: Message, base_button_text: str) -> None:
    for row in message.buttons or []:
        for button in row:
            if button.text == base_button_text:
                return

    raise ValueError(f"Botão '{base_button_text}' não encontrado na resposta do bot.")


def _extract_result_url(message: Message) -> str | None:
    for row in message.buttons or []:
        for button in row:
            button_url = getattr(button, "url", None)
            if button_url and RESULT_URL_FRAGMENT in button_url:
                return button_url
    return None


def _extract_reply_to_msg_id(message: Message) -> int | None:
    direct_reply_id = getattr(message, "reply_to_msg_id", None)
    if direct_reply_id is not None:
        return direct_reply_id

    reply_header = getattr(message, "reply_to", None)
    return getattr(reply_header, "reply_to_msg_id", None)


def _raise_if_bot_error(message: Message) -> None:
    text = message.raw_text or ""
    normalized = _normalize_text(text)

    if "assinatura ativa" in normalized:
        raise BotResponseError("subscription", "Base requer assinatura")
    if "manutencao" in normalized:
        raise BotResponseError("maintenance", "Em manutenção")
    if "invalido" in normalized:
        raise BotResponseError("invalid", text or "O bot retornou input inválido.")
    if "nao encontrado" in normalized:
        raise BotResponseError("not_found", "Não encontrado")


def _message_has_error(message: Message) -> bool:
    normalized = _normalize_text(message.raw_text or "")
    return any(
        keyword in normalized
        for keyword in (
            "assinatura ativa",
            "manutencao",
            "invalido",
            "nao encontrado",
        )
    )


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))