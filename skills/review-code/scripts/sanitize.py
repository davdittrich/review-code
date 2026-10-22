"""Untrusted-text helpers: stdlib only, Python 3.9 grammar.

Prose from the change author is stripped (sanitize_prose); code, file names and branch names are escaped,
never deleted (escape_for_code), so hidden characters become findings. Prose and markup files (is_prose_file)
go through strip_hidden / strip_hidden_diff: HTML comments, markdown comment lines (`[//]: # (x)`), image alt
text and hidden elements each become a `[hidden-KIND removed: N chars]` marker, every newline kept so line
numbers and diff anchors hold; each removal is returned as {line, kind, length}, never the payload, plus a short `signal`
when the span is worth a reviewer's look (gh#25): `added` (strip_hidden_diff: the span lies on a line the change adds),
`assistant` (it names the assistant or its instructions), `encoded` (a base64-like run or an invisible character),
`url` (a link), `markup` (nested markup) or `long` (over 200 characters). The wording signals are a best-effort list that a paraphrase
evades; the unsignalled rest is routine.
Not covered: attributes, link titles and definitions, entity-encoded markup, homoglyphs, base64 and
plain-language injection. The defence there is structural: PR text reaches only the Spec input and lanes run
without execution tools.
"""
import re
import unicodedata
from html.parser import HTMLParser
from typing import List, Optional, Tuple

# Invisible beyond categories Cf, Cc, Cs, Zl, Zp: default-ignorable letters and marks, variation selectors,
# unassigned default-ignorables, plane 14 (Tags) and the blank braille pattern.
_EXTRA = ((0x034F, 0x034F), (0x115F, 0x1160), (0x17B4, 0x17B5), (0x180B, 0x180F), (0x2060, 0x206F),
          (0x2800, 0x2800), (0x3164, 0x3164), (0xFE00, 0xFE0F), (0xFFA0, 0xFFA0), (0xFFF0, 0xFFF8),
          (0xE0000, 0xE0FFF))
_BREAK = re.compile(r"\r\n|\r|\n")  # CommonMark line ends


def _invisible(ch: str) -> bool:
    if ch in "\t\n\r":
        return False
    o = ord(ch)
    return unicodedata.category(ch) in ("Cf", "Cc", "Cs", "Zl", "Zp") or any(a <= o <= b for a, b in _EXTRA)


def _run_kind(ch: str) -> Optional[str]:
    """Characters that can spell a payload one per byte: escaped as a counted run, never named."""
    o = ord(ch)
    if 0xFE00 <= o <= 0xFE0F or 0xE0100 <= o <= 0xE01EF or 0x180B <= o <= 0x180D or o == 0x180F:
        return "VS"
    if 0xE0000 <= o <= 0xE0FFF:
        return "TAGS"
    if 0xD800 <= o <= 0xDFFF:
        return "BYTES"  # undecodable name bytes (surrogateescape)
    return None


_PROSE_EXT = (".md", ".livemd", ".markdown", ".mdown", ".mdwn", ".mkd", ".mkdn", ".mkdown", ".ronn", ".scd",
              ".workbook", ".mdx", ".rmd", ".qmd", ".html", ".htm", ".txt", ".rst")


def is_prose_file(path: str) -> bool:
    p = path.lower()
    return p.endswith(_PROSE_EXT) or p.rpartition("/")[2] == "contents.lr"


def strip_invisible(text: str) -> str:
    return "".join(c for c in text if not _invisible(c))


def escape_line(x) -> str:
    """Untrusted text as one escaped line (whitespace runs collapse), so no CR overwrites the terminal."""
    return escape_for_code(" ".join(str(x).split()))[0]


def escape_for_code(text: str) -> Tuple[str, List[dict]]:
    """Replace each invisible character with `<U+XXXX>`, and a run of variation selectors, Tags or lone
    surrogates with one `<VS:n>`, `<TAGS:n>` or `<BYTES:n>`, so the escape never carries the payload. A single
    U+FE0F after a symbol (an emoji) stays. Lines split on LF only. Returns the text and
    `[{"line": n, "codepoints": [...]}]`."""
    out, hits = [], []
    for n, line in enumerate(text.split("\n"), 1):
        cps, buf, i = [], [], 0
        while i < len(line):
            c, kind = line[i], _run_kind(line[i])
            j = i + 1
            while kind and j < len(line) and _run_kind(line[j]) == kind:
                j += 1
            emoji = c == "️" and j == i + 1 and i > 0 and unicodedata.category(line[i - 1]) == "So"
            if kind and not emoji:
                cps.append("%s:%d" % (kind, j - i))
            elif not kind and _invisible(c):
                cps.append("U+%04X" % ord(c))
            else:
                buf.append(line[i:j])
                i = j
                continue
            buf.append("<%s>" % cps[-1])
            i = j
        out.append("".join(buf))
        if cps:
            hits.append({"line": n, "codepoints": cps})
    return "\n".join(out), hits


# Comments, markdown comment lines and image alt text (one bracket nesting level). A line start or end is
# any line break, so CR-only files match too.
_HIDDEN = re.compile(
    r"(?P<comment><!--.*?(?:-->|\Z)"
    r"|(?<![^\r\n])[ \t]*\[[^\]\r\n]*\]:[ \t]*(?:#|<>)[ \t]*(?:\([^\r\n]*\)|\"[^\"\r\n]*\"|'[^'\r\n]*')[ \t]*(?![^\r\n]))"
    r"|!\[(?P<alt>(?:\\.|[^\[\]\\]|\[(?:\\.|[^\[\]\\])*\])+)\]", re.DOTALL)
_ADDRESSING = re.compile(r"\b(?:assistants?|ai|llms?|claude|reviewers?|ignor\w*|disregard\w*|system prompt|instructions?)\b",
                         re.IGNORECASE)
_BASE64 = re.compile(r"[A-Za-z0-9+/]{40,}")
_URL = re.compile(r"\b(?:https?|ftp)://", re.IGNORECASE)
_NESTED = re.compile(r"-->|<[A-Za-z/!]")
LONG_SPAN = 200  # characters; a longer hidden span is never a one-line authoring hint


def _signal(kind: str, payload: str) -> Optional[str]:
    """The reason a removed span is worth a look, or None (routine). Never the payload itself."""
    inner = re.sub(r"\A<!--|-->\Z", "", payload) if kind == "comment" else payload
    if _ADDRESSING.search(inner):
        return "assistant"
    if _BASE64.search(inner) or any(unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") and c not in "\t\n\r" for c in inner):
        return "encoded"
    if _URL.search(inner):
        return "url"
    if kind != "element" and _NESTED.search(inner):
        return "markup"
    return "long" if len(payload) > LONG_SPAN else None


_MARKER = re.compile(r"\[hidden-[a-z-]+ removed: \d+ chars\]\Z")
_ALTATTR = re.compile(r"""(?<![\w:-])alt\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))""", re.IGNORECASE)
_HIDING_STYLE = re.compile(r"display:none|visibility:hidden|font-size:0|opacity:0")
_VOID = frozenset("area base br col embed hr img input link meta source track wbr".split())


class _Spans(HTMLParser):
    """(start, end, kind) of `<img alt>` values and of elements with `hidden` or a hiding inline style,
    content included; an unclosed one hides to the end (over-stripping is the safe side)."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=False)
        self.text, self.starts = text, [0]
        for ln in text.split("\n"):
            self.starts.append(self.starts[-1] + len(ln) + 1)
        self.spans: List[Tuple[int, int, str]] = []
        self.stack: List[Tuple[str, int]] = []  # (tag, start offset if it hides, else -1)

    def _at(self) -> int:
        line, col = self.getpos()
        return self.starts[line - 1] + col

    def handle_starttag(self, tag, attrs):
        at, raw = self._at(), self.get_starttag_text() or ""
        a = {k: v or "" for k, v in attrs}
        if tag == "img":
            for m in _ALTATTR.finditer(raw):
                g = next(i for i in (1, 2, 3) if m.group(i) is not None)
                if m.group(g) and not _MARKER.match(m.group(g)):
                    self.spans.append((at + m.start(g), at + m.end(g), "alt-text"))
        hide = "hidden" in a or bool(_HIDING_STYLE.search("".join(a.get("style", "").lower().split())))
        if tag in _VOID:
            if hide:
                self.spans.append((at, at + len(raw), "element"))
        else:
            self.stack.append((tag, at if hide else -1))

    handle_startendtag = handle_starttag  # HTML ignores the slash on a non-void element

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                opened = [s for _, s in self.stack[i:] if s >= 0]
                if opened:
                    end = self.text.find(">", self._at())
                    self.spans.append((opened[0], len(self.text) if end < 0 else end + 1, "element"))
                del self.stack[i:]
                return

    def finish(self) -> List[Tuple[int, int, str]]:
        self.close()
        return self.spans + [(s, len(self.text), "element") for _, s in self.stack if s >= 0][:1]


def _strip(text: str) -> Tuple[str, List[Tuple[int, int, str, int, int, Optional[str]]]]:
    """(text, [(first line, last line, kind, length, offset, signal)]): each hidden span becomes a marker plus the
    span's line breaks."""
    spans, blank, pos = [], [], 0
    for m in _HIDDEN.finditer(text):
        g = "alt" if m.group("alt") is not None else "comment"
        a, b = m.span(g)
        if g == "alt" and (not m.group(g).strip() or _MARKER.match(m.group(g).rstrip("\r\n"))):
            continue
        spans.append((a, b, "alt-text" if g == "alt" else "comment"))
        blank += [text[pos:a], re.sub(r"[^\r\n]", " ", text[a:b])]
        pos = b
    blanked = ("".join(blank) + text[pos:]).replace("\r", "\n")  # same length, so offsets hold
    p = _Spans(blanked)
    p.feed(blanked)
    spans += p.finish()
    out, info, pos, line, last = [], [], 0, 1, 0
    for a, b, kind in sorted(spans, key=lambda s: (s[0], -s[1])):
        if a < last:
            continue  # inside a span already removed
        line += text.count("\n", pos, a)
        nl = text.count("\n", a, b)
        out.append(text[pos:a] + "[hidden-%s removed: %d chars]" % (kind, b - a) + "".join(_BREAK.findall(text[a:b])))
        info.append((line, line + nl, kind, b - a, a, _signal(kind, text[a:b])))
        pos, last, line = b, b, line + nl
    return "".join(out) + text[pos:], info


def strip_hidden(text: str) -> Tuple[str, List[dict]]:
    """Remove hidden text from a prose or markup file. Returns the text and `[{"line", "kind", "length"}]`
    (kind: comment, alt-text, element), never the payload, plus `signal` when set; a line ends at LF, CR or CRLF."""
    out, info = _strip(text)
    return out, [dict({"line": 1 + len(_BREAK.findall(text, 0, at)), "kind": k, "length": n}, **({"signal": sg} if sg else {}))
                 for _, _, k, n, at, sg in info]


def sanitize_prose(text: Optional[str]) -> str:
    """Change-author prose for spec.md: line breaks as LF, invisible characters and hidden text removed. The
    markers stay, so no removal can join the pieces around it into new markup."""
    return _strip(strip_invisible(_BREAK.sub("\n", text or "")))[0]


def walk(diff: str):
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
    for _, path, tag, o, n, body, old in walk(diff if full is None else full):
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
        for k, (a, b, kind, length, _, _) in enumerate(info):
            for ln in range(a, b + 1):
                by_line.setdefault(ln, []).append(k)  # 6xl.84: every removal on a line gets its entry
        done[key] = (out.split("\n"), info, by_line, lines)
    res, hits, seen = diff.split("\n"), [], {}
    for i, path, tag, o, n, body, old in walk(diff):
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
            new, ks = got[0][num - 1], got[2].get(num, [])
            keys = [(where, side, k) for k in ks]
            spans = [got[1][k] for k in ks]
        else:  # not in the full-context diff: this line alone
            new, info = _strip(body)
            keys, spans = [(i, j) for j in range(len(info))], info
        res[i] = tag + new
        for key, sp in zip(keys, spans):
            if key not in seen:
                seen[key] = {"file": path, "line": i + 1, "kind": sp[2], "length": sp[3], **({"signal": sp[5]} if sp[5] else {})}
                hits.append(seen[key])
            if tag == "+":
                seen[key]["signal"] = "added"  # provenance outweighs wording: the change author controls this span
    return "\n".join(res), sorted(hits, key=lambda h: h["line"])
