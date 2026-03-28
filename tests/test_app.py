from __future__ import annotations

from datetime import datetime
import unittest
from unittest.mock import patch

from ttrss_feed_translator.app import (
    PlannedCandidate,
    RunStats,
    _process_translation_batch,
    _translate_planned_candidates_in_batch,
)
from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.models import EntryCandidate, ProcessingPlan


class FakeConn:
    def __init__(self) -> None:
        self.commits = 0
        self.rollbacks = 0

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class FakeTranslator:
    def __init__(
        self,
        fail_when_payload_exceeds: int | None = None,
        fail_tag_batch_when_request_count_exceeds: int | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.tag_batch_calls: list[list[str]] = []
        self.tag_calls: list[str] = []
        self.fail_when_payload_exceeds = fail_when_payload_exceeds
        self.fail_tag_batch_when_request_count_exceeds = fail_tag_batch_when_request_count_exceeds

    def translate_texts(self, texts: list[str]) -> list[str]:
        self.calls.append(list(texts))
        if self.fail_when_payload_exceeds is not None and len(texts) > self.fail_when_payload_exceeds:
            raise RuntimeError("batch payload too large for fake translator")

        mapping = {
            "First title": "标题一",
            "First body": "正文一",
            "Second title": "标题二",
            "Second body": "正文二",
        }
        return [mapping[text] for text in texts]

    def generate_tags_batch(self, requests: list[object]) -> list[list[str]]:
        titles = [request.title for request in requests]
        self.tag_batch_calls.append(titles)
        if (
            self.fail_tag_batch_when_request_count_exceeds is not None
            and len(requests) > self.fail_tag_batch_when_request_count_exceeds
        ):
            raise RuntimeError("tag batch too large for fake translator")

        return [self._tag_mapping(request.title) for request in requests]

    def generate_tags(self, **kwargs: object) -> list[str]:
        title = str(kwargs["title"])
        self.tag_calls.append(title)
        return self._tag_mapping(title)

    def _tag_mapping(self, title: str) -> list[str]:
        mapping = {
            "First title": ["AI", "Startups"],
            "Second title": ["Robotics"],
        }
        return mapping[title]


class AppBatchTests(unittest.TestCase):
    def test_translate_candidates_are_batched_across_articles(self) -> None:
        translator = FakeTranslator()

        translated_entries = _translate_planned_candidates_in_batch(
            [
                _make_planned_candidate(1, "First title", "<p>First body</p>"),
                _make_planned_candidate(2, "Second title", "<div>Second body</div>"),
            ],
            translator,
        )

        self.assertEqual(
            translator.calls,
            [["First title", "First body", "Second title", "Second body"]],
        )
        self.assertEqual(
            translated_entries,
            [
                ("标题一", "<p>正文一</p>"),
                ("标题二", "<div>正文二</div>"),
            ],
        )

    def test_batch_failure_falls_back_to_per_article_translation(self) -> None:
        translator = FakeTranslator(fail_when_payload_exceeds=2)
        conn = FakeConn()
        stats = RunStats()
        saved_entries: list[tuple[int, str, str]] = []

        with patch("ttrss_feed_translator.app.save_translation") as save_translation_mock:
            with patch("ttrss_feed_translator.app.record_error") as record_error_mock:
                save_translation_mock.side_effect = (
                    lambda *args, **kwargs: saved_entries.append(
                        (
                            kwargs["candidate"].entry_id,
                            kwargs["translated_title"],
                            kwargs["translated_content"],
                        )
                    )
                )

                _process_translation_batch(
                    conn,
                    [
                        _make_planned_candidate(1, "First title", "<p>First body</p>"),
                        _make_planned_candidate(2, "Second title", "<div>Second body</div>"),
                    ],
                    _make_config(),
                    translator,
                    stats,
                )

        self.assertEqual(
            translator.calls,
            [
                ["First title", "First body", "Second title", "Second body"],
                ["First title", "First body"],
                ["Second title", "Second body"],
            ],
        )
        self.assertEqual(
            saved_entries,
            [
                (1, "标题一", "<p>正文一</p>"),
                (2, "标题二", "<div>正文二</div>"),
            ],
        )
        self.assertEqual(stats.translated, 2)
        self.assertEqual(stats.failed, 0)
        self.assertEqual(conn.commits, 2)
        self.assertEqual(conn.rollbacks, 0)
        record_error_mock.assert_not_called()

    def test_ai_tags_are_batched_across_articles(self) -> None:
        translator = FakeTranslator()
        conn = FakeConn()
        stats = RunStats()
        saved_entries: list[tuple[int, tuple[str, ...]]] = []

        with patch("ttrss_feed_translator.app.save_translation") as save_translation_mock:
            save_translation_mock.side_effect = (
                lambda *args, **kwargs: saved_entries.append(
                    (
                        kwargs["candidate"].entry_id,
                        kwargs["generated_tags"],
                    )
                )
            )

            _process_translation_batch(
                conn,
                [
                    _make_planned_candidate(1, "First title", "<p>First body</p>"),
                    _make_planned_candidate(2, "Second title", "<div>Second body</div>"),
                ],
                _make_config(ai_tagging_enabled=True),
                translator,
                stats,
            )

        self.assertEqual(translator.calls, [["First title", "First body", "Second title", "Second body"]])
        self.assertEqual(translator.tag_batch_calls, [["First title", "Second title"]])
        self.assertEqual(translator.tag_calls, [])
        self.assertEqual(
            saved_entries,
            [
                (1, ("AI", "Startups")),
                (2, ("Robotics",)),
            ],
        )
        self.assertEqual(stats.translated, 2)
        self.assertEqual(stats.tagged, 2)

    def test_ai_tag_batch_failure_falls_back_to_per_article_generation(self) -> None:
        translator = FakeTranslator(fail_tag_batch_when_request_count_exceeds=1)
        conn = FakeConn()
        stats = RunStats()
        saved_entries: list[tuple[int, tuple[str, ...]]] = []

        with patch("ttrss_feed_translator.app.save_translation") as save_translation_mock:
            with patch("ttrss_feed_translator.app.record_error") as record_error_mock:
                save_translation_mock.side_effect = (
                    lambda *args, **kwargs: saved_entries.append(
                        (
                            kwargs["candidate"].entry_id,
                            kwargs["generated_tags"],
                        )
                    )
                )

                _process_translation_batch(
                    conn,
                    [
                        _make_planned_candidate(1, "First title", "<p>First body</p>"),
                        _make_planned_candidate(2, "Second title", "<div>Second body</div>"),
                    ],
                    _make_config(ai_tagging_enabled=True),
                    translator,
                    stats,
                )

        self.assertEqual(translator.tag_batch_calls, [["First title", "Second title"]])
        self.assertEqual(translator.tag_calls, ["First title", "Second title"])
        self.assertEqual(
            saved_entries,
            [
                (1, ("AI", "Startups")),
                (2, ("Robotics",)),
            ],
        )
        self.assertEqual(stats.translated, 2)
        self.assertEqual(stats.failed, 0)
        record_error_mock.assert_not_called()


def _make_planned_candidate(entry_id: int, title: str, content: str) -> PlannedCandidate:
    candidate = EntryCandidate(
        entry_id=entry_id,
        owner_uid=1,
        user_entry_id=entry_id,
        feed_id=61,
        feed_title="Example Feed",
        title=title,
        content=content,
        current_tags=(),
        source_lang="en",
        date_entered=datetime(2026, 3, 28, 0, 0, 0),
        owner_count=1,
        translation=None,
    )
    plan = ProcessingPlan(
        action="translate",
        reason="no-tracking-record",
        source_title=title,
        source_content=content,
        source_hash=f"hash-{entry_id}",
    )
    return PlannedCandidate(candidate=candidate, plan=plan)


def _make_config(*, ai_tagging_enabled: bool = False) -> AppConfig:
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
        max_texts_per_request=40,
        max_chars_per_request=8000,
        ai_tagging_enabled=ai_tagging_enabled,
        ai_tagging_max_tags=6,
        ai_tagging_language="zh-CN",
        log_level="INFO",
    )


if __name__ == "__main__":
    unittest.main()
