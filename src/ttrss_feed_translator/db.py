from __future__ import annotations

import json
import logging

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ttrss_feed_translator.config import AppConfig
from ttrss_feed_translator.models import EntryCandidate, TranslationRecord
from ttrss_feed_translator.tags import format_tag_cache, merge_tags, parse_tag_cache


logger = logging.getLogger(__name__)


TRACKING_TABLE_SQL = """
create table if not exists ttrss_entry_translations (
    entry_id integer primary key references ttrss_entries(id) on delete cascade,
    owner_uid integer not null references ttrss_users(id) on delete cascade,
    user_entry_id integer not null references ttrss_user_entries(int_id) on delete cascade,
    feed_id integer references ttrss_feeds(id) on delete set null,
    source_lang varchar(16),
    target_language varchar(32) not null,
    source_hash varchar(64) not null,
    source_title text not null,
    source_content text not null,
    translated_title text not null,
    translated_content text not null,
    generated_tags jsonb not null default '[]'::jsonb,
    translated_at timestamptz not null default now(),
    reapplied_at timestamptz,
    updated_at timestamptz not null default now(),
    last_error text
);

create index if not exists ttrss_entry_translations_owner_uid_idx
    on ttrss_entry_translations(owner_uid);

create index if not exists ttrss_entry_translations_target_language_idx
    on ttrss_entry_translations(target_language);

create index if not exists ttrss_entry_translations_translated_at_idx
    on ttrss_entry_translations(translated_at desc);
"""

TRACKING_TABLE_MIGRATIONS_SQL = """
alter table if exists ttrss_entry_translations
    add column if not exists user_entry_id integer references ttrss_user_entries(int_id) on delete cascade;

alter table if exists ttrss_entry_translations
    add column if not exists generated_tags jsonb not null default '[]'::jsonb;

update ttrss_entry_translations t
   set user_entry_id = ue.int_id
  from ttrss_user_entries ue
 where t.user_entry_id is null
   and ue.ref_id = t.entry_id
   and ue.owner_uid = t.owner_uid;
"""


def connect(database_url: str) -> psycopg.Connection:
    return psycopg.connect(database_url, row_factory=dict_row)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.cursor() as cur:
        cur.execute(TRACKING_TABLE_SQL)
        cur.execute(TRACKING_TABLE_MIGRATIONS_SQL)


def fetch_candidates(
    conn: psycopg.Connection,
    config: AppConfig,
    *,
    limit: int | None = None,
    offset: int = 0,
) -> list[EntryCandidate]:
    effective_limit = config.batch_size if limit is None else limit
    where_clauses = [
        "ue.owner_uid = %s",
        "e.date_entered >= now() - make_interval(hours => %s)",
    ]
    params: list[object] = [config.owner_uid, config.lookback_hours]

    if config.feed_ids:
        where_clauses.append("ue.feed_id = any(%s)")
        params.append(list(config.feed_ids))

    if config.source_langs:
        where_clauses.append("e.lang = any(%s)")
        params.append(list(config.source_langs))

    where_sql = " and ".join(where_clauses)
    query = f"""
        select *
        from (
            select distinct on (e.id)
                e.id as entry_id,
                ue.owner_uid,
                ue.int_id as user_entry_id,
                ue.feed_id,
                coalesce(f.title, '') as feed_title,
                coalesce(e.link, '') as link,
                e.title,
                e.content,
                coalesce(ue.tag_cache, '') as tag_cache,
                e.lang as source_lang,
                e.date_entered,
                (
                    select count(distinct owner_uid)
                    from ttrss_user_entries shared
                    where shared.ref_id = e.id
                ) as owner_count,
                t.entry_id as tracked_entry_id,
                t.owner_uid as tracked_owner_uid,
                t.feed_id as tracked_feed_id,
                t.source_lang as tracked_source_lang,
                t.target_language as tracked_target_language,
                t.source_hash as tracked_source_hash,
                t.source_title as tracked_source_title,
                t.source_content as tracked_source_content,
                t.translated_title as tracked_translated_title,
                t.translated_content as tracked_translated_content,
                t.generated_tags as tracked_generated_tags,
                t.translated_at as tracked_translated_at,
                t.reapplied_at as tracked_reapplied_at,
                t.updated_at as tracked_updated_at,
                t.last_error as tracked_last_error
            from ttrss_entries e
            join ttrss_user_entries ue on ue.ref_id = e.id
            left join ttrss_feeds f on f.id = ue.feed_id and f.owner_uid = ue.owner_uid
            left join ttrss_entry_translations t on t.entry_id = e.id
            where {where_sql}
            order by e.id, e.date_entered desc
        ) candidates
        order by date_entered desc, entry_id desc
        limit %s
        offset %s
    """
    params.extend([effective_limit, offset])

    with conn.cursor() as cur:
        cur.execute(query, params)
        rows = cur.fetchall()

    return [_row_to_candidate(row) for row in rows]


def save_translation(
    conn: psycopg.Connection,
    *,
    candidate: EntryCandidate,
    source_title: str,
    source_content: str,
    source_hash: str,
    translated_title: str,
    translated_content: str,
    generated_tags: tuple[str, ...],
    target_language: str,
) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            update ttrss_entries
               set title = %s,
                   content = %s
             where id = %s
            """,
            (translated_title, translated_content, candidate.entry_id),
        )
        _sync_generated_tags(cur, candidate, generated_tags)
        cur.execute(
            """
            insert into ttrss_entry_translations (
                entry_id,
                owner_uid,
                user_entry_id,
                feed_id,
                source_lang,
                target_language,
                source_hash,
                source_title,
                source_content,
                translated_title,
                translated_content,
                generated_tags,
                translated_at,
                reapplied_at,
                updated_at,
                last_error
            )
            values (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), null, now(), null
            )
            on conflict (entry_id) do update
                set owner_uid = excluded.owner_uid,
                    user_entry_id = excluded.user_entry_id,
                    feed_id = excluded.feed_id,
                    source_lang = excluded.source_lang,
                    target_language = excluded.target_language,
                    source_hash = excluded.source_hash,
                    source_title = excluded.source_title,
                    source_content = excluded.source_content,
                    translated_title = excluded.translated_title,
                    translated_content = excluded.translated_content,
                    generated_tags = excluded.generated_tags,
                    translated_at = now(),
                    reapplied_at = null,
                    updated_at = now(),
                    last_error = null
            """,
            (
                candidate.entry_id,
                candidate.owner_uid,
                candidate.user_entry_id,
                candidate.feed_id,
                candidate.source_lang,
                target_language,
                source_hash,
                source_title,
                source_content,
                translated_title,
                translated_content,
                Jsonb(list(generated_tags)),
            ),
        )


def reapply_translation(
    conn: psycopg.Connection,
    candidate: EntryCandidate,
    *,
    generated_tags: tuple[str, ...] | None = None,
    persist_generated_tags: bool = False,
) -> None:
    if candidate.translation is None:
        raise ValueError("cannot reapply translation without a tracking record")

    tags_to_apply = generated_tags if generated_tags is not None else candidate.translation.generated_tags
    with conn.cursor() as cur:
        cur.execute(
            """
            update ttrss_entries
               set title = %s,
                   content = %s
             where id = %s
            """,
            (
                candidate.translation.translated_title,
                candidate.translation.translated_content,
                candidate.entry_id,
            ),
        )
        _sync_generated_tags(cur, candidate, tags_to_apply)
        cur.execute(
            """
            update ttrss_entry_translations
               set reapplied_at = now(),
                   generated_tags = case when %s then %s else generated_tags end,
                   updated_at = now(),
                   last_error = null
             where entry_id = %s
            """,
            (
                persist_generated_tags,
                Jsonb(list(tags_to_apply)),
                candidate.entry_id,
            ),
        )


def record_error(conn: psycopg.Connection, candidate: EntryCandidate, message: str) -> None:
    if candidate.translation is None:
        return

    with conn.cursor() as cur:
        cur.execute(
            """
            update ttrss_entry_translations
               set last_error = %s,
                   updated_at = now()
             where entry_id = %s
            """,
            (message[:5000], candidate.entry_id),
        )


def _row_to_candidate(row: dict) -> EntryCandidate:
    record = None
    if row["tracked_entry_id"] is not None:
        record = TranslationRecord(
            entry_id=row["tracked_entry_id"],
            owner_uid=row["tracked_owner_uid"],
            user_entry_id=row["user_entry_id"],
            feed_id=row["tracked_feed_id"],
            source_lang=row["tracked_source_lang"],
            target_language=row["tracked_target_language"],
            source_hash=row["tracked_source_hash"],
            source_title=row["tracked_source_title"],
            source_content=row["tracked_source_content"],
            translated_title=row["tracked_translated_title"],
            translated_content=row["tracked_translated_content"],
            generated_tags=_parse_generated_tags(row["tracked_generated_tags"]),
            translated_at=row["tracked_translated_at"],
            reapplied_at=row["tracked_reapplied_at"],
            updated_at=row["tracked_updated_at"],
            last_error=row["tracked_last_error"],
        )

    return EntryCandidate(
        entry_id=row["entry_id"],
        owner_uid=row["owner_uid"],
        user_entry_id=row["user_entry_id"],
        feed_id=row["feed_id"],
        feed_title=row["feed_title"],
        link=row["link"],
        title=row["title"],
        content=row["content"],
        current_tags=parse_tag_cache(row["tag_cache"]),
        source_lang=row["source_lang"],
        date_entered=row["date_entered"],
        owner_count=row["owner_count"],
        translation=record,
    )


def sync_generated_tags(
    conn: psycopg.Connection,
    candidate: EntryCandidate,
    generated_tags: tuple[str, ...],
    *,
    persist: bool,
) -> None:
    with conn.cursor() as cur:
        _sync_generated_tags(cur, candidate, generated_tags)
        if persist:
            cur.execute(
                """
                update ttrss_entry_translations
                   set generated_tags = %s,
                       updated_at = now(),
                       last_error = null
                 where entry_id = %s
                """,
                (Jsonb(list(generated_tags)), candidate.entry_id),
            )


def _sync_generated_tags(cur: psycopg.Cursor, candidate: EntryCandidate, generated_tags: tuple[str, ...]) -> None:
    normalized_generated = merge_tags((), generated_tags)
    if not normalized_generated:
        return

    cur.execute(
        """
        select tag_name
          from ttrss_tags
         where owner_uid = %s
           and post_int_id = %s
         order by id
        """,
        (candidate.owner_uid, candidate.user_entry_id),
    )
    existing_tags = merge_tags([row["tag_name"] for row in cur.fetchall()], ())
    merged_tags = merge_tags(existing_tags, normalized_generated)

    if len(merged_tags) > len(existing_tags):
        existing_keys = {tag.casefold() for tag in existing_tags}
        missing_tags = [tag for tag in merged_tags if tag.casefold() not in existing_keys]
        cur.executemany(
            """
            insert into ttrss_tags (tag_name, owner_uid, post_int_id)
            values (%s, %s, %s)
            """,
            [(tag, candidate.owner_uid, candidate.user_entry_id) for tag in missing_tags],
        )

    cur.execute(
        """
        update ttrss_user_entries
           set tag_cache = %s
         where int_id = %s
           and owner_uid = %s
        """,
        (format_tag_cache(merged_tags), candidate.user_entry_id, candidate.owner_uid),
    )


def _parse_generated_tags(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, list):
        return merge_tags((), [str(item) for item in value])
    if isinstance(value, tuple):
        return merge_tags((), [str(item) for item in value])
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return parse_tag_cache(value)
        if isinstance(parsed, list):
            return merge_tags((), [str(item) for item in parsed])
    return ()
