from __future__ import annotations

import unittest

from ttrss_feed_translator.html_translate import translate_html


class FakeTranslator:
    def translate_texts(self, texts: list[str]) -> list[str]:
        mapping = {
            "Hello": "你好",
            "world": "世界",
            "Tail text": "尾部文本",
        }
        return [mapping.get(text, f"T({text})") for text in texts]


class HtmlTranslateTests(unittest.TestCase):
    def test_only_text_nodes_are_translated(self) -> None:
        html = "<p>Hello <strong>world</strong></p><pre>print('x')</pre><div>Tail text</div>"
        translated = translate_html(html, FakeTranslator())

        self.assertIn("<p>你好 <strong>世界</strong></p>", translated)
        self.assertIn("<pre>print('x')</pre>", translated)
        self.assertIn("<div>尾部文本</div>", translated)


if __name__ == "__main__":
    unittest.main()
