from __future__ import annotations

from datetime import datetime
import unittest

from ttrss_feed_translator.models import EntryCandidate, TranslationRecord
from ttrss_feed_translator.utils import compute_source_hash
from ttrss_feed_translator.workflow import plan_entry


class WorkflowTests(unittest.TestCase):
    def test_skip_when_current_content_matches_saved_translation(self) -> None:
        record = _make_record(
            source_title="Hello",
            source_content="<p>Hello</p>",
            translated_title="你好",
            translated_content="<p>你好</p>",
            target_language="zh-CN",
        )
        candidate = _make_candidate(title="你好", content="<p>你好</p>", translation=record)

        plan = plan_entry(candidate, "zh-CN", require_single_owner=True)

        self.assertEqual(plan.action, "skip")
        self.assertEqual(plan.reason, "already-translated")

    def test_reapply_when_same_source_hash_returns(self) -> None:
        record = _make_record(
            source_title="Hello",
            source_content="<p>Hello</p>",
            translated_title="你好",
            translated_content="<p>你好</p>",
            target_language="zh-CN",
        )
        candidate = _make_candidate(title="Hello", content="<p>Hello</p>", translation=record)

        plan = plan_entry(candidate, "zh-CN", require_single_owner=True)

        self.assertEqual(plan.action, "reapply")
        self.assertEqual(plan.reason, "same-source-hash-found-in-db")

    def test_retranslate_when_target_language_changes(self) -> None:
        record = _make_record(
            source_title="Hello",
            source_content="<p>Hello</p>",
            translated_title="你好",
            translated_content="<p>你好</p>",
            target_language="zh-CN",
        )
        candidate = _make_candidate(title="你好", content="<p>你好</p>", translation=record)

        plan = plan_entry(candidate, "ja", require_single_owner=True)

        self.assertEqual(plan.action, "translate")
        self.assertEqual(plan.source_title, "Hello")
        self.assertEqual(plan.reason, "target-language-changed-reuse-stored-source")


def _make_candidate(title: str, content: str, translation: TranslationRecord | None) -> EntryCandidate:
    return EntryCandidate(
        entry_id=1,
        owner_uid=1,
        user_entry_id=101,
        feed_id=10,
        feed_title="Example Feed",
        title=title,
        content=content,
        current_tags=("ai",),
        source_lang="en",
        date_entered=datetime(2026, 3, 27, 12, 0, 0),
        owner_count=1,
        translation=translation,
    )


def _make_record(
    *,
    source_title: str,
    source_content: str,
    translated_title: str,
    translated_content: str,
    target_language: str,
) -> TranslationRecord:
    now = datetime(2026, 3, 27, 12, 0, 0)
    return TranslationRecord(
        entry_id=1,
        owner_uid=1,
        user_entry_id=101,
        feed_id=10,
        source_lang="en",
        target_language=target_language,
        source_hash=compute_source_hash(source_title, source_content),
        source_title=source_title,
        source_content=source_content,
        translated_title=translated_title,
        translated_content=translated_content,
        generated_tags=(),
        translated_at=now,
        reapplied_at=None,
        updated_at=now,
        last_error=None,
    )


if __name__ == "__main__":
    unittest.main()
