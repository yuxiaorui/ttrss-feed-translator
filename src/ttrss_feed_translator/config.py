from __future__ import annotations

from dataclasses import dataclass
import os


def _require(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ValueError(f"missing required environment variable: {name}")
    return value


def _parse_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default

    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    raise ValueError(f"invalid boolean for {name}: {raw}")


def _parse_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default

    value = int(raw)
    if value < minimum:
        raise ValueError(f"{name} must be >= {minimum}")
    return value


def _parse_csv_strings(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.getenv(name)
    if raw is None:
        return default

    values = tuple(item.strip().lower() for item in raw.split(",") if item.strip())
    return values


def _parse_csv_ints(name: str) -> tuple[int, ...]:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return ()

    return tuple(int(item.strip()) for item in raw.split(",") if item.strip())


def _require_csv_ints(name: str) -> tuple[int, ...]:
    values = _parse_csv_ints(name)
    if not values:
        raise ValueError(f"{name} must contain at least one feed id")
    return values


@dataclass(frozen=True)
class AppConfig:
    database_url: str
    owner_uid: int
    target_language: str
    source_langs: tuple[str, ...]
    feed_ids: tuple[int, ...]
    lookback_hours: int
    batch_size: int
    loop_interval_seconds: int
    require_single_owner: bool
    dry_run: bool
    api_base_url: str
    api_key: str
    model: str
    request_timeout_seconds: int
    max_texts_per_request: int
    max_chars_per_request: int
    ai_tagging_enabled: bool
    ai_tagging_max_tags: int
    ai_tagging_language: str
    log_level: str

    @classmethod
    def from_env(cls) -> "AppConfig":
        api_base_url = os.getenv("TRANSLATOR_API_BASE_URL", "https://api.openai.com/v1").strip()
        log_level = os.getenv("TRANSLATOR_LOG_LEVEL", "INFO").strip().upper()
        target_language = os.getenv("TRANSLATOR_TARGET_LANGUAGE", "zh-CN").strip() or "zh-CN"

        return cls(
            database_url=_require("TRANSLATOR_DATABASE_URL"),
            owner_uid=_parse_int("TRANSLATOR_OWNER_UID", 1, minimum=1),
            target_language=target_language,
            source_langs=_parse_csv_strings("TRANSLATOR_SOURCE_LANGS"),
            feed_ids=_require_csv_ints("TRANSLATOR_FEED_IDS"),
            lookback_hours=_parse_int("TRANSLATOR_LOOKBACK_HOURS", 48, minimum=1),
            batch_size=_parse_int("TRANSLATOR_BATCH_SIZE", 10, minimum=1),
            loop_interval_seconds=_parse_int("TRANSLATOR_LOOP_INTERVAL_SECONDS", 300, minimum=1),
            require_single_owner=_parse_bool("TRANSLATOR_REQUIRE_SINGLE_OWNER", True),
            dry_run=_parse_bool("TRANSLATOR_DRY_RUN", False),
            api_base_url=api_base_url.rstrip("/"),
            api_key=_require("TRANSLATOR_API_KEY"),
            model=_require("TRANSLATOR_MODEL"),
            request_timeout_seconds=_parse_int("TRANSLATOR_REQUEST_TIMEOUT_SECONDS", 120, minimum=1),
            max_texts_per_request=_parse_int("TRANSLATOR_MAX_TEXTS_PER_REQUEST", 40, minimum=1),
            max_chars_per_request=_parse_int("TRANSLATOR_MAX_CHARS_PER_REQUEST", 8000, minimum=100),
            ai_tagging_enabled=_parse_bool("TRANSLATOR_ENABLE_AI_TAGGING", False),
            ai_tagging_max_tags=_parse_int("TRANSLATOR_AI_TAGGING_MAX_TAGS", 6, minimum=1),
            ai_tagging_language=os.getenv("TRANSLATOR_AI_TAGGING_LANGUAGE", target_language).strip() or target_language,
            log_level=log_level or "INFO",
        )
