from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TranslationRecord:
    entry_id: int
    owner_uid: int
    feed_id: int | None
    source_lang: str | None
    target_language: str
    source_hash: str
    source_title: str
    source_content: str
    translated_title: str
    translated_content: str
    translated_at: datetime
    reapplied_at: datetime | None
    updated_at: datetime
    last_error: str | None


@dataclass(frozen=True)
class EntryCandidate:
    entry_id: int
    owner_uid: int
    feed_id: int | None
    feed_title: str
    title: str
    content: str
    source_lang: str | None
    date_entered: datetime
    owner_count: int
    translation: TranslationRecord | None


@dataclass(frozen=True)
class ProcessingPlan:
    action: str
    reason: str
    source_title: str
    source_content: str
    source_hash: str

