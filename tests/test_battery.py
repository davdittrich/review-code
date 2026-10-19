"""Hidden-text battery: each construct hides TOK from a GitHub reader, so no sink may keep it."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "skills", "review-code",
                                "scripts"))

import sanitize  # noqa: E402

BATTERY = [
    ("comment", "a <!-- TOK --> b"),
    ("comment_multiline", "a\n\n<!--\nTOK\n-->\n\nb"),
    ("comment_unclosed", "a\n\n<!-- TOK\n\nb"),
    ("comment_crlf", "a\r\n<!--\r\nTOK\r\n-->\r\nb"),
    ("comment_cr_only", "a\r<!-- x\rTOK -->\rb"),
    ("refcomment_hash", "a\n\n[//]: # (TOK)\n\nb"),
    ("refcomment_angle", "a\n\n[x]: <> (TOK)\n\nb"),
    ("refcomment_cr_only", "a\r[//]: # (TOK)\rb"),
    ("alt_inline", "a ![TOK](u) b"),
    ("alt_nested_brackets", "a ![x [TOK] y](u) b"),
    ("img_alt", 'a <img src="u" alt="TOK"> b'),
    ("hidden_nested", "<div hidden><div></div>TOK</div>"),
    ("font_size_zero", '<span style="font-size: 0">TOK</span>'),
    ("display_none_unclosed", '<p style="DISPLAY:none">x\n\nTOK'),
    ("tags_run", "a " + "".join(chr(0xE0000 + ord(c)) for c in "TOK") + " b"),
]


def diff_of(text):
    lines = text.split("\n")
    return ("diff --git a/n.md b/n.md\n--- a/n.md\n+++ b/n.md\n@@ -0,0 +1,%d @@\n" % len(lines)
            + "".join("+" + ln + "\n" for ln in lines))


class Battery(unittest.TestCase):
    def test_hidden_constructs_never_survive(self):
        for name, text in BATTERY:
            with self.subTest(name):
                tree = sanitize.escape_for_code(sanitize.strip_hidden(text)[0])[0]
                diff = sanitize.escape_for_code(sanitize.strip_hidden_diff(diff_of(text))[0])[0]
                for sink, out in (("tree/", tree), ("change.diff", diff), ("spec.md", sanitize.sanitize_prose(text))):
                    self.assertNotIn("TOK", out, sink)
                    self.assertNotIn("\U000E0054", out, sink)
                self.assertEqual(sanitize.strip_hidden(text)[0].count("\n"), text.count("\n"))


if __name__ == "__main__":
    unittest.main()
