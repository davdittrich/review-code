"""Untrusted-text helpers: stdlib only, Python 3.9 grammar."""
import re
import unicodedata
from typing import List, Optional, Tuple

# Not Cf/Cc but invisible or blank: variation selectors, CGJ, blank letters, braille blank.
_EXTRA = {0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800}
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)", re.DOTALL)


def _invisible(ch: str) -> bool:
    o = ord(ch)
    if ch in "\t\n\r":
        return False
    return (unicodedata.category(ch) in ("Cf", "Zl", "Zp", "Cc")
            or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or o in _EXTRA)


def strip_invisible(text: str) -> str:
    return "".join(c for c in text if not _invisible(c))


def sanitize_prose(text: Optional[str]) -> str:
    """Drop invisible characters and HTML comments, repeating until nothing changes so that
    `<!<!-- x -->-- y -->` cannot reassemble into a comment after one pass."""
    s = text or ""
    while True:
        t = _COMMENT.sub("", strip_invisible(s))
        if t == s:
            return s
        s = t


def escape_for_code(text: str) -> Tuple[str, List[dict]]:
    """Replace each invisible character with `<U+XXXX>`; lines split on newline only, so line
    numbers never shift. Returns the text and `[{"line": n, "codepoints": [...]}]`."""
    out, hits = [], []
    for n, line in enumerate(text.split("\n"), 1):
        cps = []
        if any(_invisible(c) for c in line):
            buf = []
            for c in line:
                if _invisible(c):
                    cps.append("U+%04X" % ord(c))
                    buf.append("<U+%04X>" % ord(c))
                else:
                    buf.append(c)
            line = "".join(buf)
        out.append(line)
        if cps:
            hits.append({"line": n, "codepoints": cps})
    return "\n".join(out), hits
