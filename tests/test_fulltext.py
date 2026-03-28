from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.fulltext import MercuryFulltextClient, _encode_mercury_url_value


class MercuryFulltextClientTests(unittest.TestCase):
    def test_fetch_content_requests_parser_endpoint(self) -> None:
        client = MercuryFulltextClient(_make_config(api_base_url="http://mercury-parser:3000"))
        response = Mock()
        response.json.return_value = {"content": "<p>Hello</p>"}
        response.raise_for_status.return_value = None
        article_url = "https://example.com/path?q=1&lang=中文"

        with patch.object(client._session, "get", return_value=response) as get_mock:
            content = client.fetch_content(article_url)

        self.assertEqual(content, "<p>Hello</p>")
        get_mock.assert_called_once_with(
            f"http://mercury-parser:3000/parser?url={_encode_mercury_url_value(article_url)}",
            timeout=30,
        )

    def test_fetch_content_returns_none_when_response_has_no_content(self) -> None:
        client = MercuryFulltextClient(_make_config(api_base_url="http://mercury-parser:3000"))
        response = Mock()
        response.json.return_value = {"title": "Example"}
        response.raise_for_status.return_value = None

        with patch.object(client._session, "get", return_value=response):
            content = client.fetch_content("https://example.com/path")

        self.assertIsNone(content)


def _make_config(*, api_base_url: str) -> AppConfig:
    return AppConfig(
        database_url="postgresql://postgres:password@db:5432/postgres",
        owner_uid=1,
        target_language="zh-CN",
        source_langs=("en",),
        feed_ids=(61,),
        lookback_hours=48,
        batch_size=10,
        loop_interval_seconds=300,
        require_single_owner=True,
        dry_run=False,
        api_base_url="https://api.openai.com/v1",
        api_key="test-key",
        model="gpt-test",
        request_timeout_seconds=120,
        mercury_fulltext_api_base_url=api_base_url,
        mercury_fulltext_request_timeout_seconds=30,
        tagging_api_base_url="https://api.openai.com/v1",
        tagging_api_key="test-key",
        tagging_model="gpt-test",
        tagging_request_timeout_seconds=120,
        max_texts_per_request=40,
        max_chars_per_request=8000,
        ai_tagging_enabled=False,
        ai_tagging_max_tags=6,
        ai_tagging_language="zh-CN",
        log_level="INFO",
    )


if __name__ == "__main__":
    unittest.main()
