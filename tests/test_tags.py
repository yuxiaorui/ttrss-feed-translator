from __future__ import annotations

import unittest

from ttrss_feed_translator.tags import extract_text_for_tagging, format_tag_cache, merge_tags, parse_tag_cache


class TagHelpersTests(unittest.TestCase):
    def test_parse_tag_cache_splits_and_deduplicates(self) -> None:
        parsed = parse_tag_cache("ai, openai,, AI , startups")

        self.assertEqual(parsed, ("ai", "openai", "startups"))

    def test_merge_tags_preserves_order_and_respects_limit(self) -> None:
        merged = merge_tags(("ai", "openai"), ("OpenAI", "startups", "venture"), limit=3)

        self.assertEqual(merged, ("ai", "openai", "startups"))

    def test_format_tag_cache_matches_ttrss_style(self) -> None:
        formatted = format_tag_cache(("ai", "openai", "startups"))

        self.assertEqual(formatted, "ai,openai,startups")

    def test_extract_text_for_tagging_flattens_html_and_truncates(self) -> None:
        text = extract_text_for_tagging("<p>Hello <strong>world</strong></p><p>Again</p>", max_chars=12)

        self.assertEqual(text, "Hello\nworld")


if __name__ == "__main__":
    unittest.main()
