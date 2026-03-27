from __future__ import annotations

from ttrss_feed_translator.models import EntryCandidate, ProcessingPlan
from ttrss_feed_translator.utils import compute_source_hash


def plan_entry(candidate: EntryCandidate, target_language: str, require_single_owner: bool) -> ProcessingPlan:
    if require_single_owner and candidate.owner_count > 1:
        return ProcessingPlan(
            action="skip",
            reason="shared-between-multiple-owners",
            source_title=candidate.title,
            source_content=candidate.content,
            source_hash=compute_source_hash(candidate.title, candidate.content),
        )

    record = candidate.translation
    current_hash = compute_source_hash(candidate.title, candidate.content)

    if record is None:
        return ProcessingPlan(
            action="translate",
            reason="no-tracking-record",
            source_title=candidate.title,
            source_content=candidate.content,
            source_hash=current_hash,
        )

    current_is_saved_translation = (
        candidate.title == record.translated_title and candidate.content == record.translated_content
    )
    target_matches = record.target_language == target_language

    if current_is_saved_translation and target_matches:
        return ProcessingPlan(
            action="skip",
            reason="already-translated",
            source_title=record.source_title,
            source_content=record.source_content,
            source_hash=record.source_hash,
        )

    if current_hash == record.source_hash and target_matches:
        return ProcessingPlan(
            action="reapply",
            reason="same-source-hash-found-in-db",
            source_title=record.source_title,
            source_content=record.source_content,
            source_hash=record.source_hash,
        )

    if current_is_saved_translation and not target_matches:
        return ProcessingPlan(
            action="translate",
            reason="target-language-changed-reuse-stored-source",
            source_title=record.source_title,
            source_content=record.source_content,
            source_hash=record.source_hash,
        )

    return ProcessingPlan(
        action="translate",
        reason="source-changed-or-manually-edited",
        source_title=candidate.title,
        source_content=candidate.content,
        source_hash=current_hash,
    )

