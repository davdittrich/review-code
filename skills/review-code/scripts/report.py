"""Turn finder and verifier JSON lines into one schema v1 review document.

Stdlib only, Python 3.9 grammar. The LLM supplies claims and verdicts; this script derives id, evidence class,
score, origin, severity cap and order. Subcommands (stdin is JSON lines; stdout is one JSON status line, except
`render`, which prints the report):

    report.py ingest   --run DIR   candidate lines + one {"inspected": [path, ...]} line
    report.py verdicts --run DIR   {"id", "verdict", "evidence": [{"path","line","quote"}], "reason"} lines
    report.py render   --run DIR [--format text|json|sarif]

Document (json output; no run path, no timestamps, so it is byte-reproducible):
  schema_version, target{kind, head_sha, merge_base, label, not_a_change}, trust (own|external), verdict,
  findings[] (class reproduced|traced), unverifiable[] (class inferred|speculative), dropped{refuted,
  do_not_flag, invalid}, inspected{files, ledger_counts, areas}.
  Item keys: id, path, line, end_line, severity (blocking|required|suggestion|noted), category, title, trigger,
  quote, evidence[{path, line, quote}], evidence_class, score, verified, origin (introduced|pre-existing|
  existing), fix, reason, source_lane ("defects").
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import hashlib
import json
import os
import secrets
import shutil

import ledger
import sanitize
import target

SCHEMA_VERSION = 1
CANDIDATE_REQUIRED = ("path", "line", "severity", "category", "title", "trigger", "quote")
CANDIDATE_OPTIONAL = ("end_line", "fix")
VERDICT_KEYS = ("id", "verdict", "evidence", "reason")
CITE_KEYS = ("path", "line", "quote")
SEVERITIES = ("blocking", "required", "suggestion", "noted")  # rank order
CATEGORIES = ("security", "data-loss", "race", "accessibility", "doc-mismatch", "constructor-divergence",
              "bug", "test", "performance", "other")
BLOCKING = CATEGORIES[:6]
VERDICTS = ("confirmed", "refuted", "unverifiable")
SCORE = {"reproduced": 100, "traced": 75, "inferred": 50, "speculative": 25}
ID_RE = r"F-[0-9a-f]{10}"
WHOLE = ("path", "repo")  # targets that are not a change: every finding is `existing`


def _ws(s):
    return " ".join(s.split())


def _int(x):
    return type(x) is int  # bool is not an int here


def _real(tree, path):
    """Real path of tree/path when it stays inside tree/ and is a regular file, else None."""
    if not isinstance(path, str) or not path or "\0" in path or os.path.isabs(path):
        return None
    real = os.path.realpath(os.path.join(tree, os.path.normpath(path)))
    return real if real.startswith(tree + os.sep) and os.path.isfile(real) else None


def _cited(tree, path, line, end, quote):
    """The cited lines when path, range and quote all hold in tree/, else None."""
    real = _real(tree, path)
    if real is None or not (_int(line) and _int(end) and 1 <= line <= end) or not isinstance(quote, str):
        return None
    try:
        with open(real, encoding="utf-8", errors="replace") as fh:
            rows = fh.read().split("\n")
    except OSError:
        return None
    if rows[-1] == "":
        rows.pop()
    if end > len(rows):
        return None
    got = rows[line - 1:end]
    return got if _ws(quote) and _ws(quote) in _ws(" ".join(got)) else None


def _load(run, name):
    try:
        with open(os.path.join(run, name), encoding="utf-8") as fh:
            obj = json.load(fh)
    except FileNotFoundError:
        raise target.Refuse("no-" + name.split(".")[0], "the run directory has no " + name)
    except (OSError, ValueError, RecursionError):
        raise target.Refuse("bad-" + name.split(".")[0], name + " cannot be read")
    if not isinstance(obj, dict):
        raise target.Refuse("bad-" + name.split(".")[0], name + " is not an object")
    return obj


def _objects(text):
    """Every `{` line of the stream: the parsed object, or None when it is not a JSON object. Prose is skipped."""
    for ln in text.split("\n"):
        if ln.lstrip().startswith("{"):
            try:
                o = json.loads(ln)
            except (ValueError, RecursionError):
                o = None
            yield o if isinstance(o, dict) else None


def _candidate(o, tree):
    for k in CANDIDATE_REQUIRED:
        if k not in o:
            return None
    end = o.get("end_line", o["line"])
    fix = o.get("fix", "")
    c = {"path": o["path"], "line": o["line"], "end_line": end, "severity": o["severity"],
         "category": o["category"], "title": o["title"], "trigger": o["trigger"], "quote": o["quote"], "fix": fix}
    if (not isinstance(c["path"], str) or c["severity"] not in SEVERITIES or c["category"] not in CATEGORIES
            or not isinstance(fix, str) or not all(isinstance(c[k], str) and c[k] for k in ("title", "trigger", "quote"))):
        return None
    c["path"] = os.path.normpath(c["path"])
    if _cited(tree, c["path"], c["line"], end, c["quote"]) is None:
        return None
    c["id"] = "F-" + hashlib.sha256("\0".join((c["path"], c["category"], _ws(c["quote"]))).encode(
        "utf-8", "surrogatepass")).hexdigest()[:10]
    return c


def _handoff(c, run, nonce):
    loc = "%s:%d%s" % (c["path"], c["line"], "-%d" % c["end_line"] if c["end_line"] != c["line"] else "")
    return "# Verify finding %s\n\nid: %s\nclaim:\n%s\nlocation: %s\ntrigger:\n%s\ntree: %s\n" % (
        c["id"], c["id"], ledger._block(c["title"], nonce), json.dumps(loc),
        ledger._block(c["trigger"], nonce), json.dumps(os.path.join(run, "tree")))


def ingest(run, text):
    _load(run, "target.json")
    tree = os.path.realpath(os.path.join(run, "tree"))
    nonce = secrets.token_hex(8)
    kept, areas, invalid = {}, [], 0
    for o in _objects(text):
        if o is not None and "inspected" in o and "path" not in o:
            areas += [a for a in o["inspected"] if isinstance(a, str)] if isinstance(o["inspected"], list) else []
            continue
        c = _candidate(o, tree) if o is not None else None
        if c is None or c["id"] in kept:
            invalid += 1
        else:
            kept[c["id"]] = c
    for stale in ("handoff", "repro"):
        shutil.rmtree(os.path.join(run, stale), ignore_errors=True)
    if os.path.exists(os.path.join(run, "verdicts.json")):
        os.remove(os.path.join(run, "verdicts.json"))
    hand = os.path.join(run, "handoff")
    os.makedirs(hand, mode=0o700)
    for c in kept.values():
        target.write_private(os.path.join(hand, c["id"] + ".md"), _handoff(c, run, nonce))
    dropped = {"invalid": invalid, "do_not_flag": 0}
    target.write_private(os.path.join(run, "candidates.json"), json.dumps(
        {"candidates": sorted(kept.values(), key=lambda c: c["id"]), "dropped": dropped, "inspected": areas},
        sort_keys=True, indent=2))
    return {"status": "ok", "ids": sorted(kept), "dropped": dropped, "handoff": hand}


def verdicts(run, text):
    cand = _load(run, "candidates.json")
    tree = os.path.realpath(os.path.join(run, "tree"))
    ids = {c["id"] for c in cand["candidates"]}
    got = {}
    for o in _objects(text):
        if o is None or o.get("id") not in ids or o["id"] in got or o.get("verdict") not in VERDICTS:
            continue
        ev = o.get("evidence")
        cites = [e for e in (ev if isinstance(ev, list) else []) if isinstance(e, dict)
                 and _cited(tree, e.get("path"), e.get("line"), e.get("line"), e.get("quote")) is not None]
        reason = o.get("reason")
        got[o["id"]] = {"verdict": o["verdict"], "reason": reason if isinstance(reason, str) else "",
                        "cites": [{k: e[k] for k in CITE_KEYS} for e in cites]}
    target.write_private(os.path.join(run, "verdicts.json"), json.dumps(got, sort_keys=True, indent=2))
    conf = sorted(i for i, v in got.items() if v["verdict"] == "confirmed" and v["cites"])
    ref = sorted(i for i, v in got.items() if v["verdict"] == "refuted")
    return {"status": "ok", "confirmed": conf, "refuted": ref, "unverifiable": sorted(ids - set(conf) - set(ref))}


def build(run):
    tj, led, cand = _load(run, "target.json"), ledger._load(run), _load(run, "candidates.json")
    try:
        ver = _load(run, "verdicts.json")
    except target.Refuse as r:
        if r.reason != "no-verdicts":
            raise
        ver = {}
    tree = os.path.realpath(os.path.join(run, "tree"))
    whole = tj["kind"] in WHOLE
    with open(os.path.join(run, "change.diff"), encoding="utf-8", errors="replace") as fh:
        added = {}
        for _, p, tag, _, n, _, _ in sanitize._walk(fh.read()):
            if tag == "+":
                added.setdefault(p, set()).add(n)
    findings = []
    for c in cand["candidates"]:
        v = ver.get(c["id"], {})
        cites = v.get("cites", [])
        if not (v.get("verdict") == "confirmed" and cites):
            continue
        origin = "existing" if whole else "introduced" if any(
            n in added.get(c["path"], ()) for n in range(c["line"], c["end_line"] + 1)) else "pre-existing"
        findings.append(dict(c, evidence=cites, evidence_class="traced", score=SCORE["traced"], verified=True,
                             origin=origin, reason=v["reason"], source_lane="defects"))
    areas = sorted({a for a in cand["inspected"] if _real(tree, a)})
    return {"schema_version": SCHEMA_VERSION,
            "target": {"kind": tj["kind"], "head_sha": tj["head_sha"], "merge_base": tj["merge_base"],
                       "label": led["label"], "not_a_change": whole},
            "trust": "own" if ledger._stored(led) == "own" else "external",
            "verdict": "No blocking findings", "findings": findings, "unverifiable": [],
            "dropped": {"refuted": 0, "do_not_flag": 0, "invalid": cand["dropped"]["invalid"]},
            "inspected": {"files": tj["included"], "areas": areas,
                          "ledger_counts": {k: len(led[k]) for k in ledger.CATEGORIES}}}


def render(run, fmt):
    doc = build(run)
    return json.dumps(doc, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _usage():
    return target.Refuse("usage", "usage: report.py ingest|verdicts --run DIR | render --run DIR [--format text|json|sarif]")


def main(argv):
    try:
        sub, rest = (argv[0], argv[1:]) if argv else ("", [])
        if sub not in ("ingest", "verdicts", "render") or rest[:1] != ["--run"] or len(rest) < 2:
            raise _usage()
        run, tail = rest[1], rest[2:]
        if sub == "render":
            if tail not in ([], ["--format", "text"], ["--format", "json"], ["--format", "sarif"]):
                raise _usage()
            sys.stdout.write(render(run, tail[1] if tail else "text"))
            return 0
        if tail:
            raise _usage()
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
        out = (ingest if sub == "ingest" else verdicts)(run, text)
    except target.Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason}, 1
    except (OSError, KeyError, ValueError, TypeError) as e:  # the skill reads one JSON line; never a traceback
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    else:
        rc = 0
    print(json.dumps(out, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
