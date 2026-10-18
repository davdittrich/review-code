"""Fixture tests for scripts/sanitize.py. Hidden characters are spelled as escapes, never raw."""
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "skills", "review-code", "scripts"))
import sanitize  # noqa: E402

ZWSP = chr(0x200B)


def tagged(word):
    """Spell `word` in Unicode tag characters (U+E0000 block), invisible in every renderer."""
    return "".join(chr(0xE0000 + ord(c)) for c in word)


# (name, input). Every input hides the word INJECT in one carrier, surrounded by visible text.
CARRIERS = [
    ("comment", "a <!-- INJECT --> b"),
    ("comment_nested", "a <!<!-- x -->-- INJECT --> b"),
    ("comment_unterminated", "a visible <!-- INJECT"),
    ("comment_entity", "a &lt;!-- INJECT --&gt; b"),
    ("comment_entity_double", "a &amp;lt;!-- INJECT --&amp;gt; b"),
    ("tag_chars", "a " + tagged("INJECT") + " b"),
    ("alt_inline", "a ![INJECT](u) b"),
    ("alt_reference", "a ![INJECT][r] b"),
    ("alt_shortcut", "a ![INJECT] b"),
    ("alt_nested_brackets", "a ![x [INJECT] y](u) b"),
    ("title_double", 'a [t](u "INJECT") b'),
    ("title_single", "a [t](u 'INJECT') b"),
    ("title_refdef", 'a\n\n[r]: http://u "INJECT"\n\nb'),
    ("title_refdef_nextline", 'a\n\n[r]: http://u\n"INJECT"\n\nb'),
    ("ref_comment_hash", "a\n[//]: # (INJECT)\nb"),
    ("ref_comment_empty_target", "a\n[//]: <> (INJECT)\nb"),
    ("ref_comment_quoted", 'a\n[//]: # "INJECT"\nb'),
    ("img_alt", 'a <img alt="INJECT" src="x"> b'),
    ("img_alt_gt", 'a <img alt="1>INJECT" src="x"> b'),
    ("anchor_title", 'a <a href="u" title="INJECT">t</a> b'),
    ("hidden_attr", "a <div hidden>INJECT</div> b"),
    ("hidden_unclosed", "a <div hidden>INJECT"),
    ("display_none", 'a <span style="display: none">INJECT</span> b'),
    ("visibility_hidden", 'a <span style="VISIBILITY :  hidden">INJECT</span> b'),
    ("font_size_zero", 'a <p style="font-size:0">INJECT</p> b'),
    ("opacity_zero", 'a <p style="opacity: 0">INJECT</p> b'),
    ("script", "a <script>INJECT</script> b"),
    ("style", "a <style>INJECT</style> b"),
    ("template", "a <template>INJECT</template> b"),
    ("noscript", "a <noscript>INJECT</noscript> b"),
    ("nested_hidden", "a <div hidden><span>x</span>INJECT</div> b"),
    ("doctype", "a <!DOCTYPE INJECT> b"),
    ("pi", "a <?php INJECT ?> b"),
    ("cdata", "a <![CDATA[ INJECT ]]> b"),
    ("unlisted_tag_attr", "a <foo bar='INJECT'>text</foo> b"),
    ("unlisted_tag_attr_unquoted", "a <Foo data-x=INJECT>text</Foo> b"),
    ("backtick_pairs_across_paragraphs", "a ` here\n\n<!-- INJECT -->\n\nb ` there"),
    ("backtick_fence_bad_info", "a\n\n```x`y\n<!-- INJECT -->\nvisible"),
    ("span_across_blank_line", "a `x\n\n<!-- INJECT -->\n\ny` b"),
]

# Carriers whose payload is the invisible character itself.
INVISIBLE_ONLY = [
    ("zwsp", "ZW" + ZWSP + "SPLIT"),
    ("zwsp_entity", "ZW&#x200b;SPLIT"),
    ("bom", "ZW" + chr(0xFEFF) + "SPLIT"),
    ("variation", "ZW️SPLIT"),
    ("bidi", "ZW" + chr(0x202E) + "SPLIT"),
    ("esc", "ZW\x1b[31mSPLIT"),
]


def combined():
    return "\n".join([
        "Fixes the parser",
        "",
        "```html",
        "<!-- keep -->",
        "```",
        "",
        "Uses `Vec<T>` here.",
        "a <!<!-- x -->-- INJECT --> b",
        "x " + tagged("INJECT") + " y",
        "![INJECT](u) ![INJECT][r] [t](u \"INJECT\")",
        "[//]: # (INJECT)",
        '<img alt="INJECT" src="x">',
        "<div hidden>INJECT</div>",
        '<span style="display: none">INJECT</span>',
        '<p style="font-size:0">INJECT</p>',
        "<script>INJECT</script>",
        "<!-- INJECT -->",
        "&lt;!-- INJECT --&gt;",
        "ZW" + ZWSP + "SPLIT",
        "<!-- INJECT",
    ])


class ProseTests(unittest.TestCase):
    def test_prose_combined_payload(self):
        out = sanitize.sanitize_prose(combined())
        self.assertNotIn("inject", out.lower())
        self.assertEqual(sanitize.strip_invisible(out), out)
        self.assertIn("Fixes the parser", out)
        self.assertIn("```html\n<!-- keep -->\n```", out)
        self.assertIn("`Vec<T>`", out)
        self.assertIn("ZWSPLIT", out)

    def test_prose_each_carrier(self):
        for name, text in CARRIERS:
            with self.subTest(carrier=name):
                out = sanitize.sanitize_prose(text)
                self.assertNotIn("inject", out.lower(), repr(out))
                self.assertTrue(out.startswith("a"), repr(out))
        for name, text in INVISIBLE_ONLY:
            with self.subTest(carrier=name):
                out = sanitize.sanitize_prose(text)
                self.assertEqual(sanitize.strip_invisible(out), out)
                self.assertTrue(out.startswith("ZW"), repr(out))
                self.assertIn("SPLIT", out)

    def test_prose_keeps_visible(self):
        keep = [
            "a < b and x <= y",
            "Uses <T> and Vec<T, U> outside code",
            "Tom & Jerry",
            "see [docs](u) and ![](u)",
            "tab\tand\nnewlines\r\nkept",
            "`<!-- keep -->` inline",
            "~~~\n<!-- keep -->\n<div hidden>keep</div>\n~~~",
            "``a ` b <!-- keep -->``",
            "see <https://x.y/z?a=b&c=d> and <me@x.com>",
        ]
        for text in keep:
            with self.subTest(text=text):
                self.assertEqual(sanitize.sanitize_prose(text), text)
        out = sanitize.sanitize_prose("<details><summary>More</summary>shown</details>")
        self.assertIn("More", out)
        self.assertIn("shown", out)
        self.assertEqual(sanitize.sanitize_prose("Tom &amp; Jerry &lt;3"), "Tom & Jerry <3")

    def test_prose_forged_token_does_not_crash(self):
        out = sanitize.sanitize_prose("x 999 y `z`")
        self.assertIn("`z`", out)


class EscapeTests(unittest.TestCase):
    def test_escape_trojan_source(self):
        src = "\r\n".join([
            "if x:\t# safe \u202e",
            "    y = '\u2066\u2067\u2068\u2069'",
            "z = 1\u200b\ufeff" + tagged("A") + "\ufe0f\u2028end",
            "\x1bq",
        ])
        out, hits = sanitize.escape_for_code(src)
        self.assertEqual(out.count("\n"), src.count("\n"))
        self.assertEqual(len(out.split("\n")), 4)
        lines = out.split("\r\n")
        self.assertEqual(lines[0], "if x:\t# safe <U+202E>")
        self.assertEqual(lines[1], "    y = '<U+2066><U+2067><U+2068><U+2069>'")
        self.assertEqual(lines[2], "z = 1<U+200B><U+FEFF><U+E0041><U+FE0F><U+2028>end")
        self.assertEqual(lines[3], "<U+001B>q")
        self.assertEqual(hits, [
            {"line": 1, "codepoints": ["U+202E"]},
            {"line": 2, "codepoints": ["U+2066", "U+2067", "U+2068", "U+2069"]},
            {"line": 3, "codepoints": ["U+200B", "U+FEFF", "U+E0041", "U+FE0F", "U+2028"]},
            {"line": 4, "codepoints": ["U+001B"]},
        ])
        self.assertEqual(sanitize.strip_invisible(out), out)

    def test_escape_clean_code(self):
        src = "def f(a, b):\n\treturn a < b  # caf\u00e9 \u00e9\u0301\r\n"
        self.assertEqual(sanitize.escape_for_code(src), (src, []))


class EdgeTests(unittest.TestCase):
    def test_edge_empty(self):
        self.assertEqual(sanitize.sanitize_prose(None), "")
        self.assertEqual(sanitize.sanitize_prose(""), "")
        self.assertEqual(sanitize.escape_for_code(""), ("", []))
        self.assertEqual(sanitize.strip_invisible(""), "")

    def test_edge_encoding(self):
        fullwidth = "\uff49\uff47\uff4e\uff4f\uff52\uff45"  # no NFKC: homoglyphs are a documented limit
        self.assertEqual(sanitize.sanitize_prose(fullwidth), fullwidth)
        family = "\U0001f468\u200d\U0001f469\u200d\U0001f467"
        self.assertEqual(sanitize.sanitize_prose(family), "\U0001f468\U0001f469\U0001f467")
        accent = "e\u0301 a\u0308"
        self.assertEqual(sanitize.sanitize_prose(accent), accent)
        self.assertEqual(sanitize.escape_for_code(accent), (accent, []))

    def test_edge_idempotent(self):
        deep = "&lt;!-- INJECT --&gt;"
        for _ in range(40):
            deep = deep.replace("&", "&amp;")
        # removing "&" after the cap joins "!" and "[": a second pass must still settle on the same text
        fixtures = [t for _, t in CARRIERS] + [t for _, t in INVISIBLE_ONLY] + [combined(), deep, deep + " !&[x](u)"]
        for text in fixtures:
            with self.subTest(text=text[:40]):
                once = sanitize.sanitize_prose(text)
                self.assertEqual(sanitize.sanitize_prose(once), once)
        start = time.monotonic()
        out = sanitize.sanitize_prose(deep)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertFalse(any(c in out for c in "<>&"))
        self.assertTrue(out.endswith(" [sanitizer: markup removed]"))
        self.assertEqual(sanitize.sanitize_prose(out), out)


if __name__ == "__main__":
    unittest.main()
