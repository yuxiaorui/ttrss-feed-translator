from __future__ import annotations

import json
import logging
from typing import Iterable

import requests

from ttrss_feed_translator.config import AppConfig


logger = logging.getLogger(__name__)


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
        url = f"{self._api_base_url}/chat/completions"
        source_hint = ", ".join(self._source_langs) if self._source_langs else "auto-detect source language"
        payload = {
            "model": self._model,
            "temperature": 0,
            "messages": [
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
            ],
        }

        response = self._session.post(url, json=payload, timeout=self._timeout)
        response.raise_for_status()
        data = response.json()

        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise TranslationError(f"unexpected response payload: {data}") from exc

        parsed = _parse_translation_payload(content)
        if len(parsed) != len(texts):
            raise TranslationError(
                f"translator returned {len(parsed)} items for {len(texts)} source texts"
            )

        logger.debug("translated %s text nodes", len(texts))
        return parsed


def _parse_translation_payload(content: str) -> list[str]:
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
