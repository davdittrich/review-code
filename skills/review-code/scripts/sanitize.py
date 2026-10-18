"""Untrusted-text helpers: stdlib only, Python 3.9 grammar.

Prose from the change author is stripped (sanitize_prose); code, file names and branch names are
escaped, never deleted (escape_for_code), so hidden characters become findings. Both use one
`_invisible` predicate: Default_Ignorable_Code_Point plus general categories Cf, Cc, Zl, Zp, all from embedded
Unicode 18.0 tables (the same set on every interpreter), no NFKC folding.
Markdown link and image destinations (inline and `[r]: url`) become an ellipsis; the link text stays.
Change-author files that reach the main thread are treated per type. Prose and markup files (every linguist
Markdown extension, .html .htm .txt .rst; is_prose_file) go through strip_hidden / strip_hidden_diff: HTML comments, markdown
`[//]: #` comments, markdown and `<img>` alt text and elements hidden by `hidden` or an invisible inline style
become a one-line `[hidden-KIND removed: N chars]` marker, every newline kept so line numbers and diff anchors
hold, and each removal is returned as {line, kind, length} (never the payload) for ledger.json `hidden_text`.
Nothing is protected inside code fences or code spans: a fence can sit inside an HTML block or a comment, so a
fence is no proof of visibility; examples of comments in fenced docs are over-stripped and flagged. Source code
files are never touched by this (their comments are the content); invisible and bidi characters are escaped in
all files (escape_for_code) and a run of plane-14 default-ignorables (Tags spell ASCII) collapses to `<TAGS:n>`.
Not covered by the strip: link destinations and titles (reviewable content), entity-encoded markup (a renderer
shows it as text), `<script>`/`<style>` bodies (code), comments written in other markup (rst `..`, org), and
hiding by CSS classes or external style sheets.
Not detected: homoglyphs, base64, plain-language injection, valueless attribute words of unlisted tags. The defence is structural: PR text
reaches only the Spec input (02-07) and lanes run without execution tools (02-01, 02-08).
"""
import bisect
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


# Default_Ignorable_Code_Point, embedded because unicodedata has no property lookup and its version
# follows the interpreter (Python 3.9: Unicode 13). Source: DerivedCoreProperties-18.0.0.txt
# (https://www.unicode.org/Public/UCD/latest/ucd/), 4174 code points; adjacent ranges merged.
_DI = ((0x00AD, 0x00AD), (0x034F, 0x034F), (0x061C, 0x061C), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F),
       (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x206F), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFEFF, 0xFEFF),
       (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8), (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0000, 0xE0FFF))
# General_Category Cf, embedded for the same reason (Python 3.9 misses U+0890, U+0891, U+13439..1343F).
# Source: extracted/DerivedGeneralCategory-18.0.0.txt, 170 code points. Cc, Zl, Zp are stable and listed whole.
_CF = ((0x00AD, 0x00AD), (0x0600, 0x0605), (0x061C, 0x061C), (0x06DD, 0x06DD), (0x070F, 0x070F), (0x0890, 0x0891),
       (0x08E2, 0x08E2), (0x180E, 0x180E), (0x200B, 0x200F), (0x202A, 0x202E), (0x2060, 0x2064), (0x2066, 0x206F),
       (0xFEFF, 0xFEFF), (0xFFF9, 0xFFFB), (0x110BD, 0x110BD), (0x110CD, 0x110CD), (0x13430, 0x1343F),
       (0x1BCA0, 0x1BCA3), (0x1D173, 0x1D17A), (0xE0001, 0xE0001), (0xE0020, 0xE007F))
_CTRL = ((0x0000, 0x001F), (0x007F, 0x009F), (0x2028, 0x2029), (0xD800, 0xDFFF))  # + surrogates (Cs, 6xl.60)


def _merge(ranges) -> Tuple[Tuple[int, int], ...]:
    out: List[Tuple[int, int]] = []
    for a, b in sorted(ranges):
        if out and a <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], b))
        else:
            out.append((a, b))
    return tuple(out)


# Every code point _invisible flags, independent of the interpreter's unicodedata version (6xl.58).
_INVIS = _merge(_DI + _CF + _CTRL + tuple((o, o) for o in _EXTRA))
_INVIS_LO = tuple(a for a, _ in _INVIS)


def _invisible(ch: str) -> bool:
    o = ord(ch)
    if ch in "\t\n\r":
        return False
    i = bisect.bisect_right(_INVIS_LO, o) - 1
    return i >= 0 and o <= _INVIS[i][1]


# Markdown: every extension and filename github-linguist languages.yml maps to Markdown (.scd is also
# SuperCollider; over-stripping its code is the safe side), the Markdown-based MDX, R Markdown and Quarto
# (6xl.60), then HTML, plain text and reStructuredText. Other markup keeps its own hidden syntax (see the doc).
_PROSE_EXT = (".md", ".livemd", ".markdown", ".mdown", ".mdwn", ".mkd", ".mkdn", ".mkdown", ".ronn", ".scd",
              ".workbook", ".mdx", ".rmd", ".qmd", ".html", ".htm", ".txt", ".rst")
_PROSE_NAMES = ("contents.lr",)


def is_prose_file(path: str) -> bool:
    p = path.lower()
    return p.endswith(_PROSE_EXT) or p.rpartition("/")[2] in _PROSE_NAMES


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


def _is_tag(c: str) -> bool:
    """Plane-14 default-ignorables other than selectors: Tags spell ASCII, E0080..E00FF the bytes 0x80..0xFF."""
    o = ord(c)
    return 0xE0000 <= o <= 0xE0FFF and not 0xE0100 <= o <= 0xE01EF


def _is_vs(c: str) -> bool:
    """Variation_Selector: FE00..FE0F, E0100..E01EF and the Mongolian free variation selectors."""
    o = ord(c)
    return 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or 0x180B <= o <= 0x180D or o == 0x180F


def _is_surrogate(c: str) -> bool:
    """A lone surrogate: an undecodable name byte (surrogateescape gives U+DC80..DCFF), one byte each."""
    return 0xD800 <= ord(c) <= 0xDFFF


def escape_for_code(text: str) -> Tuple[str, List[dict]]:
    """Replace each invisible character with `<U+XXXX>`; lines split on newline only, so line
    numbers never shift. A run of plane-14 default-ignorables (Tags U+E0000..E007F spell ASCII,
    U+E0080..E0FFF more bytes) becomes one `<TAGS:n>`, and a run of variation selectors (one byte
    each) one `<VS:n>` that never names a selector, and a run of lone surrogates (undecodable name bytes)
    one `<BYTES:n>`, so the escaped text never carries the payload. A single U+FE0F right after a
    symbol (category So: the emoji presentation selector) stays as written. Returns the text and
    `[{"line": n, "codepoints": [...]}]`."""
    out, hits = [], []
    for n, line in enumerate(text.split("\n"), 1):
        cps = []
        if any(_invisible(c) for c in line):
            buf, i = [], 0
            while i < len(line):
                c = line[i]
                test = _is_tag if _is_tag(c) else _is_vs if _is_vs(c) else _is_surrogate if _is_surrogate(c) else None
                if test is not None:
                    j = i
                    while j < len(line) and test(line[j]):
                        j += 1
                    if test is _is_vs and j == i + 1 and c == "\ufe0f" and i \
                            and unicodedata.category(line[i - 1]) == "So":
                        buf.append(c)
                    else:
                        lab = "%s:%d" % ({_is_tag: "TAGS", _is_vs: "VS"}.get(test, "BYTES"), j - i)
                        cps.append(lab)
                        buf.append("<%s>" % lab)
                    i = j
                    continue
                if _invisible(c):
                    cps.append("U+%04X" % ord(c))
                    buf.append("<U+%04X>" % ord(c))
                else:
                    buf.append(c)
                i += 1
            line = "".join(buf)
        out.append(line)
        if cps:
            hits.append({"line": n, "codepoints": cps})
    return "\n".join(out), hits


# ---- hidden text in prose and markup files (CTX-04) -------------------------------------------------
_ALTBODY = r"(?:\\.|[^\[\]\\]|\[(?:\\.|[^\[\]\\])*\])+"
_HIDDEN = re.compile(
    r"(?P<comment><!--.*?(?:-->|\Z)|<!\[CDATA\[.*?(?:\]\]>|\Z)|<!(?![dD][oO][cC][tT][yY][pP][eE]\b)[A-Za-z][^>]*(?:>|\Z)"
    r"|<\?[^>]*(?:>|\Z))"
    r"|(?P<refc>^[ \t]*\[[^\]\n]*\]:[ \t]*(?:#|<>)[ \t]*(?:\(.*?\)|\"[^\"]*\"|'[^']*')[ \t]*(?=\r?$))"
    r"|!\[(?P<alt>" + _ALTBODY + r")\]", re.DOTALL | re.MULTILINE)
_MARKER = re.compile(r"\[hidden-[a-z-]+ removed: \d+ chars\]\Z")
_ALTATTR = re.compile(r"""(?<![\w:-])alt\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)


class _Spans(HTMLParser):
    """Offsets of elements a page hides and of `<img alt>` values, over text whose other hidden spans
    are blanked. An unclosed hiding element hides to the end. `<script>` and `<style>` are code here."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.text = text
        self.starts = [0]
        for ln in text.split("\n"):
            self.starts.append(self.starts[-1] + len(ln) + 1)
        self.spans: List[Tuple[int, int, str]] = []
        self.stack: List[Tuple[str, bool]] = []
        self.hidden, self.open_at = 0, 0

    def _at(self) -> int:
        line, col = self.getpos()
        return self.starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        at, raw = self._at(), self.get_starttag_text() or ""
        if tag == "img":
            for m in _ALTATTR.finditer(raw):
                a, b = m.span(m.lastindex)
                if raw[a:b].strip() and not _MARKER.match(raw[a:b].rstrip("\n")):
                    self.spans.append((at + a, at + b, "alt-text"))
        if tag not in _ELEMENTS:
            return
        a = {k: v or "" for k, v in attrs}
        hide = "hidden" in a or bool(_HIDING_STYLE.search("".join((a.get("style") or "").lower().split())))
        if tag in _VOID:
            if hide and not self.hidden:
                self.spans.append((at, at + len(raw), "element"))
            return
        self.stack.append((tag, hide))
        if hide and not self.hidden:
            self.open_at = at
        self.hidden += hide

    handle_startendtag = handle_starttag  # HTML ignores the slash on non-void elements

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                was = self.hidden
                self.hidden -= sum(h for _, h in self.stack[i:])
                del self.stack[i:]
                if was and not self.hidden:
                    at = self._at()
                    end = self.text.find(">", at)
                    self.spans.append((self.open_at, len(self.text) if end < 0 else end + 1, "element"))
                return

    def finish(self) -> List[Tuple[int, int, str]]:
        self.close()
        if self.hidden:
            self.spans.append((self.open_at, len(self.text), "element"))
        return self.spans


def _strip(text: str) -> Tuple[str, List[Tuple[int, int, str, int]]]:
    """(text, [(first line, last line, kind, length)]) with each hidden span replaced by a marker plus the
    span's newlines."""
    spans, blank, pos = [], [], 0
    for m in _HIDDEN.finditer(text):
        g = m.lastgroup
        a, b = m.span(g)
        if g == "alt" and (not m.group(g).strip() or _MARKER.match(m.group(g).rstrip("\n"))):
            continue
        spans.append((a, b, "alt-text" if g == "alt" else "comment"))
        blank += [text[pos:a], re.sub(r"[^\n]", " ", text[a:b])]
        pos = b
    p = _Spans("".join(blank) + text[pos:])
    p.feed("".join(blank) + text[pos:])
    spans += p.finish()
    out, info, pos, line, last = [], [], 0, 1, 0
    for a, b, kind in sorted(spans, key=lambda s: (s[0], -s[1])):
        if a < last:
            continue  # inside a span already removed
        line += text.count("\n", pos, a)
        nl = text.count("\n", a, b)
        out.append(text[pos:a] + "[hidden-%s removed: %d chars]" % (kind, b - a) + "\n" * nl)
        info.append((line, line + nl, kind, b - a))
        pos, last, line = b, b, line + nl
    return "".join(out) + text[pos:], info


def strip_hidden(text: str) -> Tuple[str, List[dict]]:
    """Remove hidden text from a prose or markup file; see the module docstring. Returns the text and
    `[{"line", "kind", "length"}]` (kind: comment, alt-text, element), never the payload."""
    out, info = _strip(text)
    return out, [{"line": a, "kind": k, "length": n} for a, _, k, n in info]


def _walk(diff: str):
    """(index, new path, tag, old line, new line, content, old path) for every hunk line of a unified diff; tag is
    ' ', '+', '-', '\\' or '@' (a hunk header: content is the function-context text after its second @@).
    Paths are None before the first file header; the old path differs from the new one for a rename or copy.
    A `+++ ` line inside a hunk is content: hunk line counts decide."""
    path = old = None
    old_left, new_left, o, n = 0, 0, 0, 0
    for i, ln in enumerate(diff.split("\n")):
        if old_left > 0 or new_left > 0:
            tag, body = (ln[:1] or " "), ln[1:]
            if tag == "\\":
                yield i, path, tag, 0, 0, body, old
                continue
            yield i, path, tag, o, n, body, old
            if tag != "+":
                old_left -= 1
                o += 1
            if tag != "-":
                new_left -= 1
                n += 1
            continue
        if ln.startswith("diff --git "):
            path = old = None
        elif ln.startswith("--- ") and old is None:
            old = path = _hdr_path(ln[4:], "a/")
        elif ln.startswith("+++ "):
            p = _hdr_path(ln[4:], "b/")
            path = p if p is not None else path
            old = old if old is not None else path
        else:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
            if m:
                yield i, path, "@", 0, 0, ln[m.end():], old  # the function-context trailer git appends
                o, old_left = int(m.group(1)), int(m.group(2) or 1)
                n, new_left = int(m.group(3)), int(m.group(4) or 1)
                o, n = max(o, 1), max(n, 1)


_ESC = {"n": 10, "t": 9, '"': 34, "\\": 92, "a": 7, "b": 8, "f": 12, "r": 13, "v": 11}


def unquote_path(s: str) -> str:
    """Undo git's C-style quoting of a path in a diff header (the one place paths are unquoted, 6xl.70): octal
    escapes are bytes, so the result decodes like every other name (UTF-8, surrogateescape)."""
    if not (len(s) >= 2 and s[0] == '"' and s[-1] == '"'):
        return s
    s, out, i = s[1:-1], bytearray(), 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            if s[i + 1] in "01234567":
                out.append(int(s[i + 1:i + 4], 8) & 255)
                i += 4
            else:
                out.append(_ESC.get(s[i + 1], ord(s[i + 1]) & 255))
                i += 2
        else:
            out += c.encode("utf-8", "surrogateescape")
            i += 1
    return bytes(out).decode("utf-8", "surrogateescape")


def _hdr_path(s: str, prefix: str) -> Optional[str]:
    s = s.split("\t")[0]  # git ends a name holding a space with a TAB; a TAB inside a name is always quoted
    if s == "/dev/null":
        return None
    s = unquote_path(s)
    return s[len(prefix):] if s.startswith(prefix) else s


def strip_hidden_diff(diff: str, full: Optional[str] = None) -> Tuple[str, List[dict]]:
    """Apply strip_hidden to the lines of every prose or markup file in a unified diff, keeping every line
    and its prefix. `full` is the same diff with the whole file as context (git diff -U999999): it gives each
    line the comment state it really has. Without it, or for a line it does not hold, state comes from the
    diff itself and a comment opened above a hunk is missed. Returns the diff and `[{"file", "line", "kind",
    "length"}]`, one per removed span, at the first diff line that shows it."""
    def prose(path, tag, old):
        """6xl.63: a removed line belongs to the old path, an added line to the new one, context to both."""
        olds, news = bool(old and is_prose_file(old)), bool(path and is_prose_file(path))
        return olds if tag == "-" else news if tag == "+" else olds or news

    sides = {}  # (path, side) -> {line number: content}; the "-" side is keyed by the old path
    for _, path, tag, o, n, body, old in _walk(diff if full is None else full):
        if path and tag not in "\\@" and prose(path, tag, old):
            if tag != "+":
                sides.setdefault((old or path, "-"), {})[o] = body
            if tag != "-":
                sides.setdefault((path, "+"), {})[n] = body
    done = {}
    for key, lines in sides.items():
        text = "\n".join(lines.get(i, "") for i in range(1, max(lines) + 1))
        out, info = _strip(text)
        by_line = {}
        for k, (a, b, kind, length) in enumerate(info):
            for ln in range(a, b + 1):
                by_line[ln] = k
        done[key] = (out.split("\n"), info, by_line, lines)
    res, hits, seen = diff.split("\n"), [], set()
    for i, path, tag, o, n, body, old in _walk(diff):
        if not (path and tag != "\\" and prose(path, tag, old)):
            continue
        if tag == "@":  # 6xl.55: the trailer is a lone old-side line, maybe inside a comment: drop it
            res[i] = res[i][:len(res[i]) - len(body)]
            continue
        side, num, where = ("-", o, old or path) if tag == "-" else ("+", n, path)
        got = done.get((where, side))
        if tag == " " and not (got and got[3].get(num) == body):  # context line of a file prose only on the old side
            side, num, where = "-", o, old or path
            got = done.get((where, side))
        if got and got[3].get(num) == body:
            new, k = got[0][num - 1], got[2].get(num)
            keys = [(where, side, k)] if k is not None else []
            spans = [got[1][k]] if k is not None else []
        else:  # not in the full-context diff: this line alone
            new, info = _strip(body)
            keys, spans = [(i, j) for j in range(len(info))], info
        res[i] = tag + new
        for key, sp in zip(keys, spans):
            if key not in seen:
                seen.add(key)
                hits.append({"file": path, "line": i + 1, "kind": sp[2], "length": sp[3]})
    return "\n".join(res), sorted(hits, key=lambda h: h["line"])
