from __future__ import annotations

import unittest

from ttrss_feed_translator.html_translate import translate_html, translate_title_and_html


class FakeTranslator:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def translate_texts(self, texts: list[str]) -> list[str]:
        self.calls.append(list(texts))
        mapping = {
            "Hello": "你好",
            "world": "世界",
            "Tail text": "尾部文本",
            "Headline": "标题",
        }
        return [mapping.get(text, f"T({text})") for text in texts]


class HtmlTranslateTests(unittest.TestCase):
    def test_only_text_nodes_are_translated(self) -> None:
        html = "<p>Hello <strong>world</strong></p><pre>print('x')</pre><div>Tail text</div>"
        translator = FakeTranslator()
        translated = translate_html(html, translator)

        self.assertIn("<p>你好 <strong>世界</strong></p>", translated)
        self.assertIn("<pre>print('x')</pre>", translated)
        self.assertIn("<div>尾部文本</div>", translated)
        self.assertEqual(translator.calls, [["Hello", "world", "Tail text"]])

    def test_title_and_html_share_one_translation_batch(self) -> None:
        translator = FakeTranslator()

        translated_title, translated_html = translate_title_and_html(
            "Headline",
            "<p>Hello <strong>world</strong></p><div>Tail text</div>",
            translator,
        )

        self.assertEqual(translated_title, "标题")
        self.assertIn("<p>你好 <strong>世界</strong></p>", translated_html)
        self.assertIn("<div>尾部文本</div>", translated_html)
        self.assertEqual(translator.calls, [["Headline", "Hello", "world", "Tail text"]])

    def test_blank_title_does_not_add_extra_translation_item(self) -> None:
        translator = FakeTranslator()

        translated_title, translated_html = translate_title_and_html("   ", "<p>Hello</p>", translator)

        self.assertEqual(translated_title, "   ")
        self.assertIn("<p>你好</p>", translated_html)
        self.assertEqual(translator.calls, [["Hello"]])


if __name__ == "__main__":
    unittest.main()
