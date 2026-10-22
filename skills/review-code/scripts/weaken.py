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
       re.compile(r"\b(?:it|test|describe|context)\.(?:skip|only)\b|\bx(?:it|describe|test)\s*\(|\b(?:fit|fdescribe)\s*\("))
_JAVA = (_SLASH, re.compile(r"\bassert\w*\s*\(|\bAssert\.|\bfail\s*\("), re.compile(r"@Ignore\b|@Disabled\b"))
RULES = {
    ".py": (_C, re.compile(r"\bassert\b|\bself\.assert\w*\(|\bpytest\.raises\b|\bself\.fail\("),
            re.compile(r"\b(?:skip|skipIf|skipUnless|xfail|importorskip)\b")),
    ".rb": (_C, re.compile(r"\bexpect\s*[({]|\bassert\w*|\bshould\b"), re.compile(r"\b(?:skip|pending|xit|xdescribe|xcontext)\b")),
    ".go": (("//", "/*", "*"), re.compile(r"\b(?:assert|require)\.\w+|\bt\.(?:Error|Errorf|Fatal|Fatalf|Fail|FailNow)\b"),
            re.compile(r"\bt\.Skip\w*")),
    ".cs": (_SLASH, re.compile(r"\bAssert\.\w+|\.Should\b"), re.compile(r"\[Ignore\b|\bSkip\s*=")),
}
for _s in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
    RULES[_s] = _JS
for _s in (".java", ".kt"):
    RULES[_s] = _JAVA


def _split(data: bytes) -> list:
    return data.decode("latin-1").split("\n")  # 1:1 bytes, so only b"\n" splits and no input raises


def weakened(path: str, pre: bytes, now: bytes) -> Optional[str]:
    """None when the edit keeps every assertion line and adds no skip marker, else a short reason. A suffix with no rule
    gets the strictest one: any removed line, and a modified line is a removed one."""
    diff = list(itertools.islice(difflib.unified_diff(_split(pre), _split(now), n=0, lineterm=""), 2, None))
    gone = [x[1:] for x in diff if x[0] == "-"]
    came = [x[1:] for x in diff if x[0] == "+"]
    suffix = "." + path.rpartition(".")[2].lower()
    rule = RULES.get(suffix)
    if rule is None:
        return "%d line(s) removed or changed in a test file of unchecked type" % len(gone) if gone else None

    def count(lines: list, pat) -> int:
        return sum(1 for ln in lines if not ln.lstrip().startswith(rule[0]) and pat.search(ln))

    a, b = count(gone, rule[1]), count(came, rule[1])
    if a > b:
        return "assertion lines %d -> %d" % (a, b)
    # a skip marker is a marker even inside a comment-looking decorator line, so skip lines are counted raw
    s, t = sum(1 for ln in gone if rule[2].search(ln)), sum(1 for ln in came if rule[2].search(ln))
    if t > s:
        return "added a skip marker"
    return None
