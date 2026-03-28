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
