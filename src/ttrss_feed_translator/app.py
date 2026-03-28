from __future__ import annotations

from dataclasses import dataclass
import logging

from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.db import connect, ensure_schema, fetch_candidates, reapply_translation, record_error, save_translation
from ttrss_feed_translator.html_translate import translate_html
from ttrss_feed_translator.models import EntryCandidate
from ttrss_feed_translator.translator import OpenAICompatibleTranslator
from ttrss_feed_translator.workflow import plan_entry


logger = logging.getLogger(__name__)


@dataclass
class RunStats:
    translated: int = 0
    reapplied: int = 0
    skipped: int = 0
    failed: int = 0


def run_once(config: AppConfig) -> RunStats:
    stats = RunStats()
    translator = OpenAICompatibleTranslator(config)

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

        candidates = fetch_candidates(conn, config)
        logger.info("fetched %s candidate articles", len(candidates))

        for candidate in candidates:
            try:
                _process_candidate(conn, candidate, config, translator, stats)
                conn.commit()
            except Exception as exc:
                conn.rollback()
                logger.exception("entry %s failed: %s", candidate.entry_id, exc)
                record_error(conn, candidate, str(exc))
                conn.commit()
                stats.failed += 1

    logger.info(
        "finished run: translated=%s reapplied=%s skipped=%s failed=%s",
        stats.translated,
        stats.reapplied,
        stats.skipped,
        stats.failed,
    )
    return stats


def _process_candidate(
    conn,
    candidate: EntryCandidate,
    config: AppConfig,
    translator: OpenAICompatibleTranslator,
    stats: RunStats,
) -> None:
    plan = plan_entry(candidate, config.target_language, config.require_single_owner)

    logger.info(
        "entry=%s feed=%s action=%s reason=%s",
        candidate.entry_id,
        candidate.feed_title or candidate.feed_id,
        plan.action,
        plan.reason,
    )

    if plan.action == "skip":
        stats.skipped += 1
        return

    if plan.action == "reapply":
        if config.dry_run:
            logger.info("dry-run: would reapply cached translation for entry %s", candidate.entry_id)
        else:
            reapply_translation(conn, candidate)
        stats.reapplied += 1
        return

    translated_title = _translate_title(plan.source_title, translator)
    translated_content = translate_html(plan.source_content, translator)

    if config.dry_run:
        logger.info(
            "dry-run: would write translation for entry %s (title chars=%s, content chars=%s)",
            candidate.entry_id,
            len(translated_title),
            len(translated_content),
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
            target_language=config.target_language,
        )
    stats.translated += 1


def _translate_title(title: str, translator: OpenAICompatibleTranslator) -> str:
    if not title.strip():
        return title
    return translator.translate_texts([title])[0]
