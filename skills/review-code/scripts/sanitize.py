"""Untrusted-text helpers: stdlib only, Python 3.9 grammar.

Prose from the change author is stripped (sanitize_prose); code, file names and branch names are
escaped, never deleted (escape_for_code), so hidden characters become findings. Both use one
`_invisible` predicate on code points of the running interpreter's unicodedata, no NFKC folding.
Markdown link and image destinations (inline and `[r]: url`) become an ellipsis; the link text stays.
Not detected: homoglyphs, base64, plain-language injection, valueless attribute words of unlisted tags. The defence is structural: PR text
reaches only the Spec input (02-07) and lanes run without execution tools (02-01, 02-08).
"""
import re
import unicodedata
from html.parser import HTMLParser
from typing import List, Optional, Tuple

# Not Cf/Cc but invisible or blank: variation selectors, CGJ, blank letters, braille blank.
_EXTRA = {0x034F, 0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800}
_ROUNDS = 10
_MARK = " [sanitizer: markup removed]"
# Comments, CDATA, doctype and processing instructions; an unterminated comment runs to the end.
_COMMENT = re.compile(r"<!--.*?(?:-->|\Z)|<!\[CDATA\[.*?(?:\]\]>|\Z)|<![A-Za-z][^>]*>|<\?[^>]*>", re.DOTALL)
# Code that a reader sees verbatim: fenced blocks (an unterminated fence runs to the end) and spans.
_FENCE = re.compile(r"^ {0,3}(`{3,})[^`\n]*(?:\n.*?)?(?:^ {0,3}\1`*[ \t]*$|\Z)"
                    r"|^ {0,3}(~{3,})[^\n]*(?:\n.*?)?(?:^ {0,3}\2~*[ \t]*$|\Z)", re.DOTALL | re.MULTILINE)
# A span cannot cross a blank line, and a backtick fence's info string cannot hold a backtick.
_SPAN = re.compile(r"(?<!`)(`+)(?!`)((?:(?!\n[ \t]*\n).)+?)(?<!`)\1(?!`)", re.DOTALL)
_TOKEN = re.compile("\ue000(\\d+)\ue001")
# `[//]: # (text)` and friends render nothing on GitHub.
_REFCOMMENT = re.compile(r"^[ \t]*\[[^\]\n]*\]:[ \t]*(?:#|<>)[ \t]*(?:\(.*?\)|\"[^\"]*\"|'[^']*')[ \t]*$",
                         re.DOTALL | re.MULTILINE)
_BRACKET = r"\[(?:\\.|[^\[\]\\]|\[(?:\\.|[^\[\]\\])*\])*\]"  # one nesting level
_ALT = re.compile("!" + _BRACKET)
_TITLE = r"(?:\"[^\"]*\"|'[^']*')"
# A link or image destination (and its title) renders as a hover target or not at all, yet the Spec lane reads
# it; the link text stays and the destination becomes an ellipsis.
_DEST = r"(?:<[^<>\n]*>|(?:\\.|[^()\s\\]|\([^()\s]*\))+)"
_INLINE_DEST = re.compile(r"\]\([ \t]*(?:" + _DEST + r")?(?:\s+" + _TITLE + r")?\s*\)")
_REFDEF_DEST = re.compile(r"(^ {0,3}\[[^\]\n]+\]:[ \t]*)(?:<[^<>\n]*>|\S+)(?:\s+(?:" + _TITLE + r"|\([^()\n]*\)))?[ \t]*$",
                          re.MULTILINE)
_DOTS = "\u2026"
_HIDING_STYLE = re.compile(r"display:none|visibility:hidden|font-size:0|opacity:0")
_OPAQUE = frozenset("script style template noscript".split())
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
# Names treated as markup; any other `<x>` (Vec<T>) is prose and is kept as written.
_ELEMENTS = _VOID | _OPAQUE | frozenset((
    "a abbr address article aside audio b bdi bdo big blockquote body button canvas caption center cite code "
    "colgroup data datalist dd del details dfn dialog div dl dt em fieldset figcaption figure font footer form "
    "h1 h2 h3 h4 h5 h6 head header hgroup html i iframe ins kbd label legend li main map mark marquee math menu "
    "meter nav object ol optgroup option output p picture pre progress q rp rt ruby s samp search section select "
    "slot small span strike strong sub summary sup svg table tbody td textarea tfoot th thead time title tr tt u "
    "ul var video").split())


def _invisible(ch: str) -> bool:
    o = ord(ch)
    if ch in "\t\n\r":
        return False
    return (unicodedata.category(ch) in ("Cf", "Zl", "Zp", "Cc")
            or 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or o in _EXTRA)


def strip_invisible(text: str) -> str:
    return "".join(c for c in text if not _invisible(c))


class _TextOnly(HTMLParser):
    """Keeps text a rendered page shows. Known elements vanish with their attributes (alt, title,
    data-*); text under script/style/template/noscript, `hidden` or an invisible inline style is
    dropped. An unclosed hiding element hides to the end: over-stripping is the safe side."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: List[str] = []
        self.stack: List[Tuple[str, bool]] = []
        self.hidden = 0

    def _emit(self, s: str) -> None:
        if not self.hidden:
            self.out.append(s)

    def handle_starttag(self, tag, attrs):
        if tag not in _ELEMENTS:
            text = self.get_starttag_text() or ""
            # `<foo bar='x'>` is no prose and renders as nothing, so its attribute values go; an autolink or
            # `Vec<T, U>` has no spaced `name=value` pair and stays as written.
            self._emit("<%s>" % tag if any(v is not None for _, v in attrs) and re.search(r"\s", text) else text)
            return
        if tag in _VOID:
            return
        a = {k: v or "" for k, v in attrs}
        style = "".join((a.get("style") or "").lower().split())
        hide = tag in _OPAQUE or "hidden" in a or bool(_HIDING_STYLE.search(style))
        self.stack.append((tag, hide))
        self.hidden += hide

    handle_startendtag = handle_starttag  # HTML ignores the slash on non-void elements

    def handle_endtag(self, tag):
        if tag not in _ELEMENTS:
            self._emit("</%s>" % tag)
            return
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:  # unbalanced input: pop to the nearest match
                self.hidden -= sum(h for _, h in self.stack[i:])
                del self.stack[i:]
                return

    def handle_data(self, data):
        self._emit(data)


def _round(s: str) -> str:
    s = strip_invisible(s)
    store: List[str] = []

    def stash(m):
        store.append(m.group(0))
        return "\ue000%d\ue001" % (len(store) - 1)

    s = _SPAN.sub(stash, _FENCE.sub(stash, s))
    while True:
        t = _COMMENT.sub("", s)
        if t == s:
            break
        s = t
    s = _REFCOMMENT.sub("", s)
    s = _ALT.sub("![]", s)
    s = _REFDEF_DEST.sub(lambda m: m.group(1) + _DOTS, _INLINE_DEST.sub(lambda m: "](" + _DOTS + ")" if m.group(0).strip("]() \t\r\n") else m.group(0), s))
    p = _TextOnly()
    p.feed(s)
    p.close()
    return _TOKEN.sub(lambda m: store[int(m.group(1))] if int(m.group(1)) < len(store) else "", "".join(p.out))


def sanitize_prose(text: Optional[str]) -> str:
    """Reduce change-author prose to what a reader of the rendered page sees. Each round can expose
    work for the next (entity decoding rebuilds comments, removed tags rejoin `<!` and `--`), so rounds
    repeat to a fixpoint. Past the cap, all `<`, `>` and `&` go: no markup left to reassemble."""
    s = text or ""
    for _ in range(_ROUNDS + 1):
        t = _round(s)
        if t == s:
            return s
        s = t
    s = re.sub(r"[<>&]", "", s)
    while True:  # markup-free text only shrinks, so this ends
        t = _round(s)
        if t == s:
            return s + _MARK
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
