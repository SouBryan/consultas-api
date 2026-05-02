import asyncio
import unicodedata
from collections.abc import Callable

from telethon import TelegramClient, events
from telethon.tl.custom.message import Message

from app.config import settings


RESULT_URL_FRAGMENT = "result-consultation"
TIMEOUT_SECONDS = 15


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
        raise TimeoutError("Bot não respondeu dentro de 15 segundos.") from exc
    finally:
        close_reply_waiter()

    _raise_if_bot_error(bot_reply)

    if base_button_text is not None:
        _ensure_button_exists(bot_reply, base_button_text)

        edit_future, close_edit_waiter = _create_edit_waiter(client, bot_reply.id)
        try:
            await bot_reply.click(text=base_button_text)
            updated_message = await asyncio.wait_for(edit_future, timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("Bot não editou a mensagem dentro de 15 segundos.") from exc
        finally:
            close_edit_waiter()
    else:
        existing_url = _extract_result_url(bot_reply)
        if existing_url is not None:
            return existing_url

        edit_future, close_edit_waiter = _create_edit_waiter(client, bot_reply.id)
        try:
            updated_message = await asyncio.wait_for(edit_future, timeout=TIMEOUT_SECONDS)
        except asyncio.TimeoutError as exc:
            raise TimeoutError("Bot não retornou o resultado dentro de 15 segundos.") from exc
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
        raise ValueError("A base selecionada exige assinatura ativa.")
    if "manutencao" in normalized:
        raise ValueError("O módulo consultado está em manutenção.")
    if "invalido" in normalized:
        raise ValueError(text or "O bot retornou input inválido.")
    if "nao encontrado" in normalized:
        raise ValueError(text or "Resultado não encontrado.")


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