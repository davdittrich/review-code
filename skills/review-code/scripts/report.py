"""Turn finder and verifier JSON lines into one schema v1 review document.

Wire form (g1x.10): every line is a JSON array of [key, value] pairs, nested objects likewise, so no line holds a brace
(Claude Code's Bash guard denies a brace then a quote in a here-document). A line with a repeated key, a non-pair
element or a non-string key, and a raw JSON object at any depth (the retired object form), is invalid.
A line that is not JSON because a string holds raw double quotes is read again by _repair (a quote ends a string only
where the line goes on with `,` or `]`); the ingest and verdicts stdout count such lines as `repaired`. A line that parses
but is no pair array is never repaired.
Gate (review-code-19l): every rejected line is kept as `<reason> <line>` (a lane line's reason is `invalid-json`, or `failed-validation:<check>` with check one of missing-field,
bad-type, spec-line-invalid, sota-invalid, copied-from-spec, path-not-in-tree, line-out-of-range, unreadable, quote-not-at-line, rule-malformed, rule-source-unknown, rule-not-in-conventions) in <run>/rejected/<lane or batch>.txt (batch-K,
serial), replaced by a re-ingest of the same lane or batch. `status` shows rejected, rejected_total and no_verdict;
`render` shows a Warning section and does not go above Needs discussion while either is nonzero.

Stdlib only, Python 3.9 grammar. The LLM supplies claims and verdicts; this script derives id, evidence class,
score, origin, severity cap and order. Subcommands (stdin is JSON lines; stdout is one JSON status line, except
`render`, which prints the report):

    report.py ingest   --run DIR [--lane defects|standards|spec|sota] [--shard N]
                                   candidate lines + one ["inspected", [path, ...]] pair line (SOTA: item numbers); Defects lane only: one ["summary", text] line (text up to SUMMARY_MAX characters, first wins, others invalid);
                                   additive per lane (key <lane>, or <lane>@N for shard N, a plan.json shard id:
                                   cites are checked in <run>/shards/N/tree, conventions in
                                   <run>/shards/N/conventions; else bad-shard)
    report.py verdicts --run DIR [--batch K]   id, verdict, evidence (a list of path, line, quote), reason pair-array lines.
                                   --batch K (a verify_calls batch of candidates.json) stores the reply in replies/verify/K.txt,
                                   applies only ids of that batch (others count as foreign) and needs a cite to refute;
                                   verdicts.json is rebuilt from all stored batches. Without it one reply replaces them all.
    report.py render   --run DIR [--format text|json|sarif]
    report.py status   --run DIR   read-only: ids, spec_ids, verify_calls, lane_state, missing_lanes, missing_batches, confirmed,
                                   rejected {lane or batch: count}, rejected_total, no_verdict (ids of a stored batch without a verdict)
    report.py discard  --run DIR   removes candidates.json, verdicts.json, handoff/, replies/ and repro/ (a re-plan starts clean)

Document (json output; no run path, no timestamps, so it is byte-reproducible):
  schema_version, target{kind, head_sha, merge_base, label, not_a_change}, trust (own|external), verdict,
  findings[] (class reproduced|traced), unverifiable[] (class inferred|speculative), dropped{refuted,
  do_not_flag, invalid}, inspected{files, ledger_counts, areas}, and, only when non-empty, hidden_content[{file, line|null,
  kind, length or codepoints, name}] (notes, not findings; capped at 40) with hidden_content_omitted.
  Item keys (a finding also carries impact, the verifier's reason): id, path, line, end_line, severity (blocking|required|suggestion|noted), category, title, trigger,
  quote, evidence[{path, line, quote}], evidence_class, score, verified, origin (introduced|pre-existing|
  existing), fix, reason, axis (defects|standards|spec), source_lane (the --lane of the ingest, never model output).
A Standards `convention` item also carries rule and rule_source. skipped[] lists what plan.json left out.
Document also: summary[{lane, text}] (model-written change summary, sorted by shard, untrusted), lean_lines (sum of end_line - line + 1 over verified lean findings, an estimate), lanes{lane: complete|incomplete|not-ingested} (incomplete = the reply had no `inspected` line; not-ingested = plan.json called it, no reply was ingested), merged (candidates
folded into a same-axis neighbour), skipped[]. Items carry also_found_by[]. Ingest stdout: status, ids (queue order,
cut at plan.json caps.max_verifier_calls; spec_ids holds the Spec candidates for review-code:spec-verifier and is
cut by the same budget; SOTA candidates are in neither), dropped, handoff, capped, lanes, merged, repaired, rejected
[{lane, index (1-based row of that lane's rejected file), check}], never the wire line; cut ids render as
unverifiable with reason verifier-cap.

Spec lane (CTX-03): a line cites inputs/spec.md by `spec_line` only, kept when that line lies strictly inside an
UNTRUSTED block. A spec-lane title, trigger, fix or declined why, or a spec verdict reason, that holds SPEC_RUN
consecutive words of spec text counts invalid (verdicts: the line is dropped), so no output carries such a run; a
paraphrase passes, and the main thread relays the raw replies (untrusted-input.md Known limits). A `["declined", [[["spec_line", n], ["why", text]], ...]]` trailer (spec lane
only) is validated the same way. Document keys: declined[{spec_line, why}]; items carry spec_line, shard (shard runs)
and sources (SOTA). A SOTA line names a plan.json sota_inventory `item` number (its path and line come from there;
any path or line in the line is overwritten), quote = the item name, with 1 to 5 https `sources`, and its inspected
line lists item numbers; it gets no verifier call, class inferred, reason web-sourced, and never counts toward the verdict. With
plan.json mode serial a confirmed verdict is only inferred (self-scored). Document keys mode (parallel|serial), tier,
lost_guarantees[], shards{reviewed[ids], files{id: n}, skipped[]}. A handoff for a file listed by a plan.json pack
(path inside <run>/inputs/packs) carries that pack's `## Do not flag` section in a fenced do-not-flag: block.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import contextlib
import hashlib
import json
import os
import re
import secrets
import shutil
try:
    import fcntl
except ImportError:  # shortcut: no fcntl (Windows) means no run lock, so two concurrent ingests can lose a lane; add msvcrt.locking if Windows is ever supported
    fcntl = None
from urllib.parse import quote, urlsplit

import ledger
import sanitize
import target

MAX_HIDDEN = 40
MAX_AREAS = 20  # areas inspected shown in a PR/MR body and in SARIF; the text report lists all
SCHEMA_VERSION = 1
VERIFY_BATCH = 5  # handoffs per verifier call; shortcut: fixed, raise it if verifier replies stay reliable past 5
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
LANE_CATEGORIES = {"defects": CATEGORIES, "standards": ("convention", "comment", "smell", "lean", "altitude"),
                   "sota": ("sota",), "spec": ("spec-missing", "spec-partial", "spec-wrong", "scope-creep")}
CEILING = {"convention": "required", "comment": "required", "smell": "suggestion", "lean": "suggestion", "altitude": "suggestion",
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


def _cite(tree, path, line, end, quote):
    """(the cited lines, None) when path, range and quote all hold in tree/, else (None, the failing check)."""
    real = _real(tree, path)
    if real is None:
        return None, "path-not-in-tree"
    if not (_int(line) and _int(end) and 1 <= line <= end):
        return None, "line-out-of-range"
    try:
        with open(real, encoding="utf-8", errors="replace") as fh:
            rows = fh.read().split("\n")
    except OSError:
        return None, "unreadable"
    if rows[-1] == "":
        rows.pop()
    if end > len(rows):
        return None, "line-out-of-range"
    got = rows[line - 1:end]
    ok = isinstance(quote, str) and _ws(quote) and _ws(quote) in _ws(" ".join(got))
    return (got, None) if ok else (None, "quote-not-at-line")


def _cited(tree, path, line, end, quote):
    """The cited lines when path, range and quote all hold in tree/, else None."""
    return _cite(tree, path, line, end, quote)[0]


def _wire(v):
    """A lane line is a JSON array of [key, value] pairs (the Bash guard denies a brace then a quote in a here-document).
    Every list whose items are all 2-item lists with a string first becomes a dict, recursively; other lists stay lists.
    ValueError on a repeated key."""
    if not isinstance(v, list):
        return v
    if v and all(isinstance(p, list) and len(p) == 2 and isinstance(p[0], str) for p in v):
        d = {p[0]: _wire(p[1]) for p in v}
        if len(d) != len(v):
            raise ValueError("repeated key")
        return d
    return [_wire(x) for x in v]


def _no_object(pairs):
    raise ValueError("raw JSON object")  # the wire form has none at any depth, so a repeated key cannot hide in one


def _loads(s):
    return json.loads(s, object_pairs_hook=_no_object)


REPAIR_MAX_LEN = 8000  # characters of a line the quote repair will work on
REPAIR_STEPS = 250  # string ends and values it will try on one line (the 15 real lines need at most 47)
INGEST_STEPS = 20000  # ... and on all lines of one ingest call or one stored verdicts batch
SUMMARY_MAX = 600  # characters of a change summary: one to three sentences
LITERAL = ("title", "trigger", "quote", "fix", "reason", "summary")  # the only keys whose string may hold a raw quote (e7e)
KEYS = frozenset(CANDIDATE_REQUIRED + CANDIDATE_OPTIONAL + VERDICT_KEYS + CITE_KEYS + (
    "rule", "rule_source", "sources", "spec_line", "item", "inspected", "declined", "why", "summary"))
_TOKEN = re.compile(r'\[\s*"(%s)"\s*,' % "|".join(sorted(KEYS)))
_NUM = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_LIT = {"true": True, "false": False, "null": None}


def _unrepairable(s):
    return json.JSONDecodeError("cannot repair", s, 0)


def _skip(s, i):
    while s[i:i + 1] in (" ", "\t"):
        i += 1
    return i


def _charge(s, budget):
    budget[0] -= 1
    if budget[0] < 0:
        raise _unrepairable(s)


def _alts(s, i, budget, key=None):
    """Every reading of a JSON value at s[i:] as (value, end), shortest string first. `key` is the string before this
    value in a [key, value] pair: a value of a LITERAL key may end at any quote whose next non-space character is `,` or
    `]` (first those ending the pair), every other quote inside it is literal; any other string is read strictly. No
    object is read."""
    i = _skip(s, i)
    _charge(s, budget)
    c = s[i:i + 1]
    if c == "[":
        j = _skip(s, i + 1)
        if s[j:j + 1] == "]":
            yield [], j + 1
        else:
            yield from _items(s, j, [], budget)
    elif c == '"':
        if key not in LITERAL:
            try:
                yield json.decoder.scanstring(s, i + 1)
            except ValueError:
                pass
            return
        if i not in budget[1]:  # once per offset: backtracking asks again
            ends = [(q, s[_skip(s, q + 1):_skip(s, q + 1) + 1]) for q in range(i + 1, len(s))
                    if s[q] == '"' and s[q - 1] != "\\"]
            budget[1][i] = [q for q, c in sorted((e for e in ends if e[1] in (",", "]", "")), key=lambda e: e[1] != "]")]
        for q in budget[1][i]:
            _charge(s, budget)  # each end tried costs a decode of the string
            try:
                yield json.loads('"%s"' % re.sub(r'(\\.)|"', lambda m: m.group(1) or '\\"', s[i + 1:q])), q + 1
            except ValueError:
                pass
    elif _NUM.match(s, i):
        m = _NUM.match(s, i)
        yield json.loads(m.group()), m.end()
    else:
        for w, v in _LIT.items():
            if s.startswith(w, i):
                yield v, i + len(w)


def _items(s, i, acc, budget):
    for v, j in _alts(s, i, budget, acc[0] if len(acc) == 1 and isinstance(acc[0], str) else None):
        j = _skip(s, j)
        if s[j:j + 1] == "]":
            yield acc + [v], j + 1
        elif s[j:j + 1] == ",":
            yield from _items(s, j + 1, acc + [v], budget)


def _tokens(v, n):
    """Counts in n, at any depth, the lists of v whose first item is a known key."""
    if isinstance(v, list):
        if len(v) > 1 and isinstance(v[0], str) and v[0] in KEYS:
            n[v[0]] = n.get(v[0], 0) + 1
        for x in v:
            _tokens(x, n)
    return n


def _repair(s, total, line=None):
    """_loads for a line whose strings hold raw double quotes (seen live: models write quote and fix text unescaped).
    The first reading (see _alts) that is a pair array, with no repeated key, wins. Only string ends are chosen: no key,
    element or object appears that the line did not hold, and the caller still validates the result."""
    if len(s) > REPAIR_MAX_LEN:
        raise _unrepairable(s)
    line = [REPAIR_STEPS] if line is None else line  # steps left for this line, both candidates of _parse together
    cap = max(0, min(line[0], total[0]))
    budget = [cap, {}]  # steps left, string ends by offset
    try:
        return _repair_one(s, budget)
    finally:
        spent = cap - max(budget[0], 0)
        total[0] -= spent
        line[0] -= spent


def _repair_one(s, budget):
    for v, j in _alts(s, 0, budget):
        if _skip(s, j) == len(s):
            raw, got = {}, _tokens(v, {})
            for k in _TOKEN.findall(s):
                raw[k] = raw.get(k, 0) + 1
            if any(n > got.get(k, 0) for k, n in raw.items()):  # a `["k",` token swallowed into a string (e7e)
                continue
            try:
                if isinstance(_wire(v), dict):
                    return v
            except ValueError:
                pass
    raise _unrepairable(s)


def _hostile(tree, path):
    """True when the cited file holds a known-key wire token: a quote copied from it could carry keys into a repaired
    line, so such a line keeps no fix, end_line or evidence (dc5)."""
    real = _real(tree, path)
    if real is None:
        return False
    with open(real, encoding="utf-8", errors="replace") as fh:
        return _TOKEN.search(fh.read()) is not None


def _parse(ln, load):
    """(read, dict or None) of a wire line under `load`; the second candidate is the line with its outer bracket restored.
    read is False only when no candidate is JSON syntax: a line that reads but is no pair array (repeated key, raw object,
    wrong shape) is final and never repaired."""
    for cand in (ln, "[" + ln + "]"):
        try:
            o = load(cand)
            o = _wire(o) if isinstance(o, list) else None
            return True, o if isinstance(o, dict) else None
        except json.JSONDecodeError:
            pass
        except (ValueError, RecursionError):
            return True, None
    return False, None


def _objects(text, total=None):
    """(line, dict or None, repaired) of every wire line of the stream (`[[`, `["` or, for the retired object form, `{`):
    the dict is None when it is not a valid pair array. Prose is skipped. A `["` line whose outer bracket is missing is read
    as if it had one. The strict parse comes first; only a line it could not parse at all goes to _repair, which draws
    on `total` (a one-item list, INGEST_STEPS by default) shared by every line of the caller's call."""
    total = [INGEST_STEPS] if total is None else total
    for ln in text.split("\n"):
        if re.match(r'\s*(\{|\[\s*[\["])', ln):
            # shortcut: only the dropped-outer-bracket slip and raw quotes are repaired (seen live); other damage stays invalid, upgrade when a lane shows a new slip
            parsed, o = _parse(ln, _loads)
            if not parsed:
                line = [REPAIR_STEPS]
                _, o = _parse(ln, lambda c: _repair(c, total, line))
            yield ln, o, not parsed and o is not None


def _rule(o, run):
    """((rule, rule_source), None) of a convention line when the rule text occurs in the named conventions/ file, else
    (None, the failing check)."""
    rule, src = o.get("rule"), o.get("rule_source")
    # a stated rule: at least 3 words, matched on word boundaries, so a letter or a word fragment proves nothing
    if not (isinstance(rule, str) and len(rule.split()) >= 3 and len(rule) <= 500):
        return None, "rule-malformed"
    if not isinstance(src, str):
        return None, "rule-source-unknown"
    rel = os.path.normpath(src) if src and "\0" not in src else ""
    if not rel.startswith("conventions" + os.sep):
        return None, "rule-source-unknown"
    real = _real(os.path.realpath(os.path.join(run, "conventions")), rel[len("conventions" + os.sep):])
    if real is None:
        return None, "rule-source-unknown"
    with open(real, encoding="utf-8", errors="replace") as fh:
        if re.search(r"(?<!\w)%s(?!\w)" % re.escape(_ws(rule)), _ws(fh.read())):
            return (rule, rel), None
    return None, "rule-not-in-conventions"


def _spec_lines(run):
    """Line numbers (1-based) that lie strictly inside an UNTRUSTED block of inputs/spec.md; empty when there is no file.
    Only the numbers are returned: spec text never leaves this function (CTX-03)."""
    try:
        with open(os.path.join(run, "inputs", "spec.md"), encoding="utf-8", errors="replace") as fh:
            rows = fh.read().split("\n")
    except OSError:
        return set()
    ok, nonce = set(), None
    for n, row in enumerate(rows, 1):
        if nonce is None:
            m = re.fullmatch(r"<<<UNTRUSTED-([0-9a-f]+)", row)
            nonce = m.group(1) if m else None
        elif row == nonce + ">>>":
            nonce = None
        else:
            ok.add(n)
    return ok


SPEC_RUN = 6  # consecutive words; a shorter shared run is ordinary phrasing, not a copy


def _words(s):
    return re.findall(r"\w+", s.casefold())


def _spec_copy(run):
    """Predicate: True when a string holds SPEC_RUN consecutive words (case and punctuation ignored) of the text inside
    the UNTRUSTED blocks of inputs/spec.md. The text stays inside this closure (CTX-03)."""
    inside = _spec_lines(run)
    try:
        with open(os.path.join(run, "inputs", "spec.md"), encoding="utf-8", errors="replace") as fh:
            w = [x for n, row in enumerate(fh.read().split("\n"), 1) if n in inside for x in _words(row)]
    except OSError:
        w = []
    runs = {tuple(w[i:i + SPEC_RUN]) for i in range(len(w) - SPEC_RUN + 1)}

    def copied(s):
        t = _words(s)
        return any(tuple(t[i:i + SPEC_RUN]) in runs for i in range(len(t) - SPEC_RUN + 1))
    return copied


def _inventory(run):
    """{item: (path, line, name)} of plan.json sota_inventory: the lane names an item, never a path (WR-04)."""
    inv = _plan(run).get("sota_inventory")
    return {i["item"]: (i["path"], i["line"], i["name"]) for i in (inv if isinstance(inv, list) else [])
            if isinstance(i, dict) and _int(i.get("item")) and isinstance(i.get("path"), str) and _int(i.get("line"))
            and isinstance(i.get("name"), str)}


def _sources(x):
    """x when it is 1 to 5 https URLs without whitespace (at most 300 characters each), else None."""
    if not (isinstance(x, list) and 1 <= len(x) <= 5):
        return None
    for u in x:
        if not (isinstance(u, str) and len(u) <= 300 and u == "".join(u.split())):
            return None
        try:
            sp = urlsplit(u)
        except ValueError:
            return None
        if sp.scheme != "https" or not sp.netloc:
            return None
    return x


def _candidate(o, tree, lane, base, ctx=None):
    """(candidate, None), or (None, the failing check)."""
    for k in CANDIDATE_REQUIRED:
        if k not in o:
            return None, "missing-field"
    c = {k: o[k] for k in CANDIDATE_REQUIRED}
    c.update({k: o.get(k, d) for k, d in zip(CANDIDATE_OPTIONAL, (o["line"], ""))})
    end, fix = c["end_line"], c["fix"]
    if (not isinstance(c["path"], str) or c["severity"] not in SEVERITIES or c["category"] not in LANE_CATEGORIES[lane]
            or not isinstance(fix, str) or not all(isinstance(c[k], str) and c[k] for k in ("title", "trigger", "quote"))):
        return None, "bad-type"
    if lane == "spec" and not (_int(o.get("spec_line")) and o["spec_line"] in ctx):
        return None, "spec-line-invalid"
    c["path"] = os.path.normpath(c["path"])
    if lane == "sota":
        src = _sources(o.get("sources"))
        if src is None or _ws(c["quote"]) != _ws(ctx[o["item"]][2]) or end != c["line"]:
            return None, "sota-invalid"
        c["sources"] = src
    c["rows"], why = _cite(tree, c["path"], c["line"], end, c["quote"])
    if c["rows"] is None:
        return None, why
    if c["category"] == "convention":
        r, why = _rule(o, base)
        if r is None:
            return None, why
        c["rule"], c["rule_source"] = r
    if _int(o.get("spec_line")) and "spec_line" in LANE_OPTIONAL[lane]:
        c["spec_line"] = o["spec_line"]
    c["axis"], c["source_lane"] = LANE_AXIS[lane], lane
    key = (c["path"], c["category"], _ws(c["quote"]))  # Defects keeps the Phase 3 input; other axes salt it
    c["id"] = "F-" + hashlib.sha256("\0".join(key if c["axis"] == "defects" else (c["axis"],) + key).encode(
        "utf-8", "surrogatepass")).hexdigest()[:10]
    return c, None


def _refs(run, shard=None):
    """{symbol: (defining file, count of lines naming it outside that file across the whole pinned tree)} from the ledger; {} when unreadable."""
    try:
        led = target.load_json(run if shard is None else os.path.join(run, "shards", str(shard)), "ledger.json")
        return {s["name"]: (s.get("file"), s["refs"]) for s in led["symbols"] if isinstance(s.get("refs"), int)}
    except (target.Refuse, KeyError, TypeError):
        return {}


def _handoff(c, run, nonce, packs=(), refs=None):
    loc = "%s:%d%s" % (c["path"], c["line"], "-%d" % c["end_line"] if c["end_line"] != c["line"] else "")
    out = "# Verify finding %s\n\nid: %s\nclaim:\n%s\nlocation: %s\ntrigger:\n%s\ntree: %s\n" % (
        c["id"], c["id"], ledger.block(c["title"], nonce), json.dumps(loc),
        ledger.block(c["trigger"], nonce), json.dumps(os.path.join(run, "tree") if "shard" not in c else
                                                         os.path.join(run, "shards", str(c["shard"]), "tree")))
    out += "".join("do-not-flag:\n%s\n" % ledger.block(text, nonce) for files, text in packs if c["path"] in files)
    if c["axis"] == "defects":
        return out
    out += "check: %s\n" % ("rule" if c["category"] == "convention" else
                            "spec" if c["axis"] == "spec" else "property")
    if c["axis"] == "spec":
        out += "spec: %s line %d\n" % (json.dumps(os.path.join(run, "inputs", "spec.md")), c["spec_line"])
    if c["category"] == "lean":  # an unused-code claim needs the whole-repo count, not only what tree/ holds
        m = ledger._DEF.match(c["quote"])
        n, have = m.group(1) if m else "", (refs or {}).get(c.get("shard"), {})
        out += ('references: %s %d outside %s (git grep -w at head_sha)\n' % (json.dumps(n), have[n][1], c["path"])
                if n in have and have[n][0] == c["path"] else "references: unknown\n")  # one defining file per name
    return out + ("rule:\n%s\n" % ledger.block(c["rule"], nonce) if c["category"] == "convention" else "")


def _added_lines(run):
    """(whole, added): the target is no change (every finding `existing`), and path -> lines the diff adds."""
    tj = target.load_json(run, "target.json")
    added = {}
    with open(os.path.join(run, "change.diff"), encoding="utf-8", errors="replace") as fh:
        for _, p, tag, _, n, _, _ in sanitize.walk(fh.read()):
            if tag == "+":
                added.setdefault(p, set()).add(n)
    return tj["kind"] in WHOLE, added


def _doomed(run, cands):
    """Ids render always drops (VERI-05): a suggestion or noted candidate wholly on unchanged lines of a change. A verdict can
    only worsen a severity, and the drop does not read the verdict, so no verifier call can change the outcome."""
    whole, added = _added_lines(run)
    return {c["id"] for c in cands if not whole and c["severity"] in SEVERITIES[2:]
            and not any(n in added.get(c["path"], ()) for n in range(c["line"], c["end_line"] + 1))}


def _queue(cands, skip=()):
    """Candidates a verifier may judge, in verification order; SOTA is advisory: no verifier call; `skip` ids are doomed."""
    return sorted((c for c in cands if c["category"] != "sota" and c["id"] not in skip),
                  key=lambda c: (SEVERITIES.index(c["severity"]), AXES.index(c["axis"]), c["path"], c["line"], c["id"]))


def _planned(run):
    """Lane keys of plan.json calls, spelled as ingest keys them (<lane>, or <lane>@N for shard N)."""
    calls = _plan(run).get("calls")
    return [c["lane"] if c.get("shard") is None else "%s@%d" % (c["lane"], c["shard"])
            for c in (calls if isinstance(calls, list) else []) if isinstance(c, dict) and isinstance(c.get("lane"), str)
            and (c.get("shard") is None or _int(c["shard"]))]


def _rejected(run):
    """({lane: rejected line count}, {batch number or "serial": count}) of <run>/rejected, nonzero entries only. A row
    ends at its newline: a line may hold other Unicode line breaks."""
    d, lanes, batches = os.path.join(run, "rejected"), {}, {}
    for n in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        with open(os.path.join(d, n), encoding="utf-8", newline="") as fh:
            k = fh.read().count("\n")
        if n.endswith(".txt") and k:
            is_batch = n.startswith("batch-") or n == "serial.txt"
            (batches if is_batch else lanes)[n[:-4].replace("batch-", "", 1) if is_batch else n[:-4]] = k
    return lanes, batches


def _counts(run, name=None, lost=0, repaired=None, drop=lambda n: False):
    """<run>/counters.json: {"attempts": {name: n}, "lost": {name: n}, "repaired": {name: n}} per lane key and per
    verify batch ("batch-K", "serial"). The script keeps them so the 2-try cap never rests on the model's memory (bgq):
    attempts and lost add up over every ingest of `name` and only discard resets them; repaired is the latest reading
    of each name, replaced by `repaired` after `drop` removes the names it covers. Returns the stored counters."""
    path = os.path.join(run, "counters.json")
    try:
        with open(path, encoding="utf-8") as fh:
            c = json.load(fh)
    except FileNotFoundError:
        c = {}
    except (OSError, ValueError):
        c = None
    if c is None or not isinstance(c, dict) or any(k in c and not isinstance(c[k], dict) for k in ("attempts", "lost", "repaired")):
        raise target.Refuse("bad-counters", "counters.json is unreadable: discard the run (it would reset the retry counts)")
    c = {k: c.get(k, {}) for k in ("attempts", "lost", "repaired")}
    if name is not None:
        c["attempts"][name], c["lost"][name] = c["attempts"].get(name, 0) + 1, c["lost"].get(name, 0) + lost
        c["repaired"] = dict({n: k for n, k in c["repaired"].items() if not drop(n)}, **(repaired or {}))
        _put(path, json.dumps(c, sort_keys=True))
    return c


def _no_verdict(run, cand, ver):
    """Sorted ids of a stored verify batch (or of a serial reply: every queued id) that have no verdict line."""
    store = _replies(run)
    have = set(os.listdir(store)) if os.path.isdir(store) else set()
    cut = set(cand.get("capped_ids", []))
    queue = [c["id"] for c in _queue(cand["candidates"], cand.get("doomed_ids", ())) if c["id"] not in cut]
    asked = set(queue) if "serial.txt" in have else {i for c in cand.get("verify_calls", []) if isinstance(c, dict)
                                                       and "%d.txt" % c["batch"] in have for i in c["ids"]}
    return sorted(asked - set(ver))


def status(run):
    """Read-only: what the verify step needs and what is still missing. No lock: files are replaced whole."""
    cand = target.load_json(run, "candidates.json")
    cut = set(cand.get("capped_ids", []))
    queue = [c for c in _queue(cand["candidates"], cand.get("doomed_ids", ())) if c["id"] not in cut]
    calls = [c for c in cand.get("verify_calls", []) if isinstance(c, dict)]
    store = _replies(run)
    have = set(os.listdir(store)) if os.path.isdir(store) else set()
    try:
        ver = target.load_json(run, "verdicts.json")
    except target.Refuse:
        ver = {}
    state = cand.get("lane_state", {})
    rej, rejb = _rejected(run)
    cnt = _counts(run)
    repaired = {n: k for n, k in cnt["repaired"].items() if k}
    gone = {n: k for n, k in cnt["lost"].items() if k}
    return {"status": "ok", "lost": gone, "lost_total": sum(gone.values()), "rejected": rej, "rejected_batches": rejb, "rejected_total": sum(rej.values()) + sum(rejb.values()),
            "attempts": {n: {"attempts": k, "lost": cnt["lost"].get(n, 0)} for n, k in sorted(cnt["attempts"].items())},
            "repaired": repaired, "repaired_total": sum(repaired.values()),
            "no_verdict": _no_verdict(run, cand, ver), "ids": [c["id"] for c in queue if c["axis"] != "spec"],
            "spec_ids": [c["id"] for c in queue if c["axis"] == "spec"], "verify_calls": calls, "lane_state": state,
            "missing_lanes": sorted(set(_planned(run)) - set(state)),
            "missing_batches": [] if "serial.txt" in have else [c["batch"] for c in calls if "%d.txt" % c["batch"] not in have],
            "confirmed": sorted(i for i, v in ver.items() if v.get("verdict") == "confirmed" and v.get("cites"))}


def _batches(queue, hand, prefix="review-code"):
    """verify_calls: queue ids grouped by (agent, shard), at most VERIFY_BATCH per call. Each call pays a fixed start
    (about 9k tokens in a lean setup, 29k measured here), so a shard's ids share calls; within one, ids of a path stay
    together in queue order, and a refuted verdict still needs a cite in its own path (review-code-8dt). The prompt
    lists absolute handoff paths."""
    groups = {}
    for c in queue:
        agent = "%s:%sverifier" % (prefix, "spec-" if c["axis"] == "spec" else "")
        groups.setdefault((agent, c.get("shard")), []).append(c)
    out = []
    for (agent, _), cs in groups.items():
        ids = [c["id"] for c in sorted(cs, key=lambda c: c["path"])]  # stable: queue order within a path
        for i in range(0, len(ids), VERIFY_BATCH):
            chunk = ids[i:i + VERIFY_BATCH]
            out.append({"batch": len(out) + 1, "agent": agent, "ids": chunk, "prompt": "Verify. Handoffs:\n" + "\n".join(
                os.path.join(os.path.abspath(hand), x + ".md") for x in chunk)})
    return out


def _dedupe(cands):
    """(survivors, merged count). Per axis, candidates on one path and category whose ranges lie within 2 lines merge;
    the more severe survives, then the longer trigger, then the smaller id. A wrong merge hides a finding and a missed
    one shows two, so categories and axes never merge. One id (shards whose trees share a referenced file) always
    merges first, wherever its lines are: the more severe, then the longer trigger, then the lower line and shard."""
    best, merged = {}, 0
    for c in sorted(cands, key=lambda c: (SEVERITIES.index(c["severity"]), -len(c["trigger"]), c["line"],
                                          c.get("shard") or 0)):
        k = (c["axis"], c["path"], c["category"], c["id"])  # the id is a 40-bit hash: never merge across its inputs
        merged += k in best
        best.setdefault(k, c)
    out = []
    ordered = sorted(best.values(), key=lambda c: (c["axis"], c["path"], c["category"], c["line"], c["end_line"], c["id"]))
    i = 0
    while i < len(ordered):
        j, end = i + 1, ordered[i]["end_line"]
        while (j < len(ordered) and ordered[j]["axis"] == ordered[i]["axis"] and ordered[j]["path"] == ordered[i]["path"]
               and ordered[j]["category"] == ordered[i]["category"] and ordered[j]["line"] <= end + 2):
            end, j = max(end, ordered[j]["end_line"]), j + 1
        group = ordered[i:j]
        win = min(group, key=lambda c: (SEVERITIES.index(c["severity"]), -len(c["trigger"]), c["id"]))
        out.append(dict(win, also_found_by=sorted({c["source_lane"] for c in group} - {win["source_lane"]})))
        merged, i = merged + len(group) - 1, j
    return sorted(out, key=lambda c: c["id"]), merged


@contextlib.contextmanager
def _locked(run):
    """Serialise every writer of a run (ingest, verdicts, discard, build): a lane ingest is a read-modify-write."""
    if fcntl is None or not os.path.isdir(run):  # a missing run refuses in the caller's own load_json
        yield
        return
    with open(os.path.join(run, ".lock"), "a") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield  # closing fh drops the lock


def _put(path, text):
    """write_private through a temp file and os.replace, so a reader never sees a half file."""
    target.write_atomic(path, text.encode("utf-8"))


def _plan(run):
    """plan.json as a dict, {} when the run has none."""
    try:
        return target.load_json(run, "plan.json")
    except target.Refuse as r:
        if r.reason == "no-plan":
            return {}
        raise


def _tree(run, shard=None):
    return os.path.realpath(os.path.join(run, "tree") if shard is None else os.path.join(run, "shards", str(shard), "tree"))


def _packs(run):
    """[(files, Do not flag text)] of plan.json packs whose file lies inside <run>/inputs/packs and has that section."""
    packs, root, out = _plan(run).get("packs"), os.path.realpath(os.path.join(run, "inputs", "packs")), []
    for pk in packs if isinstance(packs, list) else []:
        if not (isinstance(pk, dict) and isinstance(pk.get("path"), str) and isinstance(pk.get("files"), list)):
            continue
        real = os.path.realpath(os.path.join(run, pk["path"]))  # an absolute path replaces run
        if not real.startswith(root + os.sep) or not os.path.isfile(real):
            continue
        with open(real, encoding="utf-8", errors="replace") as fh:
            m = re.search(r"^## Do not flag[ \t]*\n(.*?)(?=^## |\Z)", fh.read(), re.MULTILINE | re.DOTALL)
        if m and m.group(1).strip():
            out.append(({os.path.normpath(f) for f in pk["files"] if isinstance(f, str)}, m.group(1).strip()[:4000]))  # shortcut: 4000 chars, raise if packs outgrow it
    return out


def _cap_of(run):
    """caps.max_verifier_calls of plan.json, None when there is no plan.json (no cap)."""
    plan = _plan(run)
    if not plan:
        return None
    caps = plan.get("caps")
    n = caps.get("max_verifier_calls") if isinstance(caps, dict) else None
    if not _int(n) or n < 1:
        raise target.Refuse("bad-plan", "plan.json caps.max_verifier_calls is not a positive integer")
    return n


def _declined(o, ctx, copied):
    """(valid entries, invalid count) of a {"declined": [{spec_line, why}]} trailer."""
    if not isinstance(o["declined"], list):
        return [], 1
    good = [{"spec_line": d["spec_line"], "why": _ws(d["why"])} for d in o["declined"] if isinstance(d, dict)
            and _int(d.get("spec_line")) and d["spec_line"] in ctx and isinstance(d.get("why"), str)
            and _ws(d["why"]) and len(d["why"]) <= 300 and not copied(d["why"])]
    return good, len(o["declined"]) - len(good)


def _read_lane(text, lane, tree, run, shard=None):
    kept, areas, invalid, ignored, complete, declined, rejected, repaired = {}, [], 0, 0, False, [], [], 0
    summary = None
    ctx = _spec_lines(run) if lane == "spec" else _inventory(run) if lane == "sota" else None
    copied = _spec_copy(run) if lane == "spec" else None
    base = run if shard is None else os.path.join(run, "shards", str(shard))  # conventions/ lives beside the tree
    for ln, o, fixed in _objects(text):
        repaired += fixed
        why = "invalid-json" if o is None else None  # else the check that failed
        if o is not None and "path" not in o and "summary" in o and lane == "defects":  # the one summary per lane: the first valid wins
            sm = o["summary"]
            if summary is None and isinstance(sm, str) and _ws(sm) and len(sm) <= SUMMARY_MAX:
                summary = _ws(sm)
            else:
                invalid += 1  # counted, but not a rejected line: a summary holds no finding, so it never flips the verdict
            continue
        if o is not None and "path" not in o and ("inspected" in o or ("declined" in o and lane == "spec")):
            if "inspected" in o:
                complete = True
                got = o["inspected"] if isinstance(o["inspected"], list) else []
                if lane == "sota":  # item numbers, mapped back to their inventory paths
                    got = [ctx[a][0] for a in got if _int(a) and a in ctx]
                areas += [a for a in got if _real(tree, a)]
            if "declined" in o and lane == "spec":
                good, bad = _declined(o, ctx, copied)
                declined, invalid = declined + good, invalid + bad
            continue
        if lane == "sota" and o is not None:  # the inventory, never the lane, picks the path and line
            o = dict(o, path=ctx[o["item"]][0], line=ctx[o["item"]][1]) if _int(o.get("item")) and o["item"] in ctx else None
        if fixed and o is not None and _hostile(tree, o.get("path")):  # dc5
            o = {k: v for k, v in o.items() if k not in CANDIDATE_OPTIONAL}
        c, check = _candidate(o, tree, lane, base, ctx) if o is not None else (None, "sota-invalid")
        if c is not None and copied and any(copied(c[k]) for k in ("title", "trigger", "fix")):  # CTX-03
            c, check = None, "copied-from-spec"
        if c is None or c["id"] in kept:
            invalid += 1
            if c is None:  # a duplicate loses nothing
                rejected.append((why or check, ln))
        elif LINT_IGNORE.search("\n".join(c.pop("rows"))):  # VERI-05: the author ignored it on purpose
            ignored += 1
        else:
            kept[c["id"]] = dict(c, shard=shard) if shard is not None else c
    return {"candidates": sorted(kept.values(), key=lambda c: c["id"]), "inspected": areas, "complete": complete,
            **({"summary": summary} if summary else {}), "declined": declined, "dropped": {"invalid": invalid, "do_not_flag": ignored}, "rejected": rejected,
            "repaired": repaired}


def _reject(run, name, rows):
    """<run>/rejected/<name>.txt holds one `reason line` row per rejected wire line, replaced whole by a re-ingest of
    the same lane or batch and removed when none is left (review-code-19l)."""
    d, path = os.path.join(run, "rejected"), os.path.join(run, "rejected", name + ".txt")
    if rows:
        os.makedirs(d, mode=0o700, exist_ok=True)
        _put(path, "".join("%s %s\n" % (why if why == "invalid-json" else "failed-validation:" + why, ln) for why, ln in rows))
    elif os.path.exists(path):
        os.remove(path)


def ingest(run, text, lane="defects", shard=None):
    with _locked(run):
        return _ingest(run, text, lane, shard)


def _ingest(run, text, lane, shard):
    target.load_json(run, "target.json")
    _counts(run)  # refuse a corrupt counters.json before anything is replaced
    if os.path.exists(_replies(run)) or os.path.exists(os.path.join(run, "verdicts.json")):  # it would drop the verdicts
        raise target.Refuse("late-reply", "verification already started: discard the run to re-ingest a lane")
    if shard is not None:
        sh = _plan(run).get("shards")
        if not any(isinstance(x, dict) and x.get("id") == shard and _int(x["id"]) for x in (sh if isinstance(sh, list) else [])):
            raise target.Refuse("bad-shard", "--shard is not a shard id of plan.json")
    tree = _tree(run, shard)
    nonce = secrets.token_hex(8)
    try:
        lanes = target.load_json(run, "candidates.json").get("lanes")
    except target.Refuse:
        lanes = None
    lanes = lanes if isinstance(lanes, dict) else {}
    cap = _cap_of(run)
    key = lane if shard is None else "%s@%d" % (lane, shard)
    lanes[key] = _read_lane(text, lane, tree, run, shard)  # a re-ingest replaces only that lane
    rejected, repaired = lanes[key].pop("rejected"), lanes[key].pop("repaired")
    cands, merged = _dedupe([c for n in sorted(lanes) for c in lanes[n]["candidates"]])
    doomed = _doomed(run, cands)
    queue = _queue(cands, doomed)
    queue, cut = (queue, []) if cap is None else (queue[:cap], [c["id"] for c in queue[cap:]])
    dropped = {k: sum(v["dropped"][k] for v in lanes.values()) for k in ("invalid", "do_not_flag")}
    for stale in ("handoff", "repro", "replies"):
        shutil.rmtree(os.path.join(run, stale), ignore_errors=True)
    for n in _rejected(run)[1]:  # their replies are gone (4gj)
        os.remove(os.path.join(run, "rejected", ("serial" if n == "serial" else "batch-" + n) + ".txt"))
    if os.path.exists(os.path.join(run, "verdicts.json")):
        os.remove(os.path.join(run, "verdicts.json"))
    hand = os.path.join(run, "handoff")
    os.makedirs(hand, mode=0o700, exist_ok=True)
    packs = _packs(run)
    refs = {sh: _refs(run, sh) for sh in {c.get("shard") for c in queue}}
    for c in queue:
        target.write_private(os.path.join(hand, c["id"] + ".md"), _handoff(c, run, nonce, packs, refs))
    _reject(run, key, rejected)
    _counts(run, key, len(rejected), {key: repaired}, lambda n: n == key or n == "serial" or n.startswith("batch-"))
    state = {n: "complete" if lanes[n].get("complete") is True else "incomplete" for n in sorted(lanes)}
    _put(os.path.join(run, "candidates.json"), json.dumps(
        {"candidates": cands, "dropped": dropped, "inspected": [a for n in sorted(lanes) for a in lanes[n]["inspected"]],
         "lanes": lanes, "lane_state": state, "merged": merged, "capped_ids": sorted(cut), "doomed_ids": sorted(doomed),
         "verify_calls": _batches(queue, hand, _plan(run).get("agent_prefix", "review-code"))}, sort_keys=True, indent=2))
    return {"status": "ok", "ids": [c["id"] for c in queue if c["axis"] != "spec"],
            "spec_ids": [c["id"] for c in queue if c["axis"] == "spec"], "dropped": dropped, "handoff": hand,
            "capped": len(cut), "lanes": state, "merged": merged, "repaired": repaired,
            "rejected": [{"lane": key, "index": i, "check": why} for i, (why, _) in enumerate(rejected, 1)]}


def verdicts(run, text, batch=None):
    with _locked(run):
        return _verdicts(run, text, batch)


def _replies(run):
    return os.path.join(run, "replies", "verify")


def _one(text, allowed, cand_ids, trees, spec, copied, got, paths, total):
    """Fold one stored reply into got (first verdict per id wins); returns (count of ids outside `allowed`, rejected rows,
    repaired line count).
    allowed None is a serial reply (any queued id); a set is a batch, whose refuted needs a cite in the candidate's own
    path, one that no other refuted id of the reply uses (review-code-8dt)."""
    foreign, mine, bad, fixed = 0, [], [], 0
    for ln, o, fix in _objects(text, total):
        fixed += fix
        o = o and {k: o.get(k) for k in VERDICT_KEYS}
        if o is None or not isinstance(o.get("id"), str) or o.get("verdict") not in VERDICTS:
            bad.append(("invalid-json" if o is None else "bad-type", ln))
            continue
        if allowed is not None and o["id"] not in allowed:
            foreign += 1
            continue
        if o["id"] not in cand_ids or o["id"] in got:
            continue
        ev = o.get("evidence")
        cites = [e for e in (ev if isinstance(ev, list) else []) if isinstance(e, dict)
                 and _cited(trees[o["id"]], e.get("path"), e.get("line"), e.get("line"), e.get("quote")) is not None]
        reason = o.get("reason")
        if fix and any(_hostile(trees[o["id"]], p) for p in {paths[o["id"]]} | {e["path"] for e in cites}):
            cites = []  # a repaired line may not carry evidence out of a file that holds wire tokens (dc5)
        if o["id"] in spec and isinstance(reason, str) and copied(reason):  # CTX-03: the line is dropped
            continue
        if allowed is not None and o["verdict"] == "refuted":
            cites = [e for e in cites if os.path.normpath(e["path"]) == paths[o["id"]]]
        refuted = o["verdict"] == "refuted"  # a repaired line never refutes: text read from a quote must not drop a finding
        kind = "unverifiable" if refuted and (fix or (allowed is not None and not cites)) else o["verdict"]
        got[o["id"]] = {"verdict": kind, "reason": reason if isinstance(reason, str) else "",
                        "cites": [{k: e[k] for k in CITE_KEYS} for e in cites]}
        mine.append(o["id"])
    key = lambda e: (os.path.normpath(e["path"]), e["line"], _ws(e["quote"]))  # noqa: E731
    used = [key(e) for i in mine if allowed is not None and got[i]["verdict"] == "refuted" for e in got[i]["cites"]]
    for i in mine:
        if got[i]["verdict"] == "refuted" and any(used.count(key(e)) > 1 for e in got[i]["cites"]):
            got[i]["verdict"] = "unverifiable"
    return foreign, bad, fixed


def _verdicts(run, text, batch):
    cand = target.load_json(run, "candidates.json")
    _counts(run)
    trees = {c["id"]: _tree(run, c.get("shard")) for c in cand["candidates"]}  # cites are checked in the cited shard's tree
    ids = {c["id"] for c in cand["candidates"] if c["category"] != "sota"} - set(cand.get("capped_ids", []))  # a cut one never reached a verifier
    paths = {c["id"]: c["path"] for c in cand["candidates"]}
    spec = {c["id"] for c in cand["candidates"] if c["axis"] == "spec"}
    copied = _spec_copy(run) if spec else None
    calls = {c["batch"]: set(c["ids"]) for c in cand.get("verify_calls", []) if isinstance(c, dict)}
    if batch is None and calls and _plan(run).get("mode") == "parallel":  # it would replace every stored batch (review-code-3k5)
        raise target.Refuse("batch-required", "plan mode is parallel: pass --batch for each verify call")
    if batch is not None and batch not in calls:
        raise target.Refuse("bad-batch", "--batch is not a batch of candidates.json verify_calls")
    store = _replies(run)
    if batch is None:  # the single-call path: this reply replaces every stored one, as it always replaced verdicts.json
        shutil.rmtree(os.path.dirname(store), ignore_errors=True)
        for n in _rejected(run)[1]:
            if n != "serial":
                os.remove(os.path.join(run, "rejected", "batch-" + n + ".txt"))
    os.makedirs(store, mode=0o700, exist_ok=True)
    _put(os.path.join(store, "serial.txt" if batch is None else "%d.txt" % batch), text)
    got, foreign, repaired, fixes, lost = {}, 0, 0, {}, 0
    names = [n for n in os.listdir(store) if re.fullmatch(r"serial\.txt|[1-9][0-9]*\.txt", n)]
    for n in sorted(names, key=lambda n: (n != "serial.txt", int(n[:-4]) if n[0].isdigit() else 0)):
        k = int(n[:-4]) if n[0].isdigit() else None
        if k is None or k in calls:
            with open(os.path.join(store, n), encoding="utf-8") as fh:
                f, bad, fixed = _one(fh.read(), None if k is None else calls[k], ids, trees, spec, copied, got, paths,
                                     [INGEST_STEPS])  # each stored batch has its own repair budget (one hostile batch starves no other)
            _reject(run, "serial" if k is None else "batch-%d" % k, bad)
            fixes["serial" if k is None else "batch-%d" % k] = fixed
            lost += len(bad) if k == batch else 0
            foreign, repaired = foreign + (f if k == batch else 0), repaired + (fixed if k == batch else 0)
    _put(os.path.join(run, "verdicts.json"), json.dumps(got, sort_keys=True, indent=2))
    _counts(run, "serial" if batch is None else "batch-%d" % batch, lost, fixes, lambda n: n == "serial" or n.startswith("batch-"))
    conf = sorted(i for i, v in got.items() if v["verdict"] == "confirmed" and v["cites"])
    ref = sorted(i for i, v in got.items() if v["verdict"] == "refuted")
    out = {"status": "ok", "confirmed": conf, "refuted": ref, "unverifiable": sorted(ids - set(conf) - set(ref)),
           "repaired": repaired}
    return out if batch is None else dict(out, foreign=foreign)


def discard(run):
    """Drop every lane result, so a serial re-plan never renders lanes from the parallel attempt (SKILL step 7)."""
    target.load_json(run, "target.json")
    with _locked(run):
        return _discard(run)


def _discard(run):
    for d in ("handoff", "repro", "replies", "rejected"):
        shutil.rmtree(os.path.join(run, d), ignore_errors=True)
    for f in ("candidates.json", "verdicts.json", "counters.json"):
        if os.path.exists(os.path.join(run, f)):
            os.remove(os.path.join(run, f))
    return {"status": "ok"}


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
    with _locked(run):
        return _build(run)


def _build(run):
    tj, led, cand = (target.load_json(run, n) for n in ("target.json", "ledger.json", "candidates.json"))
    try:
        ver = target.load_json(run, "verdicts.json")
    except target.Refuse as r:
        if r.reason != "no-verdicts":
            raise
        ver = {}
    whole, added = _added_lines(run)
    capped = set(cand.get("capped_ids", []))
    plan = _plan(run)
    serial = plan.get("mode") == "serial"
    items, dropped = [], dict(cand["dropped"], refuted=0)
    for c in cand["candidates"]:
        v = {} if c["category"] == "sota" else ver.get(c["id"], {})
        cites, kind = v.get("cites", []), v.get("verdict")
        if kind == "refuted":
            dropped["refuted"] += 1
            continue
        if c["category"] == "sota":
            cls, reason = "inferred", "web-sourced"
        elif kind == "confirmed" and cites and serial:  # a self-score is not independent verification (VERI-03)
            cls, reason = "inferred", "self-scored"
        elif kind == "confirmed" and cites:
            cls, reason = ("reproduced" if _repro(run, c["id"]) == "reproduced" else "traced"), v["reason"]
        else:
            cls = "inferred" if cites else "speculative"
            reason = v["reason"] if kind == "unverifiable" else "no-valid-cite" if kind == "confirmed" else (
                "verifier-cap" if c["id"] in capped else "no-verdict")
        origin = "existing" if whole else "introduced" if any(
            n in added.get(c["path"], ()) for n in range(c["line"], c["end_line"] + 1)) else "pre-existing"
        sev = SEVERITIES[max(SEVERITIES.index(c["severity"]), SEVERITIES.index(_cap(cls, c["category"])),
                             SEVERITIES.index(CEILING.get(c["category"], SEVERITIES[0])))]
        # VERI-05; Blocking and Required stay (VERI-06); a cut id never got a verdict, so it shows as verifier-cap
        if origin == "pre-existing" and sev in SEVERITIES[2:] and c["id"] not in capped:
            dropped["do_not_flag"] += 1
            continue
        items.append(dict(c, severity=sev, evidence=cites, evidence_class=cls, score=SCORE[cls],
                          verified=cls in ("reproduced", "traced"), origin=origin, reason=reason))
    items.sort(key=lambda i: (AXES.index(i["axis"]), SEVERITIES.index(i["severity"]), i["path"], i["line"], i["id"]))
    findings = [i for i in items if i["verified"]]
    for i in findings:
        i["impact"] = i["reason"]  # the verifier names the production impact in its reason (verifier.md)
    unverifiable = [i for i in items if not i["verified"]]
    rank = {i["severity"] for i in findings}
    # Defects: any unverifiable item needs discussion. Standards and Spec: only an item at required does, so
    # unverifiable smell, lean and comment suggestions never flip the verdict; SOTA never counts (its ceiling is suggestion).
    label = ("Needs changes" if "blocking" in rank else
             "Needs discussion" if any(i["severity"] == "required" for i in items if i["axis"] != "defects")
             or "required" in rank or any(i["axis"] == "defects" for i in unverifiable) else "No blocking findings")
    lanes = dict(dict.fromkeys(_planned(run), "not-ingested"), **cand.get("lane_state", {}))
    if label == "No blocking findings" and any(v != "complete" for n, v in lanes.items() if n.split("@")[0] in ("defects", "spec")):
        label = "Needs discussion"  # a defects or spec lane that was never ingested or never finished proves nothing (review-code-0c4)
    (rej, rejb), lost = _rejected(run), _no_verdict(run, cand, ver)
    cnt = _counts(run)
    repaired, gone = ({n: k for n, k in cnt[w].items() if k} for w in ("repaired", "lost"))
    if label == "No blocking findings" and (rej or rejb or lost or gone):
        label = "Needs discussion"  # a lane line or verdict that was dropped may have held a finding or a refutation (review-code-19l)
    areas = sorted(set(cand["inspected"]))  # checked against the lane's own tree at ingest
    skipped = plan.get("skipped")
    skipped = [s for s in skipped if isinstance(s, dict)] if isinstance(skipped, list) else []
    if label == "No blocking findings" and any(x.get("what") == "defects" for x in skipped):
        label = "Needs discussion"  # a tier that never ran Defects proves nothing about bugs (review-code-2og)
    done = sorted({int(n.split("@")[1]) for n in cand.get("lane_state", {}) if "@" in n})
    sh = plan.get("shards") if isinstance(plan.get("shards"), list) else []
    count = {x["id"]: len(x["files"]) if isinstance(x.get("files"), list) else x.get("files") for x in sh
             if isinstance(x, dict) and _int(x.get("id"))}
    shards = {"reviewed": done, "files": {str(n): count[n] for n in done if _int(count.get(n))},
              "skipped": [x for x in skipped if str(x.get("what", "")).startswith("shard")]}
    spec = led.get("spec")
    if (not isinstance(spec, dict) or spec.get("status") == "none") and not any(s.get("what") == "spec" for s in skipped):
        skipped.append({"what": "spec", "why": "no spec"})
    declined = sorted((d for v in cand.get("lanes", {}).values() for d in v.get("declined", [])),
                      key=lambda d: (d["spec_line"], d["why"]))
    summary = [{"lane": n, "text": v["summary"]} for n, v in sorted(
        cand.get("lanes", {}).items(), key=lambda kv: (kv[0].partition("@")[0], int(kv[0].partition("@")[2] or 0))) if v.get("summary")]
    doc = {"schema_version": SCHEMA_VERSION, "summary": summary,
            "target": {"kind": tj["kind"], "head_sha": tj["head_sha"], "merge_base": tj["merge_base"],
                       "label": led["label"], "not_a_change": whole},
            "trust": "own" if ledger._stored(led) == "own" else "external",
            "verdict": label, "findings": findings, "unverifiable": unverifiable, "dropped": dropped, "skipped": skipped, "declined": declined,
            "shards": shards, "mode": "serial" if serial else "parallel", "tier": plan["tier"] if isinstance(plan.get("tier"), str) else None,
            "lost_guarantees": [x for x in plan["lost_guarantees"] if isinstance(x, str)]
            if isinstance(plan.get("lost_guarantees"), list) else [],
            "lanes": lanes, "merged": cand.get("merged", 0),
            "lean_lines": sum(i["end_line"] - i["line"] + 1 for i in findings if i["category"] == "lean"),  # an estimate
            "inspected": {"files": tj["included"], "areas": areas,
                          "ledger_counts": {k: len(led[k]) for k in ledger.CATEGORIES}}}
    hc = [{"file": h.get("path", h["file"]), "line": h["line"], "kind": h["kind"], "length": h["length"], "name": False}
          for h in led["hidden_text"]]
    hc += [{"file": h["file"], "line": h.get("line"), "kind": "hidden-chars", "codepoints": h["codepoints"],
            "name": h.get("name") is True} for h in led["hidden_chars"]]
    if rej or rejb or lost:
        doc.update(rejected=rej, rejected_batches=rejb, no_verdict=lost)
    if repaired:
        doc.update(repaired=repaired)
    if gone:  # lines dropped by any ingest, kept until discard: a retry that omits them must not clear the warning (dc5)
        doc.update(lost=gone)
    if hc:  # OUT-02: notes outside the evidence gate; positions and sizes, never the payload
        doc.update(hidden_content=hc[:MAX_HIDDEN], hidden_content_omitted=max(0, len(hc) - MAX_HIDDEN))
    return doc


_LEVEL = {"blocking": "error", "required": "warning", "suggestion": "note", "noted": "note"}


def _sarif(doc):
    fs, areas, hc = doc["findings"], doc["inspected"]["areas"], doc.get("hidden_content", [])
    return {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0", "runs": [{
        "tool": {"driver": {"name": "review-code",
                            "rules": [{"id": "review-code/" + c} for c in sorted({f["category"] for f in fs})]
                 + ([{"id": "hidden-content"}] if hc else [])}},
        "results": [{"ruleId": "review-code/" + f["category"], "level": _LEVEL[f["severity"]],
                     "message": {"text": f["title"]},
                     "locations": [{"physicalLocation": {"artifactLocation": {"uri": quote(f["path"])},
                                                         "region": {"startLine": f["line"], "endLine": f["end_line"]}}}],
                     "partialFingerprints": {"reviewCodeId/v1": f["id"]},
                     "properties": {"evidenceClass": f["evidence_class"], "severity": f["severity"],
                                    "origin": f["origin"], "score": f["score"], "axis": f["axis"]}} for f in fs]
        + [{"ruleId": "hidden-content", "level": "note", "message": {"text": "hidden content in a change: " + h["kind"]},
            "locations": [{"physicalLocation": dict({"artifactLocation": {"uri": quote(h["file"])}},
                                                    **({"region": {"startLine": h["line"]}} if h["line"] is not None else {}))}],
            "properties": {k: h[k] for k in ("length", "codepoints", "name") if k in h and h[k] is not None}} for h in hc],
        "properties": {"inspectedAreas": areas[:MAX_AREAS], "inspectedAreasOmitted": max(0, len(areas) - MAX_AREAS)}}]}


def _text(doc):
    e = sanitize.escape_line  # repo and LLM text reaches the terminal only through here; one line each, so no CR overwrite

    def item(i):
        out = ["- [%s] %s:%d  %s (%s, score %d, %s)" % (i["id"], e(i["path"]), i["line"], e(i["title"]),
                                                      i["evidence_class"], i["score"], i["origin"]),
               "    trigger: " + e(i["trigger"])]
        out += ["    spec: inputs/spec.md:%d" % i["spec_line"]] if "spec_line" in i else []
        out += ["    evidence: %s:%d  %s" % (e(c["path"]), c["line"], e(c["quote"])) for c in i["evidence"]]
        out += ["    impact: " + e(i["impact"])] if i["impact"] else []
        return out + (["    fix: " + e(i["fix"])] if i["fix"] else [])

    t, d, fs = doc["target"], doc["dropped"], doc["findings"]
    out = ["Summary"] + ["change summary (model text, unverified): " + e(x["text"]) for x in doc["summary"]]
    out += ["verdict: " + doc["verdict"], "target: %s %s (merge base %s), ledger %s" % (
        e(t["kind"]), e(t["head_sha"]), e(t["merge_base"]), e(t["label"]))]
    if t["not_a_change"]:
        out.append("Target is not a change: whole-file findings")
    out += ["mode: " + doc["mode"]] + (["tier: " + e(doc["tier"])] if doc["tier"] else [])
    out += ["lost guarantee: " + e(g) for g in doc["lost_guarantees"]]
    out += ["findings: %d, unverifiable: %d" % (len(fs), sum(1 for i in doc["unverifiable"] if i["category"] != "sota")),
            "dropped: refuted %d, do-not-flag %d, invalid %d" % (d["refuted"], d["do_not_flag"], d["invalid"]),
            "trust: " + doc["trust"]]
    for ax in AXES:
        mine = [i for i in fs if i["axis"] == ax]
        out.append("%s: %d finding%s, worst %s" % (ax, len(mine), "" if len(mine) == 1 else "s", min(
            (i["severity"] for i in mine), key=SEVERITIES.index, default="none")))
    sh = doc["shards"]
    out += ["shards reviewed: " + ", ".join("%d (%s files)" % (n, sh["files"].get(str(n), "?")) for n in sh["reviewed"])] if sh["reviewed"] else []
    out += ["shard skipped: %s (%s)%s" % (e(x.get("what")), e(x.get("why")), " %s files" % x["files"] if _int(x.get("files")) else "")
            for x in sh["skipped"]]
    out += ["lane %s incomplete: reply had no inspected line" % e(n) for n, v in sorted(doc["lanes"].items())
            if v == "incomplete"]
    out += ["lane %s not-ingested: its reply was never ingested" % e(n) for n, v in sorted(doc["lanes"].items())
            if v == "not-ingested"]
    out += ["Lean: -%d lines possible (estimate)" % doc["lean_lines"]] if doc["lean_lines"] else []
    if doc.get("rejected") or doc.get("rejected_batches") or doc.get("no_verdict") or doc.get("repaired") or doc.get("lost"):
        out += ["", "Warning"]
        out += ["rejected lines: %d (%s)" % (sum(doc["rejected"].values()), ", ".join(
            "%s %d" % (e(n), k) for n, k in sorted(doc["rejected"].items())))] if doc.get("rejected") else []
        out += ["rejected verdict lines: %d (%s)" % (sum(doc["rejected_batches"].values()), ", ".join(
            "batch %s %d" % (e(n), k) for n, k in sorted(doc["rejected_batches"].items())))] if doc.get("rejected_batches") else []
        out += ["repaired lines: %d (%s)" % (sum(doc["repaired"].values()), ", ".join(
            "%s %d" % (e(n), k) for n, k in sorted(doc["repaired"].items())))] if doc.get("repaired") else []
        out += ["lost lines: %d (%s)" % (sum(doc["lost"].values()), ", ".join(
            "%s %d" % (e(n), k) for n, k in sorted(doc["lost"].items())))] if doc.get("lost") else []
        out += ["no verdict: %d (%s)" % (len(doc["no_verdict"]), ", ".join(doc["no_verdict"]))] if doc.get("no_verdict") else []
    out.append("")

    def section(head, rows):
        return [head] + (rows or ["none"]) + [""]

    for ax in AXES:
        out.append(ax.capitalize())
        sota = [i for i in doc["unverifiable"] if i["category"] == "sota"] if ax == "standards" else []
        unv = [i for i in doc["unverifiable"] if i["axis"] == ax and i not in sota]
        if (ax != "defects" or any(x.get("what") == "defects" for x in doc["skipped"])) and not unv and not sota and not any(i["axis"] == ax for i in fs) and not (ax == "spec" and doc["declined"]):
            why = [s.get("why") for s in doc["skipped"] if s.get("what") == ax]
            out += ["%s: skipped (%s)" % (ax.capitalize(), e(why[0])) if why else "none", ""]
            continue
        for head, pick in (("Blocking", ("blocking",)), ("Required", ("required",)),
                           ("Suggestions", ("suggestion", "noted"))):
            out += section(head, [x for i in fs if i["axis"] == ax and i["severity"] in pick for x in item(i)])
        out += section("Unverifiable", ["- [%s] %s:%d  %s (%s): %s" % (i["id"], e(i["path"]), i["line"], e(i["title"]),
                                                                       i["evidence_class"], e(i["reason"]))
                                        + (" [inputs/spec.md:%d]" % i["spec_line"] if "spec_line" in i else "") for i in unv])
        if sota:
            out += section("SOTA (advisory)", [x for i in sota for x in (
                ["- [%s] %s:%d  %s (%s: %s)" % (i["id"], e(i["path"]), i["line"], e(i["title"]), i["evidence_class"],
                                                 e(i["reason"])), "    trigger: " + e(i["trigger"])]
                + ["    source: " + e(u) for u in i["sources"]])])
        if ax == "spec" and doc["declined"]:
            out += section("Declined", ["- inputs/spec.md:%d: %s" % (d["spec_line"], e(d["why"])) for d in doc["declined"]])
    if doc.get("hidden_content"):
        out += section("Hidden content (notes)", ["- %s%s  %s%s%s%s" % (
            e(h["file"]), ":%d" % h["line"] if h["line"] is not None else "", h["kind"],
            ", length %d" % h["length"] if "length" in h else "",
            ", codepoints " + " ".join(str(c) for c in h["codepoints"]) if "codepoints" in h else "",
            ", file name" if h["name"] else "") for h in doc["hidden_content"]]
            + (["(+%d more)" % doc["hidden_content_omitted"]] if doc["hidden_content_omitted"] else []))
    out += section("Areas inspected", ["- " + e(a) for a in doc["inspected"]["areas"]])
    return "\n".join(out + ["Verdict", doc["verdict"] + " (advisory only)", ""])


def render(run, fmt):
    doc = build(run)
    if fmt == "text":
        return _text(doc)
    return json.dumps(_sarif(doc) if fmt == "sarif" else doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def _usage():
    return target.Refuse("usage", "usage: report.py ingest --run DIR [--lane defects|standards|spec|sota] [--shard N] | verdicts --run DIR [--batch K] | render --run DIR [--format text|json|sarif] | discard --run DIR | status --run DIR")


def main(argv):
    try:
        sub, rest = (argv[0], argv[1:]) if argv else ("", [])
        if sub not in ("ingest", "verdicts", "render", "discard", "status") or rest[:1] != ["--run"] or len(rest) < 2:
            raise _usage()
        run, tail = rest[1], rest[2:]
        if sub == "render":
            if tail not in ([], ["--format", "text"], ["--format", "json"], ["--format", "sarif"]):
                raise _usage()
            sys.stdout.write(render(run, tail[1] if tail else "text"))
            return 0
        if sub in ("discard", "status"):
            if tail:
                raise _usage()
            print(json.dumps((discard if sub == "discard" else status)(run), sort_keys=True))
            return 0
        opts = {}
        while len(tail) >= 2 and tail[0] in (("--lane", "--shard") if sub == "ingest" else ("--batch",)) and tail[0] not in opts:
            opts[tail[0]], tail = tail[1], tail[2:]
        lane = opts.get("--lane", "defects")
        if tail or lane not in LANE_AXIS:
            raise _usage()
        if "--shard" in opts and not re.fullmatch(r"[1-9][0-9]{0,8}", opts["--shard"]):
            raise target.Refuse("bad-shard", "--shard is not a positive integer")
        if "--batch" in opts and not re.fullmatch(r"[1-9][0-9]{0,8}", opts["--batch"]):
            raise target.Refuse("bad-batch", "--batch is not a positive integer")
        text = sys.stdin.buffer.read().decode("utf-8", "replace")
        out = ingest(run, text, lane, int(opts["--shard"]) if "--shard" in opts else None) if sub == "ingest" else verdicts(run, text, int(opts["--batch"]) if "--batch" in opts else None)
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
