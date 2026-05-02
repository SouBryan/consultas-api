import re
import unicodedata
from urllib.parse import parse_qs, urlparse
from typing import Any

import httpx
from selectolax.parser import HTMLParser


FIELD_PATTERN = re.compile(r"^[•\-]\s*(?P<key>[^:]+):\s*(?P<value>.+)$")


async def scrape_result(url: str) -> dict[str, Any]:
    async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as client:
        api_payload = await _fetch_consultation_payload(client, url)
        if api_payload is not None:
            return _parse_payload(api_payload)

        response = await client.get(url)
        response.raise_for_status()

    parser = HTMLParser(response.text)
    raw_text = _extract_result_text(parser)
    parsed = parse_consultation_text(raw_text)

    if parsed:
        return parsed

    return {"raw_text": raw_text.strip()}


def parse_consultation_text(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current_section_key: str | None = None
    current_item: dict[str, Any] | None = None

    for raw_line in text.splitlines():
        line = _clean_line(raw_line)
        if not line:
            continue

        parsed_field = _parse_field_line(line)
        if parsed_field is not None:
            key, value = parsed_field
            if _normalize_text(value) == "sem informacao":
                continue
            target_container = _get_target_container(result, current_section_key, current_item)
            _assign_value(target_container, key, value)
            continue

        if _looks_like_section_header(line):
            header_key = _slugify(line)
            if not header_key or header_key.startswith("consulta"):
                continue

            if _is_repeated_item_header(header_key):
                collection_key = _collection_key_for_item(header_key)
                items = result.setdefault(collection_key, [])
                if not isinstance(items, list):
                    items = []
                    result[collection_key] = items
                current_section_key = collection_key
                current_item = {}
                items.append(current_item)
                continue

            current_section_key = header_key
            current_item = None

            if header_key in {"enderecos", "beneficios_sociais"}:
                result.setdefault(header_key, [])
            else:
                result.setdefault(header_key, {})

    cleaned_result = _prune_empty_values(result)
    return cleaned_result if isinstance(cleaned_result, dict) else {}


async def _fetch_consultation_payload(
    client: httpx.AsyncClient,
    result_url: str,
) -> dict[str, Any] | None:
    api_url = _build_api_url(result_url)
    if api_url is None:
        return None

    response = await client.get(api_url)
    response.raise_for_status()
    payload = response.json()

    data = payload.get("data")
    if isinstance(data, dict):
        return data

    return None


def _build_api_url(result_url: str) -> str | None:
    parsed_url = urlparse(result_url)
    path_parts = [part for part in parsed_url.path.split("/") if part]
    if len(path_parts) < 2 or path_parts[0] != "result-consultation":
        return None

    slug = path_parts[1]
    bot_values = parse_qs(parsed_url.query, keep_blank_values=True).get("bot", [])
    api_url = f"https://api.blackconsultas.com/consultation/{slug}"

    if bot_values:
        bot_value = bot_values[0]
        return f"{api_url}?bot={bot_value}"

    return api_url


def _parse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    content = payload.get("content")

    if isinstance(content, str) and content.strip():
        parsed_content = parse_consultation_text(content)
        if parsed_content:
            result.update(parsed_content)
        else:
            result["raw_text"] = content.strip()

    for extra_key in ("photo",):
        extra_value = payload.get(extra_key)
        if extra_value is not None:
            result[extra_key] = extra_value

    return result


def _extract_result_text(parser: HTMLParser) -> str:
    selectors = (
        "code",
        "pre",
        ".result-content",
        "[class*='result']",
    )

    for selector in selectors:
        for node in parser.css(selector):
            text = node.text(separator="\n").strip()
            if "•" in text or ":" in text:
                return text

    if parser.body is not None:
        return parser.body.text(separator="\n")

    return parser.html.strip()


def _parse_field_line(line: str) -> tuple[str, str] | None:
    candidate = line
    bullet_index = candidate.find("•")
    if bullet_index != -1:
        candidate = candidate[bullet_index:]

    match = FIELD_PATTERN.match(candidate)
    if match is None and ":" in line:
        key, _, value = line.partition(":")
        match_key = key.strip()
        match_value = value.strip()
    elif match is not None:
        match_key = match.group("key").strip()
        match_value = match.group("value").strip()
    else:
        return None

    if not match_key or not match_value:
        return None

    return _slugify(match_key), match_value


def _looks_like_section_header(line: str) -> bool:
    if ":" in line or "•" in line:
        return False

    cleaned = _strip_visual_markers(line)
    if len(cleaned) < 3:
        return False

    has_letter = any(char.isalpha() for char in cleaned)
    return has_letter


def _clean_line(line: str) -> str:
    return line.replace("\xa0", " ").strip()


def _get_target_container(
    result: dict[str, Any],
    current_section_key: str | None,
    current_item: dict[str, Any] | None,
) -> dict[str, Any]:
    if current_item is not None:
        return current_item

    if current_section_key is None:
        return result

    section = result.setdefault(current_section_key, {})
    if isinstance(section, list):
        if not section:
            section.append({})
        last_item = section[-1]
        if isinstance(last_item, dict):
            return last_item
        section.append({})
        return section[-1]

    return section


def _is_repeated_item_header(header_key: str) -> bool:
    return header_key.startswith("endereco_") or header_key.startswith("beneficio_")


def _collection_key_for_item(header_key: str) -> str:
    if header_key.startswith("endereco_"):
        return "enderecos"
    if header_key.startswith("beneficio_"):
        return "beneficios_sociais"
    return header_key


def _strip_visual_markers(value: str) -> str:
    return "".join(
        char if char.isalnum() or char.isspace() else " "
        for char in value
    ).strip()


def _slugify(value: str) -> str:
    normalized = _normalize_text(_strip_visual_markers(value))
    normalized = normalized.replace("/", " ")
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^a-z0-9_]", "", normalized)
    return normalized.strip("_")


def _normalize_text(value: str) -> str:
    decomposed = unicodedata.normalize("NFKD", value.casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def _assign_value(container: dict[str, Any], key: str, value: str) -> None:
    existing = container.get(key)
    if existing is None:
        container[key] = value
        return

    if isinstance(existing, list):
        existing.append(value)
        return

    container[key] = [existing, value]


def _prune_empty_values(value: Any) -> Any:
    if isinstance(value, dict):
        cleaned_dict = {
            key: cleaned_value
            for key, raw_value in value.items()
            if (cleaned_value := _prune_empty_values(raw_value)) not in ({}, [], None, "")
        }
        return cleaned_dict

    if isinstance(value, list):
        cleaned_list = [
            cleaned_value
            for item in value
            if (cleaned_value := _prune_empty_values(item)) not in ({}, [], None, "")
        ]
        return cleaned_list

    return value