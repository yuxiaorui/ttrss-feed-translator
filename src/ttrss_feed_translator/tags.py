from __future__ import annotations

import re
from typing import Iterable, Sequence

from bs4 import BeautifulSoup


_TAG_SPLIT_RE = re.compile(r"[,;\n]+")


def parse_tag_cache(raw: str | None) -> tuple[str, ...]:
    if raw is None:
        return ()
    return merge_tags(_TAG_SPLIT_RE.split(raw), ())


def merge_tags(existing: Sequence[str], generated: Sequence[str], *, limit: int | None = None) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()

    for source in (existing, generated):
        for candidate in source:
            for cleaned in _iter_clean_tags(candidate):
                key = cleaned.casefold()
                if key in seen:
                    continue

                merged.append(cleaned)
                seen.add(key)
                if limit is not None and len(merged) >= limit:
                    return tuple(merged)

    return tuple(merged)


def format_tag_cache(tags: Sequence[str]) -> str:
    return ",".join(merge_tags(tags, ()))


def extract_text_for_tagging(html: str, *, max_chars: int) -> str:
    text = BeautifulSoup(html, "html.parser").get_text(separator="\n", strip=True)
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].strip() or text[:max_chars].strip()


def _iter_clean_tags(tag: str) -> Iterable[str]:
    if not tag:
        return

    for part in _TAG_SPLIT_RE.split(tag):
        cleaned = re.sub(r"\s+", " ", part).strip(" \t\r\n#'\"`.,;:[](){}")
        if cleaned:
            yield cleaned
