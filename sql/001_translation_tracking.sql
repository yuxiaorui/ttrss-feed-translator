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
