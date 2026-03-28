from __future__ import annotations

import unittest
from unittest.mock import patch

from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.translator import (
    OpenAICompatibleTranslator,
    TagGenerationRequest,
    TranslationError,
    _parse_tag_generation_payload,
    _parse_string_matrix_payload,
)


class TranslatorBatchTests(unittest.TestCase):
    def test_generate_tags_batch_normalizes_each_article(self) -> None:
        translator = OpenAICompatibleTranslator(_make_config())

        with patch.object(
            translator,
            "_request_json",
            return_value={
                "results": [
                    {"request_id": "0", "tags": ["AI", "Startups", "OpenAI"]},
                    {"request_id": "1", "tags": ["Robotics", "AI"]},
                ]
            },
        ) as request_mock:
            generated = translator.generate_tags_batch(
                [
                    TagGenerationRequest(
                        title="First",
                        content="<p>First body</p>",
                        existing_tags=("OpenAI",),
                        max_total_tags=3,
                        language="zh-CN",
                    ),
                    TagGenerationRequest(
                        title="Second",
                        content="<p>Second body</p>",
                        existing_tags=(),
                        max_total_tags=1,
                        language="zh-CN",
                    ),
                ]
            )

        self.assertEqual(generated, [["AI", "Startups"], ["Robotics"]])
        request_mock.assert_called_once()

    def test_generate_tags_batch_retries_smaller_chunks_on_incomplete_result(self) -> None:
        translator = OpenAICompatibleTranslator(_make_config())
        calls: list[list[str]] = []

        def fake_generate(chunk):
            request_ids = [request.request_id for request in chunk]
            calls.append(request_ids)
            if len(chunk) == 2:
                raise TranslationError("translator returned 1 tag sets for 2 requests")
            if chunk[0].request_id == "0":
                return [["AI"]]
            return [["Robotics"]]

        with patch.object(translator, "_generate_tags_chunk", side_effect=fake_generate):
            generated = translator.generate_tags_batch(
                [
                    TagGenerationRequest(
                        title="First",
                        content="<p>First body</p>",
                        existing_tags=(),
                        max_total_tags=3,
                        language="zh-CN",
                    ),
                    TagGenerationRequest(
                        title="Second",
                        content="<p>Second body</p>",
                        existing_tags=(),
                        max_total_tags=3,
                        language="zh-CN",
                    ),
                ]
            )

        self.assertEqual(generated, [["AI"], ["Robotics"]])
        self.assertEqual(calls, [["0", "1"], ["0"], ["1"]])

    def test_parse_tag_generation_payload_accepts_request_id_objects(self) -> None:
        parsed = _parse_tag_generation_payload(
            {
                "results": [
                    {"request_id": "b", "tags": ["Robotics", "Startups"]},
                    {"request_id": "a", "tags": ["AI"]},
                ]
            },
            ["a", "b"],
        )

        self.assertEqual(parsed, [["AI"], ["Robotics", "Startups"]])

    def test_parse_string_matrix_payload_accepts_tags_key(self) -> None:
        parsed = _parse_string_matrix_payload({"tags": [["AI"], ["Robotics", "Startups"]]})

        self.assertEqual(parsed, [["AI"], ["Robotics", "Startups"]])


def _make_config() -> AppConfig:
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
        tagging_api_base_url="https://api.openai.com/v1",
        tagging_api_key="test-key",
        tagging_model="gpt-test",
        tagging_request_timeout_seconds=120,
        max_texts_per_request=40,
        max_chars_per_request=8000,
        ai_tagging_enabled=True,
        ai_tagging_max_tags=6,
        ai_tagging_language="zh-CN",
        log_level="INFO",
    )


if __name__ == "__main__":
    unittest.main()
