from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from ttrss_feed_translator.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_requires_feed_whitelist(self) -> None:
        env = _base_env()
        env["TRANSLATOR_FEED_IDS"] = ""

        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(ValueError, "TRANSLATOR_FEED_IDS"):
                AppConfig.from_env()

    def test_source_langs_is_optional_when_feed_whitelist_exists(self) -> None:
        env = _base_env()
        env["TRANSLATOR_FEED_IDS"] = "61,62"
        env["TRANSLATOR_SOURCE_LANGS"] = ""

        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()

        self.assertEqual(config.feed_ids, (61, 62))
        self.assertEqual(config.source_langs, ())

    def test_ai_tagging_defaults_to_disabled_and_target_language(self) -> None:
        with patch.dict(os.environ, _base_env(), clear=True):
            config = AppConfig.from_env()

        self.assertFalse(config.ai_tagging_enabled)
        self.assertEqual(config.ai_tagging_max_tags, 6)
        self.assertEqual(config.ai_tagging_language, "zh-CN")
        self.assertEqual(config.tagging_api_base_url, config.api_base_url)
        self.assertEqual(config.tagging_api_key, config.api_key)
        self.assertEqual(config.tagging_model, config.model)
        self.assertEqual(config.tagging_request_timeout_seconds, config.request_timeout_seconds)
        self.assertEqual(config.mercury_fulltext_api_base_url, "")
        self.assertEqual(config.mercury_fulltext_request_timeout_seconds, 30)

    def test_tagging_backend_can_override_translation_backend(self) -> None:
        env = _base_env()
        env["TRANSLATOR_TAGGING_API_BASE_URL"] = "http://ollama:11434/v1"
        env["TRANSLATOR_TAGGING_API_KEY"] = "ollama"
        env["TRANSLATOR_TAGGING_MODEL"] = "qwen3:8b"
        env["TRANSLATOR_TAGGING_REQUEST_TIMEOUT_SECONDS"] = "45"

        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()

        self.assertEqual(config.tagging_api_base_url, "http://ollama:11434/v1")
        self.assertEqual(config.tagging_api_key, "ollama")
        self.assertEqual(config.tagging_model, "qwen3:8b")
        self.assertEqual(config.tagging_request_timeout_seconds, 45)

    def test_mercury_fulltext_can_be_enabled(self) -> None:
        env = _base_env()
        env["TRANSLATOR_MERCURY_FULLTEXT_API_BASE_URL"] = "http://mercury-parser:3000"
        env["TRANSLATOR_MERCURY_FULLTEXT_REQUEST_TIMEOUT_SECONDS"] = "15"

        with patch.dict(os.environ, env, clear=True):
            config = AppConfig.from_env()

        self.assertEqual(config.mercury_fulltext_api_base_url, "http://mercury-parser:3000")
        self.assertEqual(config.mercury_fulltext_request_timeout_seconds, 15)


def _base_env() -> dict[str, str]:
    return {
        "TRANSLATOR_DATABASE_URL": "postgresql://postgres:password@db:5432/postgres",
        "TRANSLATOR_OWNER_UID": "1",
        "TRANSLATOR_TARGET_LANGUAGE": "zh-CN",
        "TRANSLATOR_FEED_IDS": "61",
        "TRANSLATOR_API_BASE_URL": "https://api.openai.com/v1",
        "TRANSLATOR_API_KEY": "test-key",
        "TRANSLATOR_MODEL": "gpt-test",
    }


if __name__ == "__main__":
    unittest.main()
