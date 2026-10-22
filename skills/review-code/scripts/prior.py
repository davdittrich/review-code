"""The poster's own earlier reviews on a pull request: the forge is the ledger.

Stdlib only; shared by post.py and target.py, so neither imports the other. Pure: callers fetch the review and comment
lists (post.py with its `_pages`, target.py with `gh api`), this module reads only what it can validate.

Stored text is untrusted. A record is read only from an item written by the exact login `me`, as whole lines; a finding
id is `F-` and 10 hex digits, a head a 40 or 64 hex sha, and a state one of `open fixed outdated`. Anything else drops
the record, never the run.

Review marker: `<!-- review-code:review v1 head=<sha> [fixed=F-a,F-b] [outdated=F-c] -->`. The two lists carry the ids
earlier runs found fixed or outdated, so the next run neither reports them again nor posts a fixed id's old comment.
"""
import re
from typing import Dict, Optional

ID = r"F-[0-9a-f]{10}"
SHA = r"[0-9a-f]{40}(?:[0-9a-f]{24})?"
_IDS = ID + r"(?:," + ID + r")*"
FINDING = re.compile(r"<!-- review-code:finding (" + ID + r") v1 -->")
REVIEW = re.compile(r"<!-- review-code:review v1 head=(" + SHA + r")(?: fixed=(" + _IDS + r"))?(?: outdated=(" + _IDS + r"))? -->")
_LOCATION = re.compile(r"\*\*Location:\*\* (.+):[0-9]+ \(outside the diff\)")
# The only label lines the guard accepts for this feature (ids and counts, no repository text).
_OPEN, _GONE = r"[0-9]+ (?:open|unknown)", r"[0-9]+ fixed \(%s(?:, %s)*\)" % (ID, ID)
_OUT = r"[0-9]+ outdated \(%s(?: -> may be %s)?(?:, %s(?: -> may be %s)?)*\)" % ((ID,) * 4)
_SAME = r"[0-9]+ unknown \(%s(?:, %s)*: file unchanged since the last review\)" % (ID, ID)
_PART = "|".join((_OPEN, _GONE, _OUT, _SAME))
PRIOR_LINE = re.compile(r"\*\*Prior:\*\* (?:%s)(?:, (?:%s))*" % (_PART, _PART))
STATE_LINE = re.compile(r"\*\*State:\*\* new")


def review_mark(head: str, fixed=(), outdated=()) -> str:
    return "<!-- review-code:review v1 head=%s%s%s -->" % (
        head, " fixed=" + ",".join(sorted(fixed)) if fixed else "", " outdated=" + ",".join(sorted(outdated)) if outdated else "")


def parse_mark(line: str):
    """(head, fixed ids, outdated ids) of a whole-line review marker, else None (a malformed field drops the marker)."""
    m = REVIEW.fullmatch(line)
    return (m.group(1), frozenset((m.group(2) or "").split(",") if m.group(2) else ()),
            frozenset(m.group(3).split(",") if m.group(3) else ())) if m else None


def mark_head(line: str) -> Optional[str]:
    m = parse_mark(line)
    return m[0] if m else None


def _lines(body: str):
    return body.replace("\r\n", "\n").split("\n")  # the web editor stores CRLF


def _unescape(s: str) -> str:
    """Undo post.py's `_prose` on a path: backtick spans dropped, `&lt;` `&amp;` and doubled backslashes restored."""
    return re.sub(r"\\(.)|&(lt|amp);|`", lambda m: m.group(1) or {"lt": "<", "amp": "&"}.get(m.group(2), ""), s)


def _path(v) -> Optional[str]:
    return v if isinstance(v, str) and 0 < len(v) <= 4096 and not re.search(r"[\x00-\x1f\x7f]", v) else None


def _sha(v) -> Optional[str]:
    return v if isinstance(v, str) and re.fullmatch(SHA, v) else None


def _own(item, me) -> bool:
    user = item.get("user") if isinstance(item, dict) else None
    return isinstance(user, dict) and user.get("login") == me and isinstance(item.get("body"), str)


def read(reviews: list, comments: list, me) -> dict:
    """{"findings": {id: {path, head, state}}, "last_head": sha or None, "fixed": set, "outdated": set} from the review and
    comment lists (oldest first) of one PR. Only items whose author login is exactly `me` count. The carried sets and
    `last_head` come from the newest own review whose body holds a valid review marker; a finding's path is the REST
    comment `path`, else the `**Location:**` line above its marker in an own review body."""
    found, last = {}, None
    for kind, items in (("review", reviews), ("comment", comments)):
        for item in items:
            if not _own(item, me):
                continue
            marks = [m for m in map(parse_mark, _lines(item["body"])) if m] if kind == "review" else []
            last = marks[-1] if marks else last
            loc = None
            for ln in _lines(item["body"]):
                m = _LOCATION.fullmatch(ln)
                if m:
                    loc = _path(_unescape(m.group(1)))
                elif ln == "---":
                    loc = None
                elif FINDING.fullmatch(ln):
                    rec = found.setdefault(FINDING.fullmatch(ln).group(1), {"path": None, "head": None})
                    rec["path"] = _path(item.get("path")) or loc or rec["path"]
                    rec["head"] = _sha(item.get("commit_id")) or (marks[-1][0] if marks else None) or rec["head"]
                    loc = None
    fixed, outdated = (last[1], last[2]) if last else (frozenset(), frozenset())
    for i, rec in found.items():
        rec["state"] = "fixed" if i in fixed else "outdated" if i in outdated else "open"
    return {"findings": found, "last_head": last[0] if last else None, "fixed": set(fixed), "outdated": set(outdated)}


def derive(old: dict, now: Dict[str, str], scope, complete: bool, unverifiable=(), changed=None) -> dict:
    """Set algebra over P (`old["findings"]`, with the carried sets) and R (`now`: this run's finding id -> path).
    new = R - P, plus any id earlier found fixed; open = R & P; a prior id absent from R is `outdated` when a new id sits
    on its path (the quote moved or changed, so the hash did; not claimed fixed), `fixed` when the run is complete, the
    path was in scope, no new id sits there and `changed` (the paths git says differ between the prior review head and this
    head; None when that is unknown) holds the path ("no longer found by this review", never proven), else `unknown`; an
    absent id whose file did not change is also listed in `unchanged`, since a lane that missed it proves nothing.
    Ids already carried as fixed or outdated are not reported again. `fixed_out` and `outdated_out` are the sets to carry."""
    old_f, old_o, P, R = old["fixed"], old["outdated"], old["findings"], set(now)
    new = (R - set(P)) | (R & old_f)
    out = {"new": new, "open": (R & set(P)) - old_f, "fixed": set(), "outdated": {}, "unknown": set(), "unchanged": set()}
    for i in sorted(set(P) - R - old_f - old_o):
        path = P[i]["path"]
        twin = sorted(n for n in new if now[n] == path)
        if path is None or i in unverifiable:
            out["unknown"].add(i)
        elif twin:
            out["outdated"][i] = twin[0]
        elif complete and path in scope and changed is not None and path in changed:
            out["fixed"].add(i)
        elif complete and path in scope and changed is not None:
            out["unknown"].add(i)
            out["unchanged"].add(i)
        else:
            out["unknown"].add(i)
    out["fixed_out"] = (old_f | out["fixed"]) - R
    out["outdated_out"] = (old_o | set(out["outdated"])) - R
    return out


def render(d: dict) -> Optional[str]:
    """The `**Prior:**` line (ids and counts only), or None when there is nothing to say."""
    parts = ["%d open" % len(d["open"])] if d["open"] else []
    parts += ["%d fixed (%s)" % (len(d["fixed"]), ", ".join(sorted(d["fixed"])))] if d["fixed"] else []
    parts += ["%d outdated (%s)" % (len(d["outdated"]), ", ".join("%s -> may be %s" % kv for kv in sorted(d["outdated"].items())))
              ] if d["outdated"] else []
    rest = d["unknown"] - d["unchanged"]
    parts += ["%d unknown" % len(rest)] if rest else []
    parts += ["%d unknown (%s: file unchanged since the last review)" % (len(d["unchanged"]), ", ".join(sorted(d["unchanged"])))
              ] if d["unchanged"] else []
    return "**Prior:** " + ", ".join(parts) if parts else None
