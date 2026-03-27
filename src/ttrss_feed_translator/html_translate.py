from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Protocol

from bs4 import BeautifulSoup
from bs4 import Comment
from bs4 import NavigableString


SKIP_TAGS = {"script", "style", "noscript", "code", "pre", "textarea", "svg", "math"}


class TextBatchTranslator(Protocol):
    def translate_texts(self, texts: list[str]) -> list[str]:
        ...


@dataclass(frozen=True)
class TextNodeRef:
    node: NavigableString
    prefix: str
    core: str
    suffix: str


def _split_whitespace(text: str) -> tuple[str, str, str]:
    match = re.match(r"^(\s*)(.*?)(\s*)$", text, flags=re.DOTALL)
    if match is None:
        return "", text, ""
    return match.group(1), match.group(2), match.group(3)


def _looks_translatable(text: str) -> bool:
    return bool(re.search(r"[0-9A-Za-z\u00C0-\u024F\u0400-\u04FF\u0370-\u03FF]", text))


def _collect_text_nodes(html: str) -> tuple[BeautifulSoup, list[TextNodeRef]]:
    soup = BeautifulSoup(html, "html.parser")
    refs: list[TextNodeRef] = []

    for node in soup.find_all(string=True):
        if isinstance(node, Comment):
            continue

        parent = getattr(node, "parent", None)
        parent_name = getattr(parent, "name", None)
        if parent_name and parent_name.lower() in SKIP_TAGS:
            continue

        raw = str(node)
        if not raw or not raw.strip():
            continue

        prefix, core, suffix = _split_whitespace(raw)
        if not core or not _looks_translatable(core):
            continue

        refs.append(TextNodeRef(node=node, prefix=prefix, core=core, suffix=suffix))

    return soup, refs


def translate_html(html: str, translator: TextBatchTranslator) -> str:
    if not html.strip():
        return html

    soup, refs = _collect_text_nodes(html)
    if not refs:
        return html

    translated = translator.translate_texts([ref.core for ref in refs])
    if len(translated) != len(refs):
        raise ValueError("translator returned a different number of text nodes")

    for ref, translated_text in zip(refs, translated, strict=True):
        replacement = f"{ref.prefix}{translated_text}{ref.suffix}"
        ref.node.replace_with(NavigableString(replacement))

    return soup.decode(formatter="html")

