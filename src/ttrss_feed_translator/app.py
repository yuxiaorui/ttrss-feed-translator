from __future__ import annotations

from dataclasses import dataclass
import logging

from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.db import (
    connect,
    ensure_schema,
    fetch_candidates,
    reapply_translation,
    record_error,
    save_translation,
    sync_generated_tags,
)
from ttrss_feed_translator.html_translate import (
    prepare_title_and_html_translation,
    translate_title_and_html,
)
from ttrss_feed_translator.models import EntryCandidate, ProcessingPlan
from ttrss_feed_translator.tags import merge_tags
from ttrss_feed_translator.translator import OpenAICompatibleTranslator, TagGenerationRequest
from ttrss_feed_translator.workflow import plan_entry


logger = logging.getLogger(__name__)


@dataclass
class RunStats:
    translated: int = 0
    reapplied: int = 0
    skipped: int = 0
    failed: int = 0
    tagged: int = 0


@dataclass(frozen=True)
class TagPlan:
    action: str
    tags: tuple[str, ...]
    reason: str
    persist: bool


@dataclass(frozen=True)
class PlannedCandidate:
    candidate: EntryCandidate
    plan: ProcessingPlan


def run_once(config: AppConfig) -> RunStats:
    stats = RunStats()
    translation_translator = OpenAICompatibleTranslator(config)
    tagging_translator = OpenAICompatibleTranslator.for_tagging(config)

    source_langs = ",".join(config.source_langs) if config.source_langs else "<disabled>"
    feed_ids = ",".join(str(feed_id) for feed_id in config.feed_ids)
    logger.info(
        "running with filters: owner_uid=%s feed_ids=%s lookback_hours=%s batch_size=%s source_langs=%s dry_run=%s",
        config.owner_uid,
        feed_ids,
        config.lookback_hours,
        config.batch_size,
        source_langs,
        config.dry_run,
    )

    with connect(config.database_url) as conn:
        ensure_schema(conn)
        conn.commit()

        translation_queue = _collect_translation_queue(
            conn,
            config,
            translation_translator,
            tagging_translator,
            stats,
        )
        _process_translation_batch(
            conn,
            translation_queue,
            config,
            translation_translator,
            tagging_translator,
            stats,
        )

    logger.info(
        "finished run: translated=%s reapplied=%s skipped=%s failed=%s tagged=%s",
        stats.translated,
        stats.reapplied,
        stats.skipped,
        stats.failed,
        stats.tagged,
    )
    return stats


def _collect_translation_queue(
    conn,
    config: AppConfig,
    translation_translator: OpenAICompatibleTranslator,
    tagging_translator: OpenAICompatibleTranslator,
    stats: RunStats,
) -> list[PlannedCandidate]:
    translation_queue: list[PlannedCandidate] = []
    inspected_candidates = 0
    offset = 0
    page_size = config.batch_size

    while len(translation_queue) < config.batch_size:
        candidates = fetch_candidates(conn, config, limit=page_size, offset=offset)
        if not candidates:
            break

        logger.info(
            "fetched %s candidate articles at offset=%s while collecting translation queue",
            len(candidates),
            offset,
        )
        offset += len(candidates)

        for candidate in candidates:
            inspected_candidates += 1
            planned_candidate = _plan_candidate(candidate, config)
            if planned_candidate.plan.action == "translate":
                translation_queue.append(planned_candidate)
                if len(translation_queue) >= config.batch_size:
                    break
                continue

            _process_candidate_safely(
                conn,
                planned_candidate,
                config,
                translation_translator,
                tagging_translator,
                stats,
            )

        if len(candidates) < page_size:
            break

    logger.info(
        "collected %s translation candidates after inspecting %s candidate articles",
        len(translation_queue),
        inspected_candidates,
    )
    return translation_queue


def _plan_candidate(candidate: EntryCandidate, config: AppConfig) -> PlannedCandidate:
    plan = plan_entry(candidate, config.target_language, config.require_single_owner)
    logger.info(
        "entry=%s feed=%s action=%s reason=%s",
        candidate.entry_id,
        candidate.feed_title or candidate.feed_id,
        plan.action,
        plan.reason,
    )
    return PlannedCandidate(candidate=candidate, plan=plan)


def _process_candidate_safely(
    conn,
    planned_candidate: PlannedCandidate,
    config: AppConfig,
    translation_translator: OpenAICompatibleTranslator,
    tagging_translator: OpenAICompatibleTranslator,
    stats: RunStats,
    translated_entry: tuple[str, str] | None = None,
    generated_tags: tuple[str, ...] | None = None,
) -> None:
    try:
        _process_candidate(
            conn,
            planned_candidate,
            config,
            translation_translator,
            tagging_translator,
            stats,
            translated_entry=translated_entry,
            generated_tags=generated_tags,
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        logger.exception("entry %s failed: %s", planned_candidate.candidate.entry_id, exc)
        record_error(conn, planned_candidate.candidate, str(exc))
        conn.commit()
        stats.failed += 1


def _process_translation_batch(
    conn,
    planned_candidates: list[PlannedCandidate],
    config: AppConfig,
    translation_translator: OpenAICompatibleTranslator,
    tagging_translator: OpenAICompatibleTranslator,
    stats: RunStats,
) -> None:
    if not planned_candidates:
        return

    try:
        translated_entries = _translate_planned_candidates_in_batch(
            planned_candidates,
            translation_translator,
        )
    except Exception:
        logger.warning(
            "batch translation failed for %s entries; falling back to per-entry translation",
            len(planned_candidates),
            exc_info=True,
        )
        for planned_candidate in planned_candidates:
            _process_candidate_safely(
                conn,
                planned_candidate,
                config,
                translation_translator,
                tagging_translator,
                stats,
            )
        return

    generated_tags_by_entry = _generate_ai_tags_in_batch(
        planned_candidates,
        config,
        tagging_translator,
    )

    for planned_candidate, translated_entry, generated_tags in zip(
        planned_candidates,
        translated_entries,
        generated_tags_by_entry,
        strict=True,
    ):
        _process_candidate_safely(
            conn,
            planned_candidate,
            config,
            translation_translator,
            tagging_translator,
            stats,
            translated_entry=translated_entry,
            generated_tags=generated_tags,
        )


def _translate_planned_candidates_in_batch(
    planned_candidates: list[PlannedCandidate],
    translator: OpenAICompatibleTranslator,
) -> list[tuple[str, str]]:
    prepared_translations = []
    batch_payload: list[str] = []
    spans: list[tuple[int, int]] = []

    for planned_candidate in planned_candidates:
        if planned_candidate.plan.action != "translate":
            raise ValueError("batch translation only supports translate actions")

        prepared = prepare_title_and_html_translation(
            planned_candidate.plan.source_title,
            planned_candidate.plan.source_content,
        )
        start = len(batch_payload)
        batch_payload.extend(prepared.texts)
        spans.append((start, len(prepared.texts)))
        prepared_translations.append(prepared)

    translated_payload = translator.translate_texts(batch_payload) if batch_payload else []
    if len(translated_payload) != len(batch_payload):
        raise ValueError("translator returned a different number of batched text items")

    translated_entries: list[tuple[str, str]] = []
    for prepared, (start, size) in zip(prepared_translations, spans, strict=True):
        translated_entries.append(prepared.apply_translations(translated_payload[start : start + size]))

    return translated_entries


def _process_candidate(
    conn,
    planned_candidate: PlannedCandidate,
    config: AppConfig,
    translation_translator: OpenAICompatibleTranslator,
    tagging_translator: OpenAICompatibleTranslator,
    stats: RunStats,
    translated_entry: tuple[str, str] | None = None,
    generated_tags: tuple[str, ...] | None = None,
) -> None:
    candidate = planned_candidate.candidate
    plan = planned_candidate.plan

    if plan.action == "skip":
        tag_plan = _plan_tag_sync(candidate, plan, config, tagging_translator)
        _apply_tag_plan(conn, candidate, config, tag_plan, stats)
        stats.skipped += 1
        return

    if plan.action == "reapply":
        tag_plan = _plan_tag_sync(candidate, plan, config, tagging_translator)
        if config.dry_run:
            logger.info(
                "dry-run: would reapply cached translation for entry %s%s",
                candidate.entry_id,
                _format_tag_plan_suffix(tag_plan),
            )
        else:
            reapply_translation(
                conn,
                candidate,
                generated_tags=tag_plan.tags if tag_plan.action != "skip" else None,
                persist_generated_tags=tag_plan.persist,
            )
            if tag_plan.action != "skip":
                stats.tagged += 1
        stats.reapplied += 1
        return

    if translated_entry is None:
        translated_title, translated_content = translate_title_and_html(
            plan.source_title,
            plan.source_content,
            translation_translator,
        )
    else:
        translated_title, translated_content = translated_entry

    if generated_tags is None:
        generated_tags = _generate_ai_tags(
            candidate,
            plan.source_title,
            plan.source_content,
            config,
            tagging_translator,
        )

    if config.dry_run:
        logger.info(
            "dry-run: would write translation for entry %s (title chars=%s, content chars=%s%s)",
            candidate.entry_id,
            len(translated_title),
            len(translated_content),
            f", ai_tags={len(generated_tags)}" if generated_tags else "",
        )
    else:
        save_translation(
            conn,
            candidate=candidate,
            source_title=plan.source_title,
            source_content=plan.source_content,
            source_hash=plan.source_hash,
            translated_title=translated_title,
            translated_content=translated_content,
            generated_tags=generated_tags,
            target_language=config.target_language,
        )
        if generated_tags:
            stats.tagged += 1
    stats.translated += 1


def _generate_ai_tags(
    candidate: EntryCandidate,
    source_title: str,
    source_content: str,
    config: AppConfig,
    tagging_translator: OpenAICompatibleTranslator,
) -> tuple[str, ...]:
    request = _build_tag_generation_request(candidate, source_title, source_content, config)
    if request is None:
        return ()

    generated = tagging_translator.generate_tags(
        title=request.title,
        content=request.content,
        existing_tags=request.existing_tags,
        max_total_tags=request.max_total_tags,
        language=request.language,
    )
    return tuple(generated)


def _generate_ai_tags_in_batch(
    planned_candidates: list[PlannedCandidate],
    config: AppConfig,
    tagging_translator: OpenAICompatibleTranslator,
) -> list[tuple[str, ...]]:
    generated_tags_by_entry: list[tuple[str, ...]] = [()] * len(planned_candidates)
    requests: list[TagGenerationRequest] = []
    request_indexes: list[int] = []

    for index, planned_candidate in enumerate(planned_candidates):
        request = _build_tag_generation_request(
            planned_candidate.candidate,
            planned_candidate.plan.source_title,
            planned_candidate.plan.source_content,
            config,
        )
        if request is None:
            continue

        request_indexes.append(index)
        requests.append(request)

    if not requests:
        return generated_tags_by_entry

    try:
        generated_batches = tagging_translator.generate_tags_batch(requests)
    except Exception:
        logger.warning(
            "batch ai-tag generation failed for %s entries; falling back to per-entry tag generation",
            len(requests),
            exc_info=True,
        )
        for index, request in zip(request_indexes, requests, strict=True):
            generated_tags_by_entry[index] = tuple(
                tagging_translator.generate_tags(
                    title=request.title,
                    content=request.content,
                    existing_tags=request.existing_tags,
                    max_total_tags=request.max_total_tags,
                    language=request.language,
                )
            )
        return generated_tags_by_entry

    if len(generated_batches) != len(requests):
        raise ValueError("translator returned a different number of ai-tag result sets")

    for index, generated in zip(request_indexes, generated_batches, strict=True):
        generated_tags_by_entry[index] = tuple(generated)

    return generated_tags_by_entry


def _build_tag_generation_request(
    candidate: EntryCandidate,
    source_title: str,
    source_content: str,
    config: AppConfig,
) -> TagGenerationRequest | None:
    if not config.ai_tagging_enabled:
        return None
    if candidate.owner_count > 1 and config.require_single_owner:
        return None
    if len(candidate.current_tags) >= config.ai_tagging_max_tags:
        return None

    return TagGenerationRequest(
        title=source_title,
        content=source_content,
        existing_tags=candidate.current_tags,
        max_total_tags=config.ai_tagging_max_tags,
        language=config.ai_tagging_language,
    )


def _plan_tag_sync(
    candidate: EntryCandidate,
    plan,
    config: AppConfig,
    tagging_translator: OpenAICompatibleTranslator,
) -> TagPlan:
    if not config.ai_tagging_enabled:
        return TagPlan(action="skip", tags=(), reason="ai-tagging-disabled", persist=False)
    if plan.reason == "shared-between-multiple-owners":
        return TagPlan(action="skip", tags=(), reason="shared-entry", persist=False)
    if candidate.translation is None:
        return TagPlan(action="skip", tags=(), reason="no-tracking-record", persist=False)

    if candidate.translation.generated_tags:
        merged = merge_tags(candidate.current_tags, candidate.translation.generated_tags)
        if len(merged) > len(candidate.current_tags):
            return TagPlan(
                action="apply",
                tags=candidate.translation.generated_tags,
                reason="restore-saved-ai-tags",
                persist=False,
            )
        return TagPlan(action="skip", tags=(), reason="saved-ai-tags-already-present", persist=False)

    if len(candidate.current_tags) >= config.ai_tagging_max_tags:
        return TagPlan(action="skip", tags=(), reason="existing-tags-sufficient", persist=False)

    generated = tagging_translator.generate_tags(
        title=candidate.translation.source_title,
        content=candidate.translation.source_content,
        existing_tags=candidate.current_tags,
        max_total_tags=config.ai_tagging_max_tags,
        language=config.ai_tagging_language,
    )
    if not generated:
        return TagPlan(action="skip", tags=(), reason="no-new-ai-tags", persist=False)

    return TagPlan(action="generate", tags=tuple(generated), reason="generated-ai-tags", persist=True)


def _apply_tag_plan(
    conn,
    candidate: EntryCandidate,
    config: AppConfig,
    tag_plan: TagPlan,
    stats: RunStats,
) -> None:
    if tag_plan.action == "skip":
        return

    if config.dry_run:
        logger.info(
            "dry-run: would %s %s ai tags for entry %s (reason=%s)",
            "restore" if tag_plan.action == "apply" else "generate",
            len(tag_plan.tags),
            candidate.entry_id,
            tag_plan.reason,
        )
        return

    sync_generated_tags(conn, candidate, tag_plan.tags, persist=tag_plan.persist)
    stats.tagged += 1


def _format_tag_plan_suffix(tag_plan: TagPlan) -> str:
    if tag_plan.action == "skip":
        return ""
    verb = "restore" if tag_plan.action == "apply" else "generate"
    return f", would {verb} {len(tag_plan.tags)} ai tags"
