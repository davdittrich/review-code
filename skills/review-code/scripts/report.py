"""Turn finder and verifier JSON lines into one schema v1 review document.

Stdlib only, Python 3.9 grammar. The LLM supplies claims and verdicts; this script derives id, evidence class,
score, origin, severity cap and order. Subcommands (stdin is JSON lines; stdout is one JSON status line, except
`render`, which prints the report):

    report.py ingest   --run DIR [--lane defects|standards|spec|sota]
                                   candidate lines + one {"inspected": [path, ...]} line; additive per lane
    report.py verdicts --run DIR   {"id", "verdict", "evidence": [{"path","line","quote"}], "reason"} lines
    report.py render   --run DIR [--format text|json|sarif]

Document (json output; no run path, no timestamps, so it is byte-reproducible):
  schema_version, target{kind, head_sha, merge_base, label, not_a_change}, trust (own|external), verdict,
  findings[] (class reproduced|traced), unverifiable[] (class inferred|speculative), dropped{refuted,
  do_not_flag, invalid}, inspected{files, ledger_counts, areas}.
  Item keys: id, path, line, end_line, severity (blocking|required|suggestion|noted), category, title, trigger,
  quote, evidence[{path, line, quote}], evidence_class, score, verified, origin (introduced|pre-existing|
  existing), fix, reason, axis (defects|standards|spec), source_lane (the --lane of the ingest, never model output).
A Standards `convention` item also carries rule and rule_source. skipped[] lists what plan.json left out.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import hashlib
import json
import os
import re
import secrets
import shutil
from urllib.parse import quote

import ledger
import sanitize
import target

SCHEMA_VERSION = 1
CANDIDATE_REQUIRED = ("path", "line", "severity", "category", "title", "trigger", "quote")
CANDIDATE_OPTIONAL = ("end_line", "fix")
AXES = ("defects", "standards", "spec")
LANE_AXIS = {"defects": "defects", "standards": "standards", "sota": "standards", "spec": "spec"}
VERDICT_KEYS = ("id", "verdict", "evidence", "reason")
CITE_KEYS = ("path", "line", "quote")
SEVERITIES = ("blocking", "required", "suggestion", "noted")  # rank order
CATEGORIES = ("security", "data-loss", "race", "accessibility", "doc-mismatch", "constructor-divergence",
              "bug", "test", "performance", "other")
BLOCKING = CATEGORIES[:6]
LANE_CATEGORIES = {"defects": CATEGORIES, "standards": ("convention", "comment", "smell", "lean"),
                   "sota": ("sota",), "spec": ("spec-missing", "spec-partial", "spec-wrong", "scope-creep")}
CEILING = {"convention": "required", "comment": "required", "smell": "suggestion", "lean": "suggestion",
           "sota": "suggestion", "spec-missing": "required", "spec-partial": "required", "spec-wrong": "required",
           "scope-creep": "required"}  # strongest severity a non-Defects category may reach
LANE_OPTIONAL = {"defects": (), "standards": ("rule", "rule_source"), "sota": ("sources",), "spec": ("spec_line",)}
VERDICTS = ("confirmed", "refuted", "unverifiable")
SCORE = {"reproduced": 100, "traced": 75, "inferred": 50, "speculative": 25}
ID_RE = r"F-[0-9a-f]{10}"
LINT_IGNORE = re.compile(r"noqa|type: ?ignore|pylint: ?disable|eslint-disable|@ts-ignore|@ts-expect-error|nolint|NOSONAR|"
                         r"rubocop: ?disable|#\[allow\(|@SuppressWarnings|nosec", re.IGNORECASE)
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


def _rule(o, run):
    """(rule, rule_source) of a convention line when the rule text occurs in the named conventions/ file, else None."""
    rule, src = o.get("rule"), o.get("rule_source")
    if not (isinstance(rule, str) and _ws(rule) and len(rule) <= 500 and isinstance(src, str)):
        return None
    rel = os.path.normpath(src) if src and "\0" not in src else ""
    if not rel.startswith("conventions" + os.sep):
        return None
    real = _real(os.path.realpath(os.path.join(run, "conventions")), rel[len("conventions" + os.sep):])
    if real is None:
        return None
    with open(real, encoding="utf-8", errors="replace") as fh:
        return (rule, rel) if _ws(rule) in _ws(fh.read()) else None


def _candidate(o, tree, lane, run):
    for k in CANDIDATE_REQUIRED:
        if k not in o:
            return None
    end = o.get("end_line", o["line"])
    fix = o.get("fix", "")
    c = {"path": o["path"], "line": o["line"], "end_line": end, "severity": o["severity"],
         "category": o["category"], "title": o["title"], "trigger": o["trigger"], "quote": o["quote"], "fix": fix}
    if (not isinstance(c["path"], str) or c["severity"] not in SEVERITIES or c["category"] not in LANE_CATEGORIES[lane]
            or not isinstance(fix, str) or not all(isinstance(c[k], str) and c[k] for k in ("title", "trigger", "quote"))):
        return None
    c["path"] = os.path.normpath(c["path"])
    c["rows"] = _cited(tree, c["path"], c["line"], end, c["quote"])
    if c["rows"] is None:
        return None
    if c["category"] == "convention":
        r = _rule(o, run)
        if r is None:
            return None
        c["rule"], c["rule_source"] = r
    if isinstance(o.get("sources"), list) and "sources" in LANE_OPTIONAL[lane]:
        c["sources"] = [x for x in o["sources"] if isinstance(x, str)]
    if _int(o.get("spec_line")) and "spec_line" in LANE_OPTIONAL[lane]:
        c["spec_line"] = o["spec_line"]
    c["axis"], c["source_lane"] = LANE_AXIS[lane], lane
    key = (c["path"], c["category"], _ws(c["quote"]))  # Defects keeps the Phase 3 input; other axes salt it
    c["id"] = "F-" + hashlib.sha256("\0".join(key if c["axis"] == "defects" else (c["axis"],) + key).encode(
        "utf-8", "surrogatepass")).hexdigest()[:10]
    return c


def _handoff(c, run, nonce):
    loc = "%s:%d%s" % (c["path"], c["line"], "-%d" % c["end_line"] if c["end_line"] != c["line"] else "")
    out = "# Verify finding %s\n\nid: %s\nclaim:\n%s\nlocation: %s\ntrigger:\n%s\ntree: %s\n" % (
        c["id"], c["id"], ledger._block(c["title"], nonce), json.dumps(loc),
        ledger._block(c["trigger"], nonce), json.dumps(os.path.join(run, "tree")))
    if c["axis"] == "defects":
        return out
    out += "check: %s\n" % ("rule" if c["category"] == "convention" else
                            "spec" if c["axis"] == "spec" else "property")
    return out + ("rule:\n%s\n" % ledger._block(c["rule"], nonce) if c["category"] == "convention" else "")


def _read_lane(text, lane, tree, run):
    kept, areas, invalid, ignored = {}, [], 0, 0
    for o in _objects(text):
        if o is not None and "inspected" in o and "path" not in o:
            areas += [a for a in o["inspected"] if isinstance(a, str)] if isinstance(o["inspected"], list) else []
            continue
        c = _candidate(o, tree, lane, run) if o is not None else None
        if c is None or c["id"] in kept:
            invalid += 1
        elif LINT_IGNORE.search("\n".join(c.pop("rows"))):  # VERI-05: the author ignored it on purpose
            ignored += 1
        else:
            kept[c["id"]] = c
    return {"candidates": sorted(kept.values(), key=lambda c: c["id"]), "inspected": areas,
            "dropped": {"invalid": invalid, "do_not_flag": ignored}}


def ingest(run, text, lane="defects"):
    _load(run, "target.json")
    tree = os.path.realpath(os.path.join(run, "tree"))
    nonce = secrets.token_hex(8)
    try:
        lanes = _load(run, "candidates.json").get("lanes")
    except target.Refuse:
        lanes = None
    lanes = lanes if isinstance(lanes, dict) else {}
    lanes[lane] = _read_lane(text, lane, tree, run)  # re-ingesting a lane replaces only that lane
    cands = sorted((c for n in sorted(lanes) for c in lanes[n]["candidates"]), key=lambda c: c["id"])
    dropped = {k: sum(v["dropped"][k] for v in lanes.values()) for k in ("invalid", "do_not_flag")}
    for stale in ("handoff", "repro"):
        shutil.rmtree(os.path.join(run, stale), ignore_errors=True)
    if os.path.exists(os.path.join(run, "verdicts.json")):
        os.remove(os.path.join(run, "verdicts.json"))
    hand = os.path.join(run, "handoff")
    os.makedirs(hand, mode=0o700)
    for c in cands:
        target.write_private(os.path.join(hand, c["id"] + ".md"), _handoff(c, run, nonce))
    target.write_private(os.path.join(run, "candidates.json"), json.dumps(
        {"candidates": cands, "dropped": dropped, "inspected": [a for n in sorted(lanes) for a in lanes[n]["inspected"]],
         "lanes": lanes}, sort_keys=True, indent=2))
    return {"status": "ok", "ids": [c["id"] for c in cands], "dropped": dropped, "handoff": hand}


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


def _cap(cls, category):
    """Strongest severity the evidence class allows (VERI-03)."""
    if cls in ("reproduced", "traced"):
        return "blocking" if category in BLOCKING else "required"
    if cls == "inferred":
        return "required" if category == "accessibility" else "suggestion"
    return "noted"


def _repro(run, fid):
    try:
        with open(os.path.join(run, "repro", fid + ".json"), encoding="utf-8") as fh:
            return json.load(fh).get("outcome")
    except (OSError, ValueError, AttributeError):
        return None


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
    items, dropped = [], dict(cand["dropped"], refuted=0)
    for c in cand["candidates"]:
        v = ver.get(c["id"], {})
        cites, kind = v.get("cites", []), v.get("verdict")
        if kind == "refuted":
            dropped["refuted"] += 1
            continue
        if kind == "confirmed" and cites:
            cls, reason = ("reproduced" if _repro(run, c["id"]) == "reproduced" else "traced"), v["reason"]
        else:
            cls = "inferred" if cites else "speculative"
            reason = v["reason"] if kind == "unverifiable" else "no-valid-cite" if kind == "confirmed" else "no-verdict"
        origin = "existing" if whole else "introduced" if any(
            n in added.get(c["path"], ()) for n in range(c["line"], c["end_line"] + 1)) else "pre-existing"
        sev = SEVERITIES[max(SEVERITIES.index(c["severity"]), SEVERITIES.index(_cap(cls, c["category"])),
                             SEVERITIES.index(CEILING.get(c["category"], SEVERITIES[0])))]
        if origin == "pre-existing" and sev in SEVERITIES[2:]:  # VERI-05; Blocking and Required stay (VERI-06)
            dropped["do_not_flag"] += 1
            continue
        items.append(dict(c, severity=sev, evidence=cites, evidence_class=cls, score=SCORE[cls],
                          verified=cls in ("reproduced", "traced"), origin=origin, reason=reason))
    items.sort(key=lambda i: (AXES.index(i["axis"]), SEVERITIES.index(i["severity"]), i["path"], i["line"], i["id"]))
    findings = [i for i in items if i["verified"]]
    unverifiable = [i for i in items if not i["verified"]]
    rank = {i["severity"] for i in findings}
    # Defects: any unverifiable item needs discussion. Standards and Spec: only an item at required does, so
    # unverifiable smell, lean and comment suggestions never flip the verdict; SOTA never counts (its ceiling is suggestion).
    label = ("Needs changes" if "blocking" in rank else
             "Needs discussion" if any(i["severity"] == "required" for i in items if i["axis"] != "defects")
             or "required" in rank or any(i["axis"] == "defects" for i in unverifiable) else "No blocking findings")
    areas = sorted({a for a in cand["inspected"] if _real(tree, a)})
    try:
        skipped = _load(run, "plan.json").get("skipped")
    except target.Refuse:
        skipped = None
    skipped = [s for s in skipped if isinstance(s, dict)] if isinstance(skipped, list) else []
    spec = led.get("spec")
    if (not isinstance(spec, dict) or spec.get("status") == "none") and not any(s.get("what") == "spec" for s in skipped):
        skipped.append({"what": "spec", "why": "no spec"})
    return {"schema_version": SCHEMA_VERSION,
            "target": {"kind": tj["kind"], "head_sha": tj["head_sha"], "merge_base": tj["merge_base"],
                       "label": led["label"], "not_a_change": whole},
            "trust": "own" if ledger._stored(led) == "own" else "external",
            "verdict": label, "findings": findings, "unverifiable": unverifiable, "dropped": dropped, "skipped": skipped,
            "inspected": {"files": tj["included"], "areas": areas,
                          "ledger_counts": {k: len(led[k]) for k in ledger.CATEGORIES}}}


_LEVEL = {"blocking": "error", "required": "warning", "suggestion": "note", "noted": "note"}


def _sarif(doc):
    fs = doc["findings"]
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0", "runs": [{
        "tool": {"driver": {"name": "review-code",
                            "rules": [{"id": "review-code/" + c} for c in sorted({f["category"] for f in fs})]}},
        "results": [{"ruleId": "review-code/" + f["category"], "level": _LEVEL[f["severity"]],
                     "message": {"text": f["title"]},
                     "locations": [{"physicalLocation": {"artifactLocation": {"uri": quote(f["path"])},
                                                         "region": {"startLine": f["line"], "endLine": f["end_line"]}}}],
                     "partialFingerprints": {"reviewCodeId/v1": f["id"]},
                     "properties": {"evidenceClass": f["evidence_class"], "severity": f["severity"],
                                    "origin": f["origin"], "score": f["score"]}} for f in fs]}]}


def _text(doc):
    def e(x):  # repo and LLM text reaches the terminal only through here; one line each, so no CR overwrite
        return sanitize.escape_for_code(" ".join(str(x).split()))[0]

    def item(i):
        out = ["- [%s] %s:%d  %s (%s, score %d, %s)" % (i["id"], e(i["path"]), i["line"], e(i["title"]),
                                                      i["evidence_class"], i["score"], i["origin"]),
               "    trigger: " + e(i["trigger"])]
        out += ["    evidence: %s:%d  %s" % (e(c["path"]), c["line"], e(c["quote"])) for c in i["evidence"]]
        return out + (["    fix: " + e(i["fix"])] if i["fix"] else [])

    t, d, fs = doc["target"], doc["dropped"], doc["findings"]
    out = ["Summary", "verdict: " + doc["verdict"], "target: %s %s (merge base %s), ledger %s" % (
        e(t["kind"]), e(t["head_sha"]), e(t["merge_base"]), e(t["label"]))]
    if t["not_a_change"]:
        out.append("Target is not a change: whole-file findings")
    out += ["findings: %d, unverifiable: %d" % (len(fs), len(doc["unverifiable"])),
            "dropped: refuted %d, do-not-flag %d, invalid %d" % (d["refuted"], d["do_not_flag"], d["invalid"]),
            "trust: " + doc["trust"]]
    for ax in AXES:
        mine = [i for i in fs if i["axis"] == ax]
        out.append("%s: %d finding%s, worst %s" % (ax, len(mine), "" if len(mine) == 1 else "s", min(
            (i["severity"] for i in mine), key=SEVERITIES.index, default="none")))
    out.append("")

    def section(head, rows):
        return [head] + (rows or ["none"]) + [""]

    for ax in AXES:
        out.append(ax.capitalize())
        unv = [i for i in doc["unverifiable"] if i["axis"] == ax]
        if ax != "defects" and not unv and not any(i["axis"] == ax for i in fs):
            why = [s.get("why") for s in doc["skipped"] if s.get("what") == ax]
            out += ["%s: skipped (%s)" % (ax.capitalize(), e(why[0])) if why else "none", ""]
            continue
        for head, pick in (("Blocking", ("blocking",)), ("Required", ("required",)),
                           ("Suggestions", ("suggestion", "noted"))):
            out += section(head, [x for i in fs if i["axis"] == ax and i["severity"] in pick for x in item(i)])
        out += section("Unverifiable", ["- [%s] %s:%d  %s (%s): %s" % (i["id"], e(i["path"]), i["line"], e(i["title"]),
                                                                       i["evidence_class"], e(i["reason"])) for i in unv])
    out += section("Areas inspected", ["- " + e(a) for a in doc["inspected"]["areas"]])
    return "\n".join(out + ["Verdict", doc["verdict"] + " (advisory only)", ""])


def render(run, fmt):
    doc = build(run)
    if fmt == "text":
        return _text(doc)
    return json.dumps(_sarif(doc) if fmt == "sarif" else doc, sort_keys=True, indent=2, ensure_ascii=True) + "\n"


def _usage():
    return target.Refuse("usage", "usage: report.py ingest --run DIR [--lane defects|standards|spec|sota] | verdicts --run DIR | render --run DIR [--format text|json|sarif]")


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
        lane = tail[1] if sub == "ingest" and len(tail) == 2 and tail[0] == "--lane" and tail[1] in LANE_AXIS else None
        if tail and lane is None:
            raise _usage()
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
        out = ingest(run, text, lane or "defects") if sub == "ingest" else verdicts(run, text)
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
