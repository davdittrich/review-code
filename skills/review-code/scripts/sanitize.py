"""Untrusted-text helpers: stdlib only, Python 3.9 grammar.

Prose from the change author is stripped (sanitize_prose); code, file names and branch names are
escaped, never deleted (escape_for_code), so hidden characters become findings. Both use one
`_invisible` predicate on code points of the running interpreter's unicodedata, no NFKC folding.
Markdown link and image destinations (inline and `[r]: url`) become an ellipsis; the link text stays.
Change-author files that reach the main thread are treated per type. Prose and markup files (.md .markdown
.html .htm .txt .rst; is_prose_file) go through strip_hidden / strip_hidden_diff: HTML comments, markdown
`[//]: #` comments, markdown and `<img>` alt text and elements hidden by `hidden` or an invisible inline style
become a one-line `[hidden-KIND removed: N chars]` marker, every newline kept so line numbers and diff anchors
hold, and each removal is returned as {line, kind, length} (never the payload) for ledger.json `hidden_text`.
Nothing is protected inside code fences or code spans: a fence can sit inside an HTML block or a comment, so a
fence is no proof of visibility; examples of comments in fenced docs are over-stripped and flagged. Source code
files are never touched by this (their comments are the content); invisible and bidi characters are escaped in
all files (escape_for_code) and a run of Tags-block characters, which spells ASCII, collapses to `<TAGS:n>`.
Not covered by the strip: link destinations and titles (reviewable content), entity-encoded markup (a renderer
shows it as text), `<script>`/`<style>` bodies (code), comments written in other markup (rst `..`, org), and
hiding by CSS classes or external style sheets.
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


_PROSE_EXT = (".md", ".markdown", ".html", ".htm", ".txt", ".rst")


def is_prose_file(path: str) -> bool:
    return path.lower().endswith(_PROSE_EXT)


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
    numbers never shift. A run of Tags-block characters (U+E0000..E007F spell ASCII) becomes one
    `<TAGS:n>`, so the escaped text never carries the payload. Returns the text and
    `[{"line": n, "codepoints": [...]}]`."""
    out, hits = [], []
    for n, line in enumerate(text.split("\n"), 1):
        cps = []
        if any(_invisible(c) for c in line):
            buf, i = [], 0
            while i < len(line):
                c = line[i]
                if 0xE0000 <= ord(c) <= 0xE007F:
                    j = i
                    while j < len(line) and 0xE0000 <= ord(line[j]) <= 0xE007F:
                        j += 1
                    cps.append("TAGS:%d" % (j - i))
                    buf.append("<TAGS:%d>" % (j - i))
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
    """(index, path, tag, old line, new line, content) for every hunk line of a unified diff; tag is
    ' ', '+', '-' or '\\'. path is None before the first file header. A `+++ ` line inside a hunk is
    content: hunk line counts decide."""
    path, old_left, new_left, o, n = None, 0, 0, 0, 0
    for i, ln in enumerate(diff.split("\n")):
        if old_left > 0 or new_left > 0:
            tag, body = (ln[:1] or " "), ln[1:]
            if tag == "\\":
                yield i, path, tag, 0, 0, body
                continue
            yield i, path, tag, o, n, body
            if tag != "+":
                old_left -= 1
                o += 1
            if tag != "-":
                new_left -= 1
                n += 1
            continue
        if ln.startswith("diff --git "):
            path = None
        elif ln.startswith("--- ") and path is None:
            path = _hdr_path(ln[4:], "a/")
        elif ln.startswith("+++ "):
            p = _hdr_path(ln[4:], "b/")
            path = p if p is not None else path
        else:
            m = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", ln)
            if m:
                o, old_left = int(m.group(1)), int(m.group(2) or 1)
                n, new_left = int(m.group(3)), int(m.group(4) or 1)
                o, n = max(o, 1), max(n, 1)


def _hdr_path(s: str, prefix: str) -> Optional[str]:
    s = s.split("\t")[0]
    if s == "/dev/null":
        return None
    s = s.strip('"')
    return s[len(prefix):] if s.startswith(prefix) else s


def strip_hidden_diff(diff: str, full: Optional[str] = None) -> Tuple[str, List[dict]]:
    """Apply strip_hidden to the lines of every prose or markup file in a unified diff, keeping every line
    and its prefix. `full` is the same diff with the whole file as context (git diff -U999999): it gives each
    line the comment state it really has. Without it, or for a line it does not hold, state comes from the
    diff itself and a comment opened above a hunk is missed. Returns the diff and `[{"file", "line", "kind",
    "length"}]`, one per removed span, at the first diff line that shows it."""
    sides = {}  # (path, side) -> {line number: content}
    for _, path, tag, o, n, body in _walk(diff if full is None else full):
        if path and tag != "\\" and is_prose_file(path):
            if tag != "+":
                sides.setdefault((path, "-"), {})[o] = body
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
    for i, path, tag, o, n, body in _walk(diff):
        if not (path and tag != "\\" and is_prose_file(path)):
            continue
        side, num = ("-", o) if tag == "-" else ("+", n)
        got = done.get((path, side))
        if got and got[3].get(num) == body:
            new, k = got[0][num - 1], got[2].get(num)
            keys = [(path, side, k)] if k is not None else []
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
