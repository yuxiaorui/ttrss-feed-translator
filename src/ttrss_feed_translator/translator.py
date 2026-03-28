from __future__ import annotations

from dataclasses import dataclass
import json
import logging
from typing import Iterable

import requests

from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.tags import extract_text_for_tagging, merge_tags


logger = logging.getLogger(__name__)
TAGGING_SOURCE_TEXT_LIMIT = 4000


class TranslationError(RuntimeError):
    pass


@dataclass(frozen=True)
class TagGenerationRequest:
    title: str
    content: str
    existing_tags: tuple[str, ...]
    max_total_tags: int
    language: str


class OpenAICompatibleTranslator:
    def __init__(self, config: AppConfig):
        self._api_base_url = config.api_base_url
        self._api_key = config.api_key
        self._model = config.model
        self._timeout = config.request_timeout_seconds
        self._target_language = config.target_language
        self._source_langs = config.source_langs
        self._max_texts = config.max_texts_per_request
        self._max_chars = config.max_chars_per_request
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            }
        )

    def translate_texts(self, texts: list[str]) -> list[str]:
        if not texts:
            return []

        translated: list[str] = []
        for chunk in self._chunk_texts(texts):
            translated.extend(self._translate_chunk(chunk))
        return translated

    def generate_tags(
        self,
        *,
        title: str,
        content: str,
        existing_tags: tuple[str, ...],
        max_total_tags: int,
        language: str,
    ) -> list[str]:
        return self.generate_tags_batch(
            [
                TagGenerationRequest(
                    title=title,
                    content=content,
                    existing_tags=existing_tags,
                    max_total_tags=max_total_tags,
                    language=language,
                )
            ]
        )[0]

    def generate_tags_batch(self, requests: list[TagGenerationRequest]) -> list[list[str]]:
        if not requests:
            return []

        results: list[list[str] | None] = [None] * len(requests)
        prepared_requests: list[_PreparedTagGenerationRequest] = []

        for index, request in enumerate(requests):
            prepared = _prepare_tag_generation_request(index, request)
            if prepared is None:
                results[index] = []
                continue
            prepared_requests.append(prepared)

        for chunk in self._chunk_tag_generation_requests(prepared_requests):
            generated_chunk = self._generate_tags_chunk(chunk)
            if len(generated_chunk) != len(chunk):
                raise TranslationError(
                    f"translator returned {len(generated_chunk)} tag sets for {len(chunk)} requests"
                )

            for prepared, generated in zip(chunk, generated_chunk, strict=True):
                results[prepared.index] = _normalize_generated_tags(
                    generated,
                    existing_tags=prepared.request.existing_tags,
                    remaining_slots=prepared.remaining_slots,
                )

        return [result if result is not None else [] for result in results]

    def _chunk_texts(self, texts: list[str]) -> Iterable[list[str]]:
        chunk: list[str] = []
        char_count = 0

        for text in texts:
            next_size = char_count + len(text)
            if chunk and (len(chunk) >= self._max_texts or next_size > self._max_chars):
                yield chunk
                chunk = []
                char_count = 0

            chunk.append(text)
            char_count += len(text)

        if chunk:
            yield chunk

    def _chunk_tag_generation_requests(
        self,
        requests: list["_PreparedTagGenerationRequest"],
    ) -> Iterable[list["_PreparedTagGenerationRequest"]]:
        chunk: list[_PreparedTagGenerationRequest] = []
        char_count = 0

        for request in requests:
            next_size = char_count + request.payload_size
            if chunk and (len(chunk) >= self._max_texts or next_size > self._max_chars):
                yield chunk
                chunk = []
                char_count = 0

            chunk.append(request)
            char_count += request.payload_size

        if chunk:
            yield chunk

    def _translate_chunk(self, texts: list[str]) -> list[str]:
        source_hint = ", ".join(self._source_langs) if self._source_langs else "auto-detect source language"
        parsed = self._request_string_array(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a translation engine. "
                        f"Translate each input string from {source_hint} to {self._target_language}. "
                        "Return JSON only. The output must be a JSON array of strings with exactly the same length "
                        "and order as the input array. Do not add markdown fences or commentary."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(texts, ensure_ascii=False),
                },
            ]
        )
        if len(parsed) != len(texts):
            raise TranslationError(
                f"translator returned {len(parsed)} items for {len(texts)} source texts"
            )

        logger.debug("translated %s text nodes", len(texts))
        return parsed

    def _generate_tags_chunk(self, requests: list["_PreparedTagGenerationRequest"]) -> list[list[str]]:
        parsed = self._request_string_matrix(
            [
                {
                    "role": "system",
                    "content": (
                        "You are an RSS tagging engine. "
                        "For each input object, generate up to that object's max_new_tags additional article tags "
                        "in that object's tag_language. "
                        "Return JSON only. The output must be a JSON array where each item is a JSON array of "
                        "strings. The outer array must have exactly the same length and order as the input array. "
                        "Prefer concrete people, companies, products, places, technologies, and themes. "
                        "Avoid generic tags like news, latest, article, update, and analysis. "
                        "Avoid repeating any existing tags. Keep tags concise."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps([request.payload for request in requests], ensure_ascii=False),
                },
            ]
        )
        logger.debug("generated ai tags for %s articles", len(requests))
        return parsed

    def _request_string_array(self, messages: list[dict[str, str]]) -> list[str]:
        parsed = self._request_json(messages)
        return _parse_string_array_payload(parsed)

    def _request_string_matrix(self, messages: list[dict[str, str]]) -> list[list[str]]:
        parsed = self._request_json(messages)
        return _parse_string_matrix_payload(parsed)

    def _request_json(self, messages: list[dict[str, str]]) -> object:
        url = f"{self._api_base_url}/chat/completions"
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": messages,
        }

        response = self._session.post(url, json=payload, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError(f"unexpected response payload: {data}") from exc

        return _parse_json_payload(content)


@dataclass(frozen=True)
class _PreparedTagGenerationRequest:
    index: int
    request: TagGenerationRequest
    payload: dict[str, object]
    payload_size: int
    remaining_slots: int


def _prepare_tag_generation_request(
    index: int,
    request: TagGenerationRequest,
) -> _PreparedTagGenerationRequest | None:
    remaining_slots = request.max_total_tags - len(request.existing_tags)
    if remaining_slots <= 0:
        return None

    payload = {
        "title": request.title,
        "content_excerpt": extract_text_for_tagging(request.content, max_chars=TAGGING_SOURCE_TEXT_LIMIT),
        "existing_tags": list(request.existing_tags),
        "max_new_tags": remaining_slots,
        "tag_language": request.language,
    }
    return _PreparedTagGenerationRequest(
        index=index,
        request=request,
        payload=payload,
        payload_size=len(json.dumps(payload, ensure_ascii=False)),
        remaining_slots=remaining_slots,
    )


def _normalize_generated_tags(
    generated: list[str],
    *,
    existing_tags: tuple[str, ...],
    remaining_slots: int,
) -> list[str]:
    normalized = merge_tags((), generated, limit=remaining_slots)
    filtered = [
        tag for tag in normalized if len(merge_tags(existing_tags, (tag,))) > len(existing_tags)
    ]
    logger.debug("generated %s ai tags", len(filtered))
    return filtered


def _parse_json_payload(content: str) -> object:
    cleaned = content.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    return json.loads(cleaned)


def _parse_string_array_payload(content: object) -> list[str]:
    parsed = content
    if isinstance(parsed, dict) and "translations" in parsed:
        parsed = parsed["translations"]

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise TranslationError("translation response is not a JSON string array")

    return list(parsed)


def _parse_string_matrix_payload(content: object) -> list[list[str]]:
    parsed = content
    if isinstance(parsed, dict):
        for key in ("tags", "results", "items"):
            if key in parsed:
                parsed = parsed[key]
                break

    if not isinstance(parsed, list) or not all(
        isinstance(item, list) and all(isinstance(tag, str) for tag in item) for item in parsed
    ):
        raise TranslationError("tag generation response is not a JSON array of string arrays")

    return [list(item) for item in parsed]
