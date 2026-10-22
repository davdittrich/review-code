"""Does an edit to a test file weaken its tests: fewer assertion lines, or more skip markers. Pure: bytes in, text out."""
import difflib
import itertools
import re
from typing import Optional

_C = ("#",)
_SLASH = ("//", "/*", "*")
# suffix -> (comment prefixes, assertion line, skip marker). shortcut: line patterns, not a parse; a weaker assertion that
# keeps the count is not seen, upgrade with a per-language parser.
_JS = (_SLASH, re.compile(r"\bexpect\s*\(|\bassert\b|\.should\b"),
       re.compile(r"\b(?:it|test|describe|context)\.(?:skip|only)\b|\bx(?:it|describe|test)\s*\(|\b(?:fit|fdescribe)\s*\("
                  r"|\bthis\.skip\s*\("))
_JAVA = (_SLASH, re.compile(r"\bassert\w*\s*\(|\bAssert\.|\bfail\s*\("), re.compile(r"@Ignore\b|@Disabled\b"))
RULES = {
    ".py": (_C, re.compile(r"\bassert\b|\bself\.assert\w*\(|\bpytest\.raises\b|\bself\.fail\("),
            re.compile(r"\b(?:skip|skipif|skipIf|skipUnless|skipTest|SkipTest|xfail|importorskip)\b")),
    ".rb": (_C, re.compile(r"\bexpect\s*[({]|\bassert\w*|\bshould\b"), re.compile(r"\b(?:skip|pending|xit|xdescribe|xcontext)\b")),
    ".go": (_SLASH, re.compile(r"\b(?:assert|require)\.\w+|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\b"),
            re.compile(r"\bt\.Skip\w*")),
    ".cs": (_SLASH, re.compile(r"\bAssert\.\w+|\.Should\b"), re.compile(r"\[Ignore\b|\bSkip\s*=")),
}
# a suffix with no rule: an added skip or exit line is a marker too. shortcut: a word list, upgrade with a rule per language
_ANY_SKIP = re.compile(r"\b(?:skip\w*|exit|quit)\b", re.I)
for _s in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
    RULES[_s] = _JS
for _s in (".java", ".kt"):
    RULES[_s] = _JAVA


def changed(pre: bytes, now: bytes) -> tuple:
    """(removed, added) lines of a zero-context diff; latin-1 maps bytes 1:1 so only b"\n" splits lines and no input raises."""
    d = difflib.unified_diff(pre.decode("latin-1").split("\n"), now.decode("latin-1").split("\n"), n=0, lineterm="")
    diff = list(itertools.islice(d, 2, None))  # islice drops the two headers
    return [x[1:] for x in diff if x[0] == "-"], [x[1:] for x in diff if x[0] == "+"]


def weakened(path: str, pre: bytes, now: bytes) -> Optional[str]:
    """None when the edit keeps every assertion line and adds no skip marker, else a short reason. A suffix with no rule
    gets the strictest one: any removed line, and a modified line is a removed one, and an added skip or exit line."""
    gone, came = changed(pre, now)
    suffix = "." + path.rpartition(".")[2].lower()
    rule = RULES.get(suffix)
    if rule is None:
        if gone:
            return "%d line(s) removed or changed in a test file of unchecked type" % len(gone)
        return "added a skip or exit line in a test file of unchecked type" if _count(came, _ANY_SKIP) > 0 else None

    a, b = _count(gone, rule[1], rule[0]), _count(came, rule[1], rule[0])
    if a > b:
        return "assertion lines %d -> %d" % (a, b)
    # a skip marker is a marker even inside a comment-looking decorator line, so skip lines are counted raw
    if _count(came, rule[2]) > _count(gone, rule[2]):
        return "added a skip marker"
    return None


def _count(lines: list, pat, comment: tuple = ()) -> int:
    return sum(1 for ln in lines if not (comment and ln.lstrip().startswith(comment)) and pat.search(ln))
