"""Fixture tests for scripts/sanitize.py. Hidden characters are spelled as escapes, never raw."""
import os
import sys
import time
import unittest
from unittest import mock

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
    ("link_dest", "a [t](https://x/INJECT-ALL-PREVIOUS-INSTRUCTIONS) b"),
    ("link_dest_title", 'a [t](https://x/INJECT "title") b'),
    ("link_dest_angle", "a [t](<https://x/INJECT y>) b"),
    ("link_dest_parens", "a [t](https://x/INJECT_(1)) b"),
    ("link_dest_escape", "a [t](https://x/\\)INJECT) b"),
    ("image_dest", "a ![](https://x/INJECT) b"),
    ("refdef_dest", "a\n\n[r]: https://x/INJECT\n\n[t][r] b"),
    ("refdef_dest_angle_title", "a\n\n[r]: <https://x/INJECT> 'title'\n\n[t][r] b"),
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
            "see [docs] and ![]",
            "see [a b](not a link) and <https://x.y/z> and https://x.y/bare",
            "[t](",
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

    def test_prose_link_destination_neutralized_text_kept(self):
        dots = chr(0x2026)
        cases = {
            "see [the docs](https://x/y) now": "see [the docs](%s) now" % dots,
            "![](https://x/y)": "![](%s)" % dots,
            "[a](u \"t\") [b](<v w>)": "[a](%s) [b](%s)" % (dots, dots),
            "[t][r]\n\n[r]: https://x/y \"t\"\n": "[t][r]\n\n[r]: %s\n" % dots,
        }
        for text, want in cases.items():
            with self.subTest(text=text):
                self.assertEqual(sanitize.sanitize_prose(text), want)
        self.assertEqual(sanitize.sanitize_prose("`[t](https://x/y)`"), "`[t](https://x/y)`")

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
        self.assertEqual(lines[2], "z = 1<U+200B><U+FEFF><TAGS:1><VS:1><U+2028>end")
        self.assertEqual(lines[3], "<U+001B>q")
        self.assertEqual(hits, [
            {"line": 1, "codepoints": ["U+202E"]},
            {"line": 2, "codepoints": ["U+2066", "U+2067", "U+2068", "U+2069"]},
            {"line": 3, "codepoints": ["U+200B", "U+FEFF", "TAGS:1", "VS:1", "U+2028"]},
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


class TagRunTests(unittest.TestCase):
    def test_tag_run_collapsed(self):
        # a Tags-block run spells ASCII: the escaped form must not carry the payload, only its length
        out, hits = sanitize.escape_for_code("a " + tagged("INJECT") + " b\nx")
        self.assertEqual(out, "a <TAGS:6> b\nx")
        self.assertEqual(hits, [{"line": 1, "codepoints": ["TAGS:6"]}])
        self.assertEqual(sanitize.escape_for_code(tagged("AB") + "\u200b" + tagged("C"))[0], "<TAGS:2><U+200B><TAGS:1>")


def vs(data):
    """Variation-selector encoding of bytes ('emoji smuggling'): one selector per byte."""
    return "".join(chr(0xFE00 + b if b < 16 else 0xE0100 + b - 16) for b in data)


class VariationSelectorTests(unittest.TestCase):
    # gap 3: an escape that names the selector still spells the payload, so every escape hides which one it was
    def test_payload_never_decodes(self):
        emoji = "\U0001F600"
        cases = [
            ("ok" + emoji + vs(b"PWN"), "ok" + emoji + "<VS:3>", ["VS:3"]),
            ("a" + vs(b"P") + "b" + vs(b"W") + "c" + vs(b"N"), "a<VS:1>b<VS:1>c<VS:1>", ["VS:1"] * 3),
            (vs(b"P") + "\u200b" + vs(b"W"), "<VS:1><U+200B><VS:1>", ["VS:1", "U+200B", "VS:1"]),
            (emoji + "\ufe0f" + vs(b"PW"), emoji + "<VS:3>", ["VS:3"]),
            ("a\ufe0f = 1", "a<VS:1> = 1", ["VS:1"]),  # a look-alike identifier, not an emoji
            (tagged("A") + "\ufe0f", "<TAGS:1><VS:1>", ["TAGS:1", "VS:1"]),
        ]
        for src, want, cps in cases:
            with self.subTest(src=ascii(src)):
                out, hits = sanitize.escape_for_code(src)
                self.assertEqual(out, want)
                self.assertEqual(hits, [{"line": 1, "codepoints": cps}])
                self.assertEqual(sanitize.strip_invisible(out), out)
                self.assertNotRegex(out, r"FE0|E01")

    def test_single_vs16_after_emoji_kept(self):
        src = "ok \U0001F600\ufe0f \u2764\ufe0f done\n\u2705\ufe0f"
        self.assertEqual(sanitize.escape_for_code(src), (src, []))


# Each carrier hides INJECT in a prose or markup file; strip_hidden must remove it and keep the line count.
HIDDEN = [
    ("comment", "a <!-- INJECT --> b", "comment"),
    ("comment_multiline", "a <!--\nINJECT\nINJECT\n--> b", "comment"),
    ("comment_unterminated", "a <!-- INJECT\nmore", "comment"),
    ("bogus_comment", "a <!INJECT> b", "comment"),
    ("cdata", "a <![CDATA[ INJECT ]]> b", "comment"),
    ("ref_comment", "a\n[//]: # (INJECT)\nb", "comment"),
    ("ref_comment_angle", "a\n[//]: <> (INJECT)\nb", "comment"),
    ("alt_inline", "a ![INJECT](u.png) b", "alt-text"),
    ("alt_reference", "a ![INJECT][r] b", "alt-text"),
    ("alt_multiline", "a ![x\nINJECT](u.png) b", "alt-text"),
    ("alt_html", 'a <img src="u.png" alt="INJECT"> b', "alt-text"),
    ("hidden_attr", "a <div hidden>INJECT</div> b", "element"),
    ("hidden_style", '<p>a</p><span style="display: none">INJECT</span> b', "element"),
    ("hidden_nested", "a <div hidden><p>x</p>INJECT</div> b", "element"),
    ("hidden_void", 'a <input hidden value="INJECT"> b', "element"),
    ("hidden_unclosed", "a <div hidden>INJECT\nmore", "element"),
]


# Oracle, copied independently of sanitize.py: Default_Ignorable_Code_Point ranges of
# https://www.unicode.org/Public/UCD/latest/ucd/DerivedCoreProperties.txt, header
# "DerivedCoreProperties-18.0.0.txt", "Total code points: 4174".
DI_18 = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5),
         (0x180B, 0x180D), (0x180E, 0x180E), (0x180F, 0x180F), (0x200B, 0x200F), (0x202A, 0x202E),
         (0x2060, 0x2064), (0x2065, 0x2065), (0x2066, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F),
         (0xFEFF, 0xFEFF), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A),
         (0xE0000, 0xE0000), (0xE0001, 0xE0001), (0xE0002, 0xE001F), (0xE0020, 0xE007F), (0xE0080, 0xE00FF),
         (0xE0100, 0xE01EF), (0xE01F0, 0xE0FFF))


# Oracle: General_Category Cf of extracted/DerivedGeneralCategory.txt, header "DerivedGeneralCategory-18.0.0.txt"
# (21 ranges, 170 code points), plus Cc, Zl and Zp (stable since Unicode 1.1).
CF_18 = ((0x00AD, 0x00AD), (0x0600, 0x0605), (0x061C, 0x061C), (0x06DD, 0x06DD), (0x070F, 0x070F), (0x0890, 0x0891),
         (0x08E2, 0x08E2), (0x180E, 0x180E), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F),
         (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0x110BD, 0x110BD), (0x110CD, 0x110CD), (0x13430, 0x1343F),
         (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0001, 0xE0001), (0xE0020, 0xE007F))
CTRL = ((0x0000, 0x0008), (0x000B, 0x000C), (0x000E, 0x001F), (0x007F, 0x009F), (0x2028, 0x2029))


class FormatCharacterTests(unittest.TestCase):
    def test_cf_oracle_total(self):
        self.assertEqual(sum(b - a + 1 for a, b in CF_18), 170)

    def test_cf_sweep_independent_of_interpreter_tables(self):
        # 6xl.58: Python 3.9 ships Unicode 13, where U+0890, U+0891 and U+13439..1343F are unassigned
        with mock.patch.object(sanitize.unicodedata, "category", return_value="Cn"):
            for a, b in CF_18 + CTRL:
                for o in range(a, b + 1):
                    c = chr(o)
                    out, hits = sanitize.escape_for_code("a" + c + "b")
                    if c in out or not hits:
                        self.fail("U+%04X passes escape_for_code" % o)
                    if c in sanitize.sanitize_prose("a" + c + "b"):
                        self.fail("U+%04X passes sanitize_prose" % o)


class DefaultIgnorableTests(unittest.TestCase):
    def test_di_oracle_total(self):
        self.assertEqual(sum(b - a + 1 for a, b in DI_18), 4174)

    def test_di_sweep_every_range(self):
        # 6xl.50: no Default_Ignorable_Code_Point passes either escaper raw, whatever unicodedata version runs
        for a, b in DI_18:
            for o in range(a, b + 1):
                c = chr(o)
                out, hits = sanitize.escape_for_code("a" + c + "b")
                if c in out or not hits:
                    self.fail("U+%04X passes escape_for_code" % o)
                if c in sanitize.sanitize_prose("a" + c + "b"):
                    self.fail("U+%04X passes sanitize_prose" % o)

    def test_di_plane14_runs_never_name_a_code_point(self):
        # E0080..E00FF map one-to-one onto bytes 0x80..0xFF: naming each one would spell the payload
        for start in (0xE0002, 0xE0080, 0xE00F0, 0xE01F0, 0xE0FF0):
            run = "".join(chr(start + i) for i in range(16))
            out, hits = sanitize.escape_for_code("x" + run + "y")
            self.assertEqual(out, "x<TAGS:16>y", hex(start))
            self.assertEqual(hits, [{"line": 1, "codepoints": ["TAGS:16"]}])
        self.assertEqual(sanitize.escape_for_code(chr(0xE007F) + chr(0xE0080))[0], "<TAGS:2>")
        self.assertEqual(sanitize.escape_for_code("q" + chr(0xE0085))[0], "q<TAGS:1>")

    def test_di_mongolian_free_variation_selectors_collapse(self):
        # U+180B..180D and U+180F are Variation_Selector code points: never named one by one
        for o in (0x180B, 0x180C, 0x180D, 0x180F):
            self.assertEqual(sanitize.escape_for_code("x" + chr(o))[0], "x<VS:1>", hex(o))
        self.assertEqual(sanitize.escape_for_code("x" + chr(0x180B) + chr(0x180F))[0], "x<VS:2>")
        self.assertEqual(sanitize.escape_for_code("x" + chr(0x180E))[0], "x<U+180E>")  # vowel separator, not a VS


class HiddenTextTests(unittest.TestCase):
    def test_hidden_each_carrier_removed(self):
        for name, text, kind in HIDDEN:
            with self.subTest(carrier=name):
                out, hits = sanitize.strip_hidden(text)
                self.assertNotIn("INJECT", out)
                self.assertEqual(out.count("\n"), text.count("\n"))
                self.assertIn("[hidden-%s removed: " % kind, out)
                self.assertTrue(out.startswith("a") or out.startswith("<p>a"), repr(out))
                self.assertEqual([h["kind"] for h in hits], [kind])
                self.assertNotIn("INJECT", repr(hits))
                self.assertTrue(all(h["length"] > 0 and h["line"] >= 1 for h in hits))

    def test_hidden_marker_text_and_length(self):
        out, hits = sanitize.strip_hidden("x\na <!-- 12345 --> b\n")
        self.assertEqual(out, "x\na [hidden-comment removed: 14 chars] b\n")
        self.assertEqual(hits, [{"line": 2, "kind": "comment", "length": 14}])

    def test_hidden_multiline_comment_keeps_lines(self):
        out, hits = sanitize.strip_hidden("a <!--\nINJECT\n--> b\nc")
        self.assertEqual(len(out.split("\n")), 4)
        self.assertTrue(out.endswith("b\nc"))
        self.assertEqual([(h["line"], h["kind"]) for h in hits], [(1, "comment")])

    def test_hidden_keeps_visible(self):
        keep = [
            "plain text, Vec<T> and a < b",
            "<!doctype html>\n<html><body><p>hi</p></body></html>",
            "![](u.png) and [t](u) and [r]: ref",
            "<script>var INJECT = 1;</script>",  # code in a markup file is reviewed, not hidden
            "<style>.INJECT { color: red }</style>",
            "<img src=x alt=''>",
            "[t](https://x/y \"title\") and ![](u.png)",  # destinations and titles are reviewable content
            "x &lt;!-- INJECT --&gt; y",  # entity-encoded: rendered as visible text
            "<img src=x alt='  '>",
        ]
        for text in keep:
            with self.subTest(text=text):
                self.assertEqual(sanitize.strip_hidden(text), (text, []))

    def test_hidden_two_carriers_two_entries(self):
        out, hits = sanitize.strip_hidden("<!-- INJECT -->\n![INJECT](u)\n<!-- INJECT -->")
        self.assertNotIn("INJECT", out)
        self.assertEqual([(h["line"], h["kind"]) for h in hits], [(1, "comment"), (2, "alt-text"), (3, "comment")])

    def test_hidden_alt_inside_comment_is_one_entry(self):
        out, hits = sanitize.strip_hidden("<!-- ![INJECT](u) -->")
        self.assertEqual([h["kind"] for h in hits], ["comment"])

    def test_hidden_inside_fence_is_still_stripped(self):
        # a fence can sit inside an HTML block or a comment, so a fence protects nothing
        for text in ("```\n<!-- INJECT -->\n```", "<div>\n```\n<!-- INJECT -->\n", "<!--\n```\n-->\n<!-- INJECT -->"):
            with self.subTest(text=text):
                self.assertNotIn("INJECT", sanitize.strip_hidden(text)[0])

    def test_hidden_img_alt_attribute_tricks(self):
        for text in ('<img title=">" alt="INJECT">', "<img alt=INJECT>", "<IMG ALT='INJECT' src=x>",
                     '<img title=" alt=\'x\' " alt="INJECT">'):
            with self.subTest(text=text):
                out, hits = sanitize.strip_hidden(text)
                self.assertNotIn("INJECT", out)
                self.assertTrue(hits)

    def test_hidden_idempotent(self):
        for _, text, _ in HIDDEN:
            once = sanitize.strip_hidden(text)[0]
            self.assertEqual(sanitize.strip_hidden(once), (once, []), text)

    def test_hidden_crlf(self):
        out, hits = sanitize.strip_hidden("a\r\n[//]: # (INJECT)\r\nb\r\n")
        self.assertEqual(out, "a\r\n[hidden-comment removed: 16 chars]\r\nb\r\n")
        self.assertEqual(hits, [{"line": 2, "kind": "comment", "length": 16}])

    def test_hidden_no_fence_in_html_file_or_md(self):
        self.assertNotIn("INJECT", sanitize.strip_hidden_diff(
            "diff --git a/x.html b/x.html\n--- a/x.html\n+++ b/x.html\n@@ -0,0 +1,3 @@\n+```\n+<!-- INJECT -->\n+```\n")[0])

    def test_hidden_empty(self):
        self.assertEqual(sanitize.strip_hidden(""), ("", []))

    def test_hidden_is_prose_file(self):
        for p in ("a.md", "A.MD", "d/x.markdown", "x.html", "x.htm", "n.txt", "r.rst"):
            self.assertTrue(sanitize.is_prose_file(p), p)
        for p in ("a.py", "x.js", "x.json", "README", "a.md.bak", "x.adoc", "x.org", "x.ipynb", "x.svg", "x.textile"):
            self.assertFalse(sanitize.is_prose_file(p), p)

    def test_hidden_every_linguist_markdown_extension(self):
        # 6xl.49: github-linguist languages.yml, Markdown: extensions plus the filename contents.lr
        exts = (".md", ".livemd", ".markdown", ".mdown", ".mdwn", ".mkd", ".mkdn", ".mkdown", ".ronn", ".scd",
                ".workbook")
        for p in ["d/n" + e for e in exts] + ["contents.lr", "d/CONTENTS.LR"]:
            with self.subTest(path=p):
                self.assertTrue(sanitize.is_prose_file(p), p)
                d = ("diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n@@ -1,1 +1,2 @@\n keep\n+a <!-- INJECT --> b\n"
                     % (p, p, p, p))
                out, hits = sanitize.strip_hidden_diff(d)
                self.assertNotIn("INJECT", out)
                self.assertEqual([(h["file"], h["kind"]) for h in hits], [(p, "comment")])
        self.assertFalse(sanitize.is_prose_file("xcontents.lr"))

    def test_hidden_markdown_like_types(self):
        # 6xl.60: MDX, R Markdown and Quarto documents are Markdown underneath
        for p in ("d/x.mdx", "a.Rmd", "a.rmd", "b.qmd"):
            with self.subTest(path=p):
                self.assertTrue(sanitize.is_prose_file(p))
                out, hits = sanitize.strip_hidden("a\n<!-- INJECT -->\n")
                self.assertNotIn("INJECT", out)

    def test_lone_surrogates_collapse(self):
        # 6xl.60: a non-UTF-8 name decodes to lone surrogates (surrogateescape): a byte channel, never named
        out, hits = sanitize.escape_for_code("caf\udce9\udcff.txt")
        self.assertEqual(out, "caf<BYTES:2>.txt")
        self.assertEqual(hits, [{"line": 1, "codepoints": ["BYTES:2"]}])
        self.assertEqual(sanitize.sanitize_prose("a\ud800b"), "ab")


class RenameSideTests(unittest.TestCase):
    def test_removed_lines_judged_by_old_path(self):
        # 6xl.63: README.md renamed to notes.py: its removed comment is prose, the added code stays as written
        d = ("diff --git a/README.md b/notes.py\nsimilarity index 50%\nrename from README.md\nrename to notes.py\n"
             "--- a/README.md\n+++ b/notes.py\n@@ -1,3 +1,3 @@\n-<!-- RENCANARY -->\n+y = '<!-- KEEPCODE -->'\n keep\n"
             " <!-- CTXCANARY -->\n")
        out, hits = sanitize.strip_hidden_diff(d)
        self.assertNotIn("RENCANARY", out)
        self.assertNotIn("CTXCANARY", out)
        self.assertIn("+y = '<!-- KEEPCODE -->'", out)
        self.assertEqual(out.count("\n"), d.count("\n"))
        self.assertEqual(sorted(h["kind"] for h in hits), ["comment", "comment"])

    def test_code_renamed_to_prose_strips_added_only(self):
        d = ("diff --git a/a.py b/a.md\nrename from a.py\nrename to a.md\n--- a/a.py\n+++ b/a.md\n@@ -1 +1 @@\n"
             "-x = '<!-- OLDCODE -->'\n+<!-- NEWPROSE -->\n")
        out, _ = sanitize.strip_hidden_diff(d)
        self.assertIn("-x = '<!-- OLDCODE -->'", out)
        self.assertNotIn("NEWPROSE", out)


def hunk(old_start, new_start, body):
    """A one-file diff of a markdown file; `body` is the hunk lines with their prefixes."""
    lines = body.split("\n")
    o = sum(1 for ln in lines if ln[:1] in (" ", "-"))
    n = sum(1 for ln in lines if ln[:1] in (" ", "+"))
    return ("diff --git a/n.md b/n.md\nindex 1..2 100644\n--- a/n.md\n+++ b/n.md\n@@ -%d,%d +%d,%d @@\n%s\n"
            % (old_start, o, new_start, n, body))


class HiddenDiffTests(unittest.TestCase):
    def test_diff_added_comment_removed(self):
        d = hunk(1, 1, " keep\n+a <!-- INJECT --> b\n+![INJECT](u.png)\n tail")
        out, hits = sanitize.strip_hidden_diff(d)
        self.assertNotIn("INJECT", out)
        self.assertEqual(out.count("\n"), d.count("\n"))
        self.assertIn("\n+a [hidden-comment removed: 15 chars] b\n", out)
        self.assertEqual(hits, [{"file": "n.md", "line": 7, "kind": "comment", "length": 15},
                                {"file": "n.md", "line": 8, "kind": "alt-text", "length": 6}])

    def test_diff_multiline_comment_keeps_prefixes(self):
        d = hunk(1, 1, " keep\n+<!--\n+INJECT\n+-->\n+after")
        out, hits = sanitize.strip_hidden_diff(d)
        self.assertNotIn("INJECT", out)
        lines = out.split("\n")
        self.assertEqual(len(lines), len(d.split("\n")))
        self.assertTrue(all(ln[:1] in " +" for ln in lines[5:-1]), lines)
        self.assertEqual(lines[-2], "+after")
        self.assertEqual([h["kind"] for h in hits], ["comment"])

    def test_diff_removed_line_also_stripped(self):
        out, hits = sanitize.strip_hidden_diff(hunk(1, 1, "-old <!-- INJECT -->\n+new"))
        self.assertNotIn("INJECT", out)
        self.assertEqual([h["kind"] for h in hits], ["comment"])

    def test_diff_context_tail_of_comment_removed(self):
        # the opener lies above the hunk; the closer ends a line, so the payload before it goes
        full = hunk(1, 1, " intro\n <!--\n INJECT\n INJECT -->\n+added\n tail")
        part = hunk(3, 3, " INJECT\n INJECT -->\n+added\n tail")
        out, hits = sanitize.strip_hidden_diff(part, full)
        self.assertNotIn("INJECT", out)
        self.assertIn("+added\n tail", out)
        self.assertEqual([(h["line"], h["kind"]) for h in hits], [(6, "comment")])

    def test_diff_arrow_inside_visible_text_kept(self):
        d = hunk(1, 1, " graph\n+A --> B\n+C --> D text")
        self.assertEqual(sanitize.strip_hidden_diff(d), (d, []))

    def test_diff_plus_plus_plus_content_line_is_not_a_header(self):
        d = hunk(1, 1, "+++ INJECT-looking line\n+<!-- INJECT -->")
        out, hits = sanitize.strip_hidden_diff(d)
        self.assertNotIn("<!--", out)
        self.assertEqual(len(hits), 1)

    def test_diff_code_files_untouched(self):
        d = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -1,1 +1,2 @@\n x = 1\n"
             "+y = '<!-- keep --> ![keep](u)'  # <div hidden>keep</div>\n")
        self.assertEqual(sanitize.strip_hidden_diff(d), (d, []))

    def test_diff_two_files_only_prose_stripped(self):
        d = ("diff --git a/x.py b/x.py\n--- a/x.py\n+++ b/x.py\n@@ -0,0 +1 @@\n+# <!-- keep -->\n"
             + hunk(0, 1, "+<!-- INJECT -->"))
        out, hits = sanitize.strip_hidden_diff(d)
        self.assertIn("# <!-- keep -->", out)
        self.assertNotIn("INJECT", out)
        self.assertEqual([h["file"] for h in hits], ["n.md"])

    def test_diff_no_newline_marker_and_binary(self):
        d = hunk(1, 1, "-a\n\\ No newline at end of file\n+<!-- INJECT -->\n\\ No newline at end of file")
        out, _ = sanitize.strip_hidden_diff(d)
        self.assertNotIn("INJECT", out)
        self.assertIn("\\ No newline at end of file", out)
        b = "diff --git a/i.md b/i.md\nBinary files a/i.md and b/i.md differ\n"
        self.assertEqual(sanitize.strip_hidden_diff(b), (b, []))
        self.assertEqual(sanitize.strip_hidden_diff(""), ("", []))


if __name__ == "__main__":
    unittest.main()
