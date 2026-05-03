import base64
import asyncio
import time
from typing import Any

import httpx

from app.config import settings
from app.utils.logger import get_logger

logger = get_logger("services.captcha_solver")

VOIDAI_URL = "https://api.voidai.app/v1/chat/completions"
MAX_RETRIES = 2


class CaptchaError(Exception):
    """Raised when captcha cannot be solved after retries."""


class CaptchaSolver:
    def __init__(self) -> None:
        self._api_key = settings.voidai_api_key
        self._model = settings.voidai_model or "gemini-2.0-flash"

    async def solve(self, image_bytes: bytes, options: list[str]) -> str:
        if not self._api_key.strip():
            raise CaptchaError("VOIDAI_API_KEY não configurada.")
        if not options:
            raise CaptchaError("Nenhuma opção de captcha foi recebida.")

        b64 = base64.b64encode(image_bytes).decode()
        data_uri = f"data:image/jpeg;base64,{b64}"
        options_str = ", ".join(options)

        payload = {
            "model": self._model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": (
                                f"Qual texto alfanumerico esta na imagem? "
                                f"Opcoes: {options_str}. "
                                f"Responda APENAS a opcao correta, nada mais."
                            ),
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        },
                    ],
                }
            ],
            "max_tokens": self._resolve_max_tokens(),
            "temperature": 0,
        }

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(1, MAX_RETRIES + 1):
            started = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(VOIDAI_URL, json=payload, headers=headers)
                    resp.raise_for_status()

                elapsed = round((time.monotonic() - started) * 1000, 1)
                data = resp.json()
                answer = self._extract_answer(data)

                matched = self._match_option(answer, options)
                if matched:
                    logger.info(
                        "Captcha resolvido.",
                        extra={
                            "event": "captcha_solved",
                            "answer": matched,
                            "attempt": attempt,
                            "duration_ms": elapsed,
                            "model": self._model,
                        },
                    )
                    return matched

                logger.warning(
                    "Resposta do solver não corresponde a nenhuma opção.",
                    extra={
                        "event": "captcha_mismatch",
                        "answer": answer,
                        "options": options,
                        "attempt": attempt,
                        "duration_ms": elapsed,
                        "model": self._model,
                    },
                )
            except (httpx.HTTPStatusError, httpx.TimeoutException, ValueError, KeyError) as exc:
                elapsed = round((time.monotonic() - started) * 1000, 1)
                logger.warning(
                    "Erro ao resolver captcha.",
                    extra={
                        "event": "captcha_error",
                        "error": str(exc),
                        "attempt": attempt,
                        "duration_ms": elapsed,
                        "model": self._model,
                    },
                )

            if attempt < MAX_RETRIES:
                await asyncio.sleep(1)

        raise CaptchaError(
            f"Captcha não resolvido após {MAX_RETRIES} tentativas. Opções: {options}"
        )

    @staticmethod
    def _extract_answer(payload: dict[str, Any]) -> str:
        choice = payload.get("choices", [{}])[0]
        message = choice.get("message", {})
        content = message.get("content", "")

        if isinstance(content, str):
            return content.strip()

        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    text = item.get("text", "")
                    if isinstance(text, str) and text.strip():
                        parts.append(text.strip())
            return " ".join(parts).strip()

        return ""

    @staticmethod
    def _match_option(answer: str, options: list[str]) -> str | None:
        answer_upper = answer.upper().strip()
        for opt in options:
            if opt.upper().strip() == answer_upper:
                return opt
        for opt in options:
            if opt.upper().strip() in answer_upper:
                return opt
        return None

    def _resolve_max_tokens(self) -> int:
        model_name = self._model.lower()
        if "2.0-flash" in model_name:
            return 20
        return 500
