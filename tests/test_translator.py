from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.translator import (
    OpenAICompatibleTranslator,
    TagGenerationRequest,
    TranslationError,
    _parse_indexed_translation_payload,
    _parse_tag_generation_payload,
    _parse_string_matrix_payload,
)


class TranslatorBatchTests(unittest.TestCase):
    def test_translate_texts_retries_smaller_chunks_on_mangled_result(self) -> None:
        translator = OpenAICompatibleTranslator(_make_config())
        calls: list[list[str]] = []

        def fake_translate_chunk(chunk):
            calls.append(list(chunk))
            if len(chunk) > 1:
                raise TranslationError(
                    f"translator returned {len(chunk) + 1} items for {len(chunk)} source texts"
                )
            return [f"zh:{chunk[0]}"]

        with patch.object(translator, "_translate_chunk", side_effect=fake_translate_chunk):
            translated = translator.translate_texts(["a", "b", "c"])

        self.assertEqual(translated, ["zh:a", "zh:b", "zh:c"])
        self.assertEqual(calls, [["a", "b", "c"], ["a"], ["b", "c"], ["b"], ["c"]])

    def test_translate_texts_raises_when_a_single_text_keeps_failing(self) -> None:
        translator = OpenAICompatibleTranslator(_make_config())

        with patch.object(
            translator,
            "_translate_chunk",
            side_effect=TranslationError("translation response is not a JSON object keyed by index"),
        ) as chunk_mock:
            with self.assertRaises(TranslationError):
                translator.translate_texts(["a", "b"])

        # whole chunk, then the left half (which re-raises before the right half runs)
        self.assertEqual(chunk_mock.call_count, 2)

    def test_parse_indexed_translation_payload_orders_by_index(self) -> None:
        parsed = _parse_indexed_translation_payload({"1": "二", "0": "一"}, 2)

        self.assertEqual(parsed, ["一", "二"])

    def test_parse_indexed_translation_payload_rejects_incomplete_result(self) -> None:
        with self.assertRaises(TranslationError) as exc_info:
            _parse_indexed_translation_payload({"0": "一"}, 2)

        self.assertIn("missing keys: 1", str(exc_info.exception))

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

    def test_request_json_uses_responses_api_output_text(self) -> None:
        translator = OpenAICompatibleTranslator(_make_config())
        response = Mock()
        response.json.return_value = {
            "id": "resp_123",
            "object": "response",
            "model": "gpt-test",
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "[\"你好\"]"},
                    ],
                }
            ],
        }

        with patch.object(translator._session, "post", return_value=response) as post_mock:
            parsed = translator._request_json([{"role": "user", "content": "[\"hello\"]"}])

        self.assertEqual(parsed, ["你好"])
        self.assertEqual(post_mock.call_args.args[0], "https://api.openai.com/v1/responses")
        self.assertEqual(
            post_mock.call_args.kwargs["json"]["input"],
            [{"role": "user", "content": [{"type": "input_text", "text": "[\"hello\"]"}]}],
        )

    def test_request_json_reports_missing_output_text_clearly(self) -> None:
        translator = OpenAICompatibleTranslator(_make_config())
        response = Mock()
        response.json.return_value = {
            "id": "resp_123",
            "object": "response",
            "model": "gpt-test",
            "status": "completed",
            "output": [],
        }

        with patch.object(translator._session, "post", return_value=response):
            with self.assertRaises(TranslationError) as exc_info:
                translator._request_json([{"role": "user", "content": "[\"hello\"]"}])

        self.assertIn("responses api response did not include any output text", str(exc_info.exception))
        self.assertIn('"status": "completed"', str(exc_info.exception))
        self.assertIn('"output_count": 0', str(exc_info.exception))


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
        mercury_fulltext_api_base_url="",
        mercury_fulltext_request_timeout_seconds=30,
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
