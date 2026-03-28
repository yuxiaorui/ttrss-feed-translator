from __future__ import annotations

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
        remaining_slots = max_total_tags - len(existing_tags)
        if remaining_slots <= 0:
            return []

        source_excerpt = extract_text_for_tagging(content, max_chars=TAGGING_SOURCE_TEXT_LIMIT)
        payload = {
            "title": title,
            "content_excerpt": source_excerpt,
            "existing_tags": list(existing_tags),
            "max_new_tags": remaining_slots,
            "tag_language": language,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an RSS tagging engine. "
                    f"Generate at most {remaining_slots} additional article tags in {language}. "
                    "Return JSON only. The output must be a JSON array of strings. "
                    "Prefer concrete people, companies, products, places, technologies, and themes. "
                    "Avoid generic tags like news, latest, article, update, and analysis. "
                    "Avoid repeating any existing tags. Keep tags concise."
                ),
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            },
        ]

        generated = self._request_string_array(messages)
        normalized = merge_tags((), generated, limit=remaining_slots)
        filtered = [
            tag for tag in normalized if len(merge_tags(existing_tags, (tag,))) > len(existing_tags)
        ]
        logger.debug("generated %s ai tags", len(filtered))
        return filtered

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

    def _request_string_array(self, messages: list[dict[str, str]]) -> list[str]:
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

        return _parse_string_array_payload(content)


def _parse_string_array_payload(content: str) -> list[str]:
    cleaned = content.strip()

    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    parsed = json.loads(cleaned)

    if isinstance(parsed, dict) and "translations" in parsed:
        parsed = parsed["translations"]

    if not isinstance(parsed, list) or not all(isinstance(item, str) for item in parsed):
        raise TranslationError("translation response is not a JSON string array")

    return list(parsed)
