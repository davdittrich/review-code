"""Turn finder and verifier JSON lines into one schema v1 review document.

Wire form: every line is a JSON array of [key, value] pairs, nested objects likewise, so no line holds a brace (Claude
Code's Bash guard denies a brace then a quote in a here-document). A line with a repeated key, a non-pair element, a
non-string key or a raw JSON object at any depth (the retired object form) is invalid. A line that is not JSON because a
string holds raw double quotes, or a backslash that starts no JSON escape (a regular expression, a Windows path), is read
again by _repair; the ingest and verdicts stdout count such lines as `repaired`. A line that parses but is no pair array is
never repaired.

Gate: every rejected line is kept as `<reason> <line>` in <run>/rejected/<lane or batch>.txt (batch-K, serial), replaced by a
re-ingest of the same lane or batch. A lane line's reason is `invalid-json[:bad-escape]`, `retired-object-form`, or
`failed-validation:<check>` (see REASONS). `status` shows rejected, rejected_total and no_verdict; `render` shows a Warning
section and does not go above Needs discussion while either is nonzero.

Ingest and verdicts stdout carry `dispositions`, one row {index, id|null, disposition, reason} per wire line of that call
(prose is skipped): accepted, rejected or downgraded, with a key of REASONS (the closed reason -> retry hint table) or
null. Cumulative figures stay cumulative: `dropped` sums every stored lane (`lane_dropped` is the ingested lane's own
count) and verdicts.json holds every stored batch. In `verdicts` stdout, confirmed, refuted and unverifiable are
cumulative over the stored batches (`cumulative` names them); `batch_ids` names the ids this call covers (every queued id for
a serial reply); `no_verdict` lists queued ids with no stored verdict. `rejected` [{batch, index, check}] lists this call's
rejected lines, never the wire line.

Stdlib only, Python 3.9 grammar. The LLM supplies claims and verdicts; this script derives id, evidence class, score,
origin, severity cap and order. Subcommands (stdin is JSON lines; stdout is one JSON status line, except `render`, which
prints the report):

    report.py ingest   --run DIR [--lane defects|standards|spec|sota] [--shard N] [--agent ID]
                                   candidate lines + one ["inspected", [path, ...]] pair line (SOTA: item numbers); Defects lane only: one ["summary", text] line (text up to SUMMARY_MAX characters, first wins, others invalid);
                                   additive per lane (key <lane>, or <lane>@N for shard N, a plan.json shard id:
                                   conventions are checked in <run>/shards/N/conventions; else bad-shard). Every
                                   cite of ingest and verdicts is checked in the pinned tree (target.json pinned_tree)
    report.py verdicts --run DIR [--batch K] [--agent ID]   id, verdict, evidence (a list of path, line, quote), reason pair-array lines.
                                   --batch K (a verify_calls batch of candidates.json) stores the reply in replies/verify/K.txt,
                                   applies only ids of that batch (others count as foreign) and needs a cite to refute;
                                   verdicts.json is rebuilt from all stored batches. Without it one reply replaces them all.
    --agent ID (ingest and verdicts; the agentId of the Agent launch result) reads the reply from the capture hook
                                   (capture.py) instead of stdin: handback capture before stop capture. Refusals: no-capture,
                                   capture-incomplete (only a stop capture, and it lacks that line), agent-mismatch (the stored
                                   agent type is not the plan's for the lane or batch, or a verdicts reply names none of the
                                   batch's ids), agent-reused (the id served another call of this run), empty-capture (no
                                   inspected line for ingest, no wire line for verdicts). An ingest with findings but no
                                   `inspected` line for an already complete lane is refused no-inspected-line and still counts
                                   as a try. A successful call consumes the capture; verdicts needs --batch.
    report.py render   --run DIR [--format text|json|sarif]
    report.py status   --run DIR   read-only: ids, spec_ids, verify_calls, lane_state, missing_lanes, missing_batches, confirmed,
                                   rejected {lane or batch: count}, rejected_total, no_verdict (ids of a stored batch without a verdict)
    report.py discard  --run DIR   removes candidates.json, verdicts.json, agents.json, handoff/, replies/ and repro/ (a re-plan starts clean;
                                   the captures belong to the repository and stay)

Document (json output; no run path, no timestamps, so it is byte-reproducible):
  schema_version, target{kind, head_sha, merge_base, label, not_a_change}, trust (own|external), verdict,
  findings[] (class reproduced|traced), unverifiable[] (class inferred|speculative), dropped{refuted,
  do_not_flag, invalid}, inspected{files, ledger_counts, areas}, and, only when non-empty, hidden_content[{file, line|null,
  kind, length or codepoints, name, signal (removed text spans only)}] (notes, not findings; capped at 40) with
  hidden_content_omitted, and hidden_routine[{file, count, kinds}]: the removed spans that carry no signal, counted per file,
  so hidden_content plus the counts always sum to the ledger's records; `added` (only when above 0) is how many of those
  the change adds. A span's `signal` is `assistant`, `encoded`, `url`, `markup` or `long`; its `provenance` (`added`: it lies on a
  line the change adds; `touched`: it lies in a file the change touches that has no diff) is separate and never lists it. adr_omitted (the ledger's capped.adr, only when above 0): ADR files the spec lane never saw.
  Item keys (a finding also carries impact, the verifier's reason): id, path, line, end_line, severity (blocking|required|suggestion|nit|noted), category, title, trigger,
  quote, evidence[{path, line, quote}], evidence_class, score, verified, origin (introduced|pre-existing|
  existing), fix, reason, axis (defects|standards|spec), source_lane (the --lane of the ingest, never model output).
Document also: not_run[] (closed words: a lane name with optional @shard, `shard N`, `repro`, `serial`; from plan skipped[] entries, lanes not complete, serial mode, and repro when trust is not own or a finding has no repro outcome while the ledger lists tests or config).
A Standards `convention` item also carries rule and rule_source. A `nit` item (only under --nit; plan.json nit) also carries basis (rule|norm); a norm item carries norm_for, norm_against, norm_glob and norm{for, against, total}, counted by the skill over tree/. The document carries nit (the nit count) only under --nit. skipped[] lists what plan.json left out.
Document also: summary[{lane, text}] (model-written change summary, sorted by shard, untrusted), lean_lines (sum of end_line - line + 1 over verified lean findings, an estimate), lanes{lane: complete|incomplete|not-ingested} (incomplete = the reply had no `inspected` line; not-ingested = plan.json called it, no reply was ingested), merged (candidates
folded into a same-axis neighbour), lost{lane: n} and recovered{lane: n}, each only when non-empty (a recovered line is a lost line whose finding a later ingest of the lane carried), skipped[]. Items carry also_found_by[]. Ingest stdout: status, ids (queue order,
cut at plan.json caps.max_verifier_calls; spec_ids holds the Spec candidates for review-code:spec-verifier and is
cut by the same budget; SOTA candidates are in neither), dropped, handoff, capped, lanes, merged, repaired, rejected
[{lane, index (1-based row of that lane's rejected file), check}], never the wire line; cut ids render as
unverifiable with reason verifier-cap.

Spec lane: a line cites inputs/spec.md by `spec_line` only, kept when that line lies strictly inside an
UNTRUSTED block. A spec-lane title, trigger, fix or declined why, or a spec verdict reason, that holds SPEC_RUN
consecutive words of spec text counts invalid (verdicts: the line is dropped), so no output carries such a run; a
paraphrase passes, and the main thread relays the raw replies (untrusted-input.md Known limits). A `["declined", [[["spec_line", n], ["why", text]], ...]]` trailer (spec lane
only) is validated the same way. Document keys: declined[{spec_line, why}]; items carry spec_line, shard (shard runs)
and sources (SOTA). A SOTA line names a plan.json sota_inventory `item` number (its path and line come from there;
any path or line in the line is overwritten), quote = the item name, with 1 to 5 https `sources`, and its inspected
line lists item numbers; it gets no verifier call, class inferred, reason web-sourced, and never counts toward the verdict. With
plan.json mode serial a confirmed verdict is only inferred (self-scored). Document keys mode (parallel|serial), tier,
lost_guarantees[], shards{reviewed[ids], files{id: n}, skipped[]}, basis{pinned_head (null in place), pinned_tree, difference (the
checkout difference size), lost_capabilities[] (plan.json; a code-navigation tool the plan step was not given)}. render adds
integrity{working_tree[] (warnings), index (bool, a warning), knowledge[] (informational), cause}, or null without a snapshot. A handoff for a file listed by a plan.json pack
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

import capture
import ledger
import nit
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
SEVERITIES = ("blocking", "required", "suggestion", "nit", "noted")  # rank order; nit only under --nit (plan.json nit)
CATEGORIES = ("security", "data-loss", "race", "accessibility", "doc-mismatch", "constructor-divergence",
              "bug", "test", "performance", "other")
BLOCKING = CATEGORIES[:6]
LANE_CATEGORIES = {"defects": CATEGORIES, "standards": ("convention", "comment", "smell", "lean", "altitude", "norm"),
                   "sota": ("sota",), "spec": ("spec-missing", "spec-partial", "spec-wrong", "scope-creep")}
CEILING = {"convention": "required", "comment": "required", "smell": "suggestion", "lean": "suggestion", "altitude": "suggestion",
           "sota": "suggestion", "spec-missing": "required", "spec-partial": "required", "spec-wrong": "required",
           "scope-creep": "required", "norm": "nit"}  # strongest severity a non-Defects category may reach
LANE_OPTIONAL = {"defects": (), "standards": ("rule", "rule_source", "norm_for", "norm_against", "norm_glob"), "sota": ("sources",), "spec": ("spec_line",)}
VERDICTS = ("confirmed", "refuted", "unverifiable")
SCORE = {"reproduced": 100, "traced": 75, "inferred": 50, "speculative": 25}
ID_RE = r"F-[0-9a-f]{10}"
ID_FULL = re.compile(ID_RE)
LINT_IGNORE = re.compile(r"noqa|type: ?ignore|pylint: ?disable|eslint-disable|@ts-ignore|@ts-expect-error|nolint|NOSONAR|"
                         r"rubocop: ?disable|#\[allow\(|@SuppressWarnings|nosec", re.IGNORECASE)
WHOLE = ("path", "repo")  # targets that are not a change: every finding is `existing`
# The one closed table of reasons: reason -> retry hint. It feeds the rejected files, the disposition rows of `ingest` and
# `verdicts`, and the capture reader's refusals. _disp and _refuse look a reason up here, so an emitted reason
# missing from the table is a KeyError (a test drives it).
REASONS = {
    "invalid-json": "resend the line as one JSON pair array, every string escaped",
    "invalid-json:bad-escape": "write each backslash in a string as a double backslash: a single one starts no JSON escape",
    "retired-object-form": "resend as pair arrays [[\"key\", value], ...], never as a { } object",
    "missing-field": "resend the line with every required field",
    "bad-type": "resend the line with each field of its documented type and an allowed value",
    "spec-line-invalid": "cite a spec_line that lies inside an UNTRUSTED block of the spec",
    "sota-invalid": "cite a checklist item, its name as the quote and 1 to 5 https sources",
    "declined-invalid:bad-entry": "send each declined entry as spec_line (inside an UNTRUSTED block) and a why",
    "declined-invalid:too-long": "keep a declined why to 300 characters",
    "declined-invalid:copied-from-spec": "paraphrase the declined why: it holds a run of words copied from the spec",
    "copied-from-spec": "paraphrase: the text holds a run of words copied from the spec",
    "path-not-in-tree": "cite a file that is in the reviewed content (the pinned tree)",
    "quote-from-checkout": "quote the reviewed content (the pinned tree; tree/ for a checkout-difference file): the checkout's copy of the file holds the quote, the reviewed version does not",
    "claims-missing": "the reviewed content has the file or symbol the finding calls missing: check it there, then drop or reword the finding",
    "line-out-of-range": "cite a line range inside the file",
    "unreadable": "cite a file that is readable text",
    "quote-not-at-line": "quote text that the file holds at the cited line",
    "rule-malformed": "state the rule in at least 3 words, at most 500 characters",
    "rule-source-unknown": "name a conventions/ file as rule_source",
    "rule-not-in-conventions": "quote the rule as the conventions file words it",
    "nit-not-enabled": "the run was not planned with --nit: drop every line with severity nit or category norm",
    "nit-only": "this run reports nits only (--nit --lean): send only lines with severity nit, for example category convention with a rule",
    "nit-basis": "a nit has severity nit and category convention (with rule and rule_source) or norm; a norm is never above nit, and only the Standards lane sends either",
    "norm-malformed": "send norm_for, norm_against and norm_glob as non-empty strings of at most 200 characters, for example \"= []\", \"= list()\", \"*.py\"; the idioms are at least 3 characters, neither holds the other, and the quote holds norm_against but not norm_for",
    "norm-quote-mismatch": "quote the deviating line itself: it must contain the exact text of norm_against",
    "norm-sample-small": "the two idioms occur on fewer than 20 lines of the tree/ files norm_glob matches: widen norm_glob (for example \"*.py\") or drop the finding",
    "norm-too-large": "the files norm_glob matches hold more text than the skill will count: narrow norm_glob to the files you counted",
    "norm-not-majority": "norm_for must occur on more lines than norm_against: if the repo writes norm_against more often there is no norm, so drop the finding",
    "repaired": "the line was read only after its raw double quotes were repaired: write each as \\\"",
    "do-not-flag": "the cited code carries a lint-ignore marker: the author ignored it on purpose",
    "duplicate": "the line repeats an earlier one; nothing is lost",
    "bad-summary": "send one summary line, text up to the length cap",
    "no-cite": "a refuted verdict needs a cite that holds in the reviewed content (any file): none did",
    "shared-cite": "one cite cannot refute two findings: give each refuted id its own cite",
    "other-batch": "the id belongs to another verify batch",
    "unknown-id": "the id is no candidate of this run",
    "empty-capture": "the capture holds no wire line of the expected kind",
    "capture-incomplete": "only the agent's closing message is captured: wait for its completion notification, or pass the reply on stdin",
    "no-capture": "no capture for this agent id: re-run without --agent and pass the reply on stdin",
    "agent-mismatch": "the capture is of another agent type than the plan's, or names none of the batch's ids",
}


def _disp(index, id, disposition, reason=None):
    """One disposition row: the `index`th wire line of a call, the finding id it names (or None), what became of it
    (accepted, rejected or downgraded) and why (a REASONS key, or None for a plain accept)."""
    if reason is not None and reason not in REASONS:
        raise KeyError("reason missing from REASONS: %r" % (reason,))
    return {"index": index, "id": id, "disposition": disposition, "reason": reason}


def _defects_skipped(skipped):
    """True when the plan left the Defects lane out: a tier that never ran it proves nothing about bugs."""
    return any(x.get("what") == "defects" for x in skipped)


def _did(x):
    """x when it has the shape of a finding id, else None: a row never echoes model text."""
    return x if isinstance(x, str) and ID_FULL.fullmatch(x) else None


def _syntax(ln):
    """The reason of a wire line that is no pair array at all: the retired object form, a lone backslash that starts no
    JSON escape (the repair doubles it where it can; this names it where it cannot), or plain invalid JSON."""
    if ln.lstrip().startswith("{"):
        return "retired-object-form"
    return "invalid-json:bad-escape" if any(not m.group(1) for m in _ESC.finditer(ln) if m.group().startswith("\\")) else "invalid-json"


def _refuse(reason, detail=None):
    return target.Refuse(reason, REASONS[reason] if detail is None else detail)


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


def _norm(path):
    """path normalised, or None when it can name no file inside a tree (no string, empty, absolute, NUL, leaves the root)."""
    if not isinstance(path, str) or not path or "\0" in path or os.path.isabs(path):
        return None
    p = os.path.normpath(path)
    return None if p in (".", "..") or p.startswith(".." + os.sep) else p


class _Pinned:
    """The pinned tree's regular files that one ingest, verdicts or post call names (gh#50): `files` from one `ls-tree`
    of exactly those paths, `rows` {path: lines, or None when unreadable} of the `cited` ones from one `cat-file --batch`,
    each blob read once by id. Rows hold the text tree/ held (ledger._scan: escaped, prose stripped), so a cite matches
    what the lanes were shown and post.py never renders a raw invisible character."""

    def __init__(self, run, paths, cited=()):
        tj = target.load_json(run, "target.json")
        self.top, self.files, self.rows, blob = tj["repo"], set(), {}, {}
        want = sorted({p for p in map(_norm, paths) if p})
        if not want:
            return
        # shortcut: the paths go on one argv (ls-tree reads no pathspec file); a reply naming ~100k paths would pass ARG_MAX
        out = target.git_bytes(["ls-tree", "-z", "-l", "--full-tree", tj["pinned_tree"], "--"] + want, self.top,
                               env={"GIT_LITERAL_PATHSPECS": "1"})
        for rec in out.split(b"\0"):
            meta, tab, name = rec.partition(b"\t")
            f = meta.split()
            if tab and len(f) == 4 and f[0] in (b"100644", b"100755"):  # a link, submodule or directory is no cited file
                p = name.decode("utf-8", "surrogateescape")
                self.files.add(p)
                blob[p] = f[2].decode()
        need = {p: blob[p] for p in {_norm(c) for c in cited} if p in blob}
        uniq = sorted(set(need.values()))
        text = {oid: None if data is None else ledger._text(data)
                for oid, (_, data) in zip(uniq, target.cat_batch([o.encode() for o in uniq], self.top, True, target.MAX_FILE))}
        for p, oid in need.items():
            t = text.get(oid)
            self.rows[p] = None if t is None else _lines(ledger._scan(p, t)[0])


def _lines(text):
    rows = text.split("\n")
    if rows[-1] == "":
        rows.pop()
    return rows


def _cite(pin, path, line, end, quote):
    """(the cited lines, None) when path, range and quote all hold in the pinned tree, else (None, the failing check)."""
    p = _norm(path)
    if p not in pin.files:
        return None, "path-not-in-tree"
    if not (_int(line) and _int(end) and 1 <= line <= end):
        return None, "line-out-of-range"
    rows = pin.rows.get(p)
    if rows is None:
        return None, "unreadable"
    if end > len(rows):
        return None, "line-out-of-range"
    got = rows[line - 1:end]
    ok = isinstance(quote, str) and _ws(quote) and _ws(quote) in _ws(" ".join(got))
    return (got, None) if ok else (None, "quote-not-at-line")


def _from_checkout(pin, path, quote):
    """True when the checkout's file holds `quote` and the pinned file does not: the quote is not of the reviewed content.
    A plain file read under the checkout root, no git process."""
    p, q = _norm(path), isinstance(quote, str) and _ws(quote)
    if not p or not q or q in _ws(" ".join(pin.rows.get(p) or ())):
        return False
    top = os.path.realpath(pin.top)
    up = os.path.realpath(os.path.dirname(os.path.join(top, p)))
    if up != top and not up.startswith(top + os.sep):  # a parent directory that leads out of the checkout
        return False
    status, data = target.read_working(os.path.join(top, p), target.MAX_FILE)
    t = ledger._text(data) if status == "ok" else None
    return t is not None and q in _ws(t)


def _relocate(pin, path, line, end, quote):
    """(line, end) of the one place nearest the cited range where the file holds `quote` across as many lines as were
    cited, else None: a tie, or no such place, keeps the refusal. The quote check itself stays as strict as _cite's."""
    rows = pin.rows.get(_norm(path))
    q = _ws(quote)
    if rows is None or not q or not (_int(line) and _int(end) and 1 <= line <= end):
        return None
    if q not in _ws(" ".join(rows)):  # an absent quote cannot move: skip the window scan, which runs under the run lock
        return None
    n = min(end - line + 1, len(rows))  # a cite past the end of the file is moved too
    hits = sorted((abs(s - (line - 1)), s) for s in range(len(rows) - n + 1) if q in _ws(" ".join(rows[s:s + n])))
    if not hits or hits[1:2] and hits[1][0] == hits[0][0]:
        return None
    lo, hi = hits[0][1], hits[0][1] + n  # the window holds the quote; trim it to the rows that do
    while hi - lo > 1 and q in _ws(" ".join(rows[lo + 1:hi])):
        lo += 1
    while hi - lo > 1 and q in _ws(" ".join(rows[lo:hi - 1])):
        hi -= 1
    return lo + 1, hi


def _cited(pin, path, line, end, quote):
    """The cited lines when path, range and quote all hold in the pinned tree, else None."""
    return _cite(pin, path, line, end, quote)[0]


_NAME = r"[`'\"]?([\w][\w./+-]*)[`'\"]?"
# shortcut: a fixed English word list; a claim worded otherwise is not caught and is verified as before
_GONE = r"(?:is |are |was )?(?:missing|absent|undefined|not defined|not found|nonexistent|non-existent|does not exist|doesn't exist)"
_MISSING = re.compile(r"%s\s+%s(?=\s*(?:$|[.,;:)!]|(?:from|in|at|on|here|there)\b))|(?:missing|absent|undefined|nonexistent|"
                      r"non-existent|no such)\s+(?:file|module|function|symbol|class|method|name|import|path)\s+%s(?=$|[\s.,;:)!])"
                      % (_NAME, _GONE, _NAME), re.I)


def _claimed(o):
    """Names a finding's title or trigger calls missing ("x.py is missing", "undefined name f")."""
    return {(a or b).rstrip(".") for k in ("title", "trigger") if isinstance(o.get(k), str) for a, b in _MISSING.findall(o[k])}


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
KEYS = frozenset(CANDIDATE_REQUIRED + CANDIDATE_OPTIONAL + VERDICT_KEYS + CITE_KEYS + tuple(k for v in LANE_OPTIONAL.values() for k in v) + (
    "item", "inspected", "declined", "why", "summary"))
_TOKEN = re.compile(r'\[\s*"(%s)"\s*,' % "|".join(sorted(KEYS)))
_ESC = re.compile(r'(\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))|\\|"')  # a valid escape (kept), else a lone backslash or quote
_CONTROL = {8: "\\b", 9: "\\t", 10: "\\n", 12: "\\f", 13: "\\r"}  # the control characters a lone \b \t \n \f \r in a quote decodes to
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
            try:  # a backslash that starts no JSON escape is a literal one
                yield json.loads('"%s"' % _ESC.sub(lambda m: m.group(1) or ("\\\\" if m.group().startswith("\\") else '\\"'),
                                                   s[i + 1:q])), q + 1
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
            if len(acc) == 1 and isinstance(acc[0], str) and acc[0] in KEYS:  # [key, value] then a third item: a `]` is missing
                _charge(s, budget)
                yield acc + [v], j


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


def _hostile(pin, path):
    """True when the cited file holds a known-key wire token: a quote copied from it could carry keys into a repaired
    line, so such a line keeps no fix, end_line or evidence (dc5)."""
    rows = pin.rows.get(_norm(path))
    return rows is not None and _TOKEN.search("\n".join(rows)) is not None


def _parse(ln, load):
    """(read, dict or None) of a wire line under `load`; the second candidate is the line with its outer bracket restored.
    read is False only when no candidate is JSON syntax: a line that reads but is no pair array (repeated key, raw object,
    wrong shape) is final and never repaired, except a `["` line that loads flat: `["k", v]` is never a pair array, so it
    is the one-pair line with its outer bracket dropped and the restored candidate is tried."""
    for cand in (ln, "[" + ln + "]"):
        try:
            o = load(cand)
            o = _wire(o) if isinstance(o, list) else None
            if not isinstance(o, dict) and cand is ln and re.match(r'\s*\[\s*"', ln):
                continue
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


def _plain(s):
    """`s` as rendered prose, whitespace collapsed: link syntax reduced to its text, emphasis markers dropped (a `_` inside
    a word, as in snake_case, stays). The lane sees the rendered rule, the conventions file holds the Markdown."""
    s = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", s)
    return _ws(re.sub(r"\*+|__+|(?<!\w)_|_(?!\w)", "", s))


def _rule(o, run):
    """((rule, rule_source), None) of a convention line when the rule text occurs in the named conventions/ file, else
    (None, the failing check)."""
    rule, src = o.get("rule"), o.get("rule_source")
    # a stated rule: at least 3 words, matched on word boundaries, so a letter or a word fragment proves nothing
    if not (isinstance(rule, str) and len(_plain(rule).split()) >= 3 and len(rule) <= 500):
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
        if re.search(r"(?<!\w)%s(?!\w)" % re.escape(_plain(rule)), _plain(fh.read())):
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


def _candidate(o, tree, pin, lane, base, ctx=None, nit_mode=False):
    """(candidate, None), or (None, the failing check): cites are judged in the pinned tree `pin`, a norm count in tree/."""
    for k in CANDIDATE_REQUIRED:
        if k not in o:
            return None, "missing-field"
    c = {k: o[k] for k in CANDIDATE_REQUIRED}
    c.update({k: o.get(k, d) for k, d in zip(CANDIDATE_OPTIONAL, (o["line"], ""))})
    end, fix = c["end_line"], c["fix"]
    if (not isinstance(c["path"], str) or c["severity"] not in SEVERITIES or c["category"] not in LANE_CATEGORIES[lane]
            or not isinstance(fix, str) or not all(isinstance(c[k], str) and c[k] for k in ("title", "trigger", "quote"))):
        return None, "bad-type"
    why = nit.check(c, lane, nit_mode)
    if why:
        return None, why
    if lane == "spec" and not (_int(o.get("spec_line")) and o["spec_line"] in ctx):
        return None, "spec-line-invalid"
    c["path"] = os.path.normpath(c["path"])
    if lane == "sota":
        src = _sources(o.get("sources"))
        if src is None or _ws(c["quote"]) != _ws(ctx[o["item"]][2]) or end != c["line"]:
            return None, "sota-invalid"
        c["sources"] = src
    c["rows"], why = _cite(pin, c["path"], c["line"], end, c["quote"])
    alt = None
    if c["rows"] is None and why in ("quote-not-at-line", "line-out-of-range") and c["quote"].translate(_CONTROL) != c["quote"]:
        # a regex or path with a single backslash before b, t, n, f or r parsed as a JSON control character: read it as the
        # written backslash and letter when that reading is what the pinned file holds (the cite check below still decides)
        fixed = c["quote"].translate(_CONTROL)
        rows, again = _cite(pin, c["path"], c["line"], end, fixed)
        if rows is not None:
            c["quote"], c["rows"], why = fixed, rows, again
        else:
            alt = fixed  # neither reading sits at the cited line, so the quote cannot tell them apart: _relocate tries both
    if c["rows"] is None and why in ("quote-not-at-line", "line-out-of-range") and lane != "sota":  # sota cites its checklist item, not a line to move
        moved = None
        for q in (c["quote"], alt):
            moved = q and _relocate(pin, c["path"], c["line"], end, q)
            if moved:
                c["quote"] = q
                break
        if moved:
            c["line"], c["end_line"] = moved
            c["rows"], why = _cite(pin, c["path"], c["line"], c["end_line"], c["quote"])
    if c["rows"] is None:
        return None, "quote-from-checkout" if why != "unreadable" and _from_checkout(pin, c["path"], c["quote"]) else why
    if c["category"] == "convention":
        r, why = _rule(o, base)
        if r is None:
            return None, why
        c["rule"], c["rule_source"] = r
    if c["category"] == "norm":
        n, why = nit.norm(o, tree, c["quote"])
        if n is None:
            return None, why
        c.update(n)
    if nit.basis(c):
        c["basis"] = nit.basis(c)
    if _int(o.get("spec_line")) and "spec_line" in LANE_OPTIONAL[lane]:
        c["spec_line"] = o["spec_line"]
    c["axis"], c["source_lane"] = LANE_AXIS[lane], lane
    c["id"] = _fid(c["axis"], c["path"], c["category"], c["quote"], c["severity"])
    return c, None


def _fid(axis, path, category, quote, severity=None):
    key = (os.path.normpath(path), category, _ws(quote)) + (("nit",) if severity == "nit" else ())  # a nit never shares a stronger finding's id
    # Defects keeps the Phase 3 input; other axes salt it
    return "F-" + hashlib.sha256("\0".join(key if axis == "defects" else (axis,) + key).encode(
        "utf-8", "surrogatepass")).hexdigest()[:10]


def _lost_id(ln, lane):
    """The finding id a rejected wire line names, or None: the line must parse as it stands and carry a path, category
    and quote. A sota line takes its path from the inventory, so it names none."""
    _, o = _parse(ln, _loads)
    if lane == "sota" or not o or not all(isinstance(o.get(k), str) for k in ("path", "category", "quote")):
        return None
    return _fid(LANE_AXIS[lane], o["path"], o["category"], o["quote"], o.get("severity"))


def _refs(run, shard=None):
    """{symbol: (defining file, count of lines naming it outside that file across the whole pinned tree)} from the ledger; {} when unreadable."""
    try:
        led = target.load_json(run if shard is None else os.path.join(run, "shards", str(shard)), "ledger.json")
        return {s["name"]: (s.get("file"), s["refs"]) for s in led["symbols"] if isinstance(s.get("refs"), int)}
    except (target.Refuse, KeyError, TypeError):
        return {}


def _symbols(base):
    """{symbol: defining file} of the ledger at `base` (the run or a shard); {} when unreadable."""
    try:
        return {s["name"]: s.get("file") for s in target.load_json(base, "ledger.json")["symbols"]}
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
        n, have = ledger.defined_name(c["quote"]), (refs or {}).get(c.get("shard"), {})
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
    """Candidates a verifier may judge, in verification order; SOTA is advisory: no verifier call; `skip` ids are doomed; a
    downgraded candidate (claims-missing) gets none either."""
    return sorted((c for c in cands if c["category"] != "sota" and c["id"] not in skip and "downgraded" not in c),
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
            text = fh.read()
        k = text.count("\n") - len(re.findall(r"^failed-validation:(?:%s) " % "|".join(sorted(nit.REFUSED)), text, re.M))  # a refused nit loses nothing
        if n.endswith(".txt") and k:
            is_batch = n.startswith("batch-") or n == "serial.txt"
            (batches if is_batch else lanes)[n[:-4].replace("batch-", "", 1) if is_batch else n[:-4]] = k
    return lanes, batches


COUNTERS = ("attempts", "lost", "repaired", "recovered", "lost_ids")


def _counts(run, name=None, lost=0, repaired=None, drop=lambda n: False, ids=None, found=(), complete=False):
    """<run>/counters.json: {"attempts": {name: n}, "lost": {name: n}, "repaired": {name: n}, "recovered": {name: n},
    "lost_ids": {name: [id or None]}} per lane key and per verify batch ("batch-K", "serial"). The script keeps them so
    the 2-try cap never rests on the model's memory: attempts and lost add up over every ingest of `name` and only
    discard resets them, except that a lane ingest moves a lost line to recovered once its finding id arrives (a lost line
    that named no id: once a complete retry of the lane loses no such line again); repaired is the latest reading of each name, replaced by `repaired` after `drop` removes the
    names it covers. A lane ingest passes `ids`, the finding id of each line it lost (None when the line named none),
    and `found`, the ids it accepted: a lost line whose id is in `found` moves from lost to recovered. Returns the
    stored counters."""
    path = os.path.join(run, "counters.json")
    try:
        with open(path, encoding="utf-8") as fh:
            c = json.load(fh)
    except FileNotFoundError:
        c = {}
    except (OSError, ValueError):
        c = None
    if c is None or not isinstance(c, dict) or any(k in c and not isinstance(c[k], dict) for k in COUNTERS):
        raise target.Refuse("bad-counters", "counters.json is unreadable: discard the run (it would reset the retry counts)")
    c = {k: c.get(k, {}) for k in COUNTERS}
    if name is not None:
        c["attempts"][name] = c["attempts"].get(name, 0) + 1
        if ids is None:
            c["lost"][name] = c["lost"].get(name, 0) + lost
        else:
            prior = c["lost_ids"].get(name, [])
            prior += [None] * (c["lost"].get(name, 0) - len(prior))  # counted before the ids were kept
            # shortcut: an id-less line (unparseable, so no path/category/quote) has no identity to match against `found`;
            # a complete retry that again loses no id-less line is taken as having recovered it. A heuristic: the retry may
            # have dropped that finding instead. Upgrade only if a stable per-line key (e.g. the line's index) is ever stored.
            clear = complete and None not in ids
            keep = [i for i in prior if (i is None and not clear) or (i is not None and i not in found)]
            c["recovered"][name] = c["recovered"].get(name, 0) + len(prior) - len(keep)
            c["lost_ids"][name] = keep + ids
            c["lost"][name] = len(c["lost_ids"][name])
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
    # a lane with rejected lines and a try left gets its retry before any verifier starts: a verdict stored now would
    # make the retry's ingest refuse late-reply (GitHub #4); the cap is 2 tries per lane (SKILL.md step 7)
    blocked = "lane-retry-open" if any(cnt["attempts"].get(n, 0) < 2 for n in rej) else None
    calls = [] if blocked else calls
    return {"status": "ok", "blocked": blocked, "lost": gone, "lost_total": sum(gone.values()), "rejected": rej, "rejected_batches": rejb, "rejected_total": sum(rej.values()) + sum(rejb.values()),
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
    together in queue order, and a refuted verdict still needs a cite no other refuted id uses (review-code-8dt). The prompt
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
    """(valid entries, reasons of the invalid ones) of a {"declined": [{spec_line, why}]} trailer."""
    if not isinstance(o["declined"], list):
        return [], ["declined-invalid:bad-entry"]
    good, bad = [], []
    for d in o["declined"]:
        if not (isinstance(d, dict) and _int(d.get("spec_line")) and d["spec_line"] in ctx and isinstance(d.get("why"), str)
                and _ws(d["why"])):
            bad.append("declined-invalid:bad-entry")
        elif len(d["why"]) > 300:
            bad.append("declined-invalid:too-long")
        elif copied(d["why"]):
            bad.append("declined-invalid:copied-from-spec")
        else:
            good.append({"spec_line": d["spec_line"], "why": _ws(d["why"])})
    return good, bad


def _read_lane(text, lane, tree, run, shard=None):
    kept, areas, invalid, ignored, complete, declined, rejected, repaired = {}, [], 0, 0, False, [], [], 0
    summary = None
    disp = []
    ctx = _spec_lines(run) if lane == "spec" else _inventory(run) if lane == "sota" else None
    copied = _spec_copy(run) if lane == "spec" else None
    base = run if shard is None else os.path.join(run, "shards", str(shard))  # conventions/ lives beside the tree
    pl = _plan(run)
    nit_mode = ("only" if pl.get("tier") == "lean" else True) if pl.get("nit") else False  # --nit --lean reports nits only
    parsed = list(_objects(text))  # read once: the paths of every line go into one ls-tree and one cat-file
    cited, named, syms = set(), set(), _symbols(base)
    for _, o, _ in parsed:
        if o is None:
            continue
        cited.add(ctx[o["item"]][0] if lane == "sota" and _int(o.get("item")) and o["item"] in ctx else o.get("path"))
        if isinstance(o.get("inspected"), list):
            named.update(ctx[a][0] if lane == "sota" else a for a in o["inspected"] if lane != "sota" or _int(a) and a in ctx)
        for name in _claimed(o):
            named.update((name, syms.get(name)))
    pin = _Pinned(run, cited | named, cited)
    for n, (ln, o, fixed) in enumerate(parsed, 1):
        repaired += fixed
        ok = "repaired" if fixed else None  # the reason of an accepted line that was repaired
        why = _syntax(ln) if o is None else None  # else the check that failed
        if o is not None and "path" not in o and "summary" in o and lane == "defects":  # the one summary per lane: the first valid wins
            sm = o["summary"]
            if summary is None and isinstance(sm, str) and _ws(sm) and len(sm) <= SUMMARY_MAX:
                summary = _ws(sm)
                disp.append(_disp(n, None, "accepted", ok))
            else:  # not counted invalid and not a rejected row: a summary holds no finding, so it never flips the verdict
                disp.append(_disp(n, None, "rejected", "bad-summary"))
            continue
        if o is not None and "path" not in o and ("inspected" in o or ("declined" in o and lane == "spec")):
            if "inspected" in o:
                complete = True
                got = o["inspected"] if isinstance(o["inspected"], list) else []
                if lane == "sota":  # item numbers, mapped back to their inventory paths
                    got = [ctx[a][0] for a in got if _int(a) and a in ctx]
                areas += [a for a in got if _norm(a) in pin.files]
            if "declined" in o and lane == "spec":
                good, bad = _declined(o, ctx, copied)
                declined, invalid = declined + good, invalid + len(bad)
                rejected += [(b, ln) for b in bad]  # one row per invalid entry
                if bad:
                    disp.append(_disp(n, None, "downgraded" if good else "rejected", bad[0]))
                    continue
            disp.append(_disp(n, None, "accepted", ok))
            continue
        if lane == "sota" and o is not None:  # the inventory, never the lane, picks the path and line
            o = dict(o, path=ctx[o["item"]][0], line=ctx[o["item"]][1]) if _int(o.get("item")) and o["item"] in ctx else None
        if fixed and o is not None and _hostile(pin, o.get("path")):  # dc5
            o = {k: v for k, v in o.items() if k not in CANDIDATE_OPTIONAL}
        c, check = _candidate(o, tree, pin, lane, base, ctx, nit_mode) if o is not None else (None, "sota-invalid")
        if c is not None and copied and any(copied(c[k]) for k in ("title", "trigger", "fix")):  # CTX-03
            c, check = None, "copied-from-spec"
        if c is None or c["id"] in kept:
            if c is None:
                invalid += 1
                rejected.append((why or check, ln))
                disp.append(_disp(n, _lost_id(ln, lane), "rejected", why or check))
            else:  # a duplicate loses nothing: no rejected row, so it is not counted invalid
                disp.append(_disp(n, c["id"], "rejected", "duplicate"))
        elif LINT_IGNORE.search("\n".join(c.pop("rows"))):  # VERI-05: the author ignored it on purpose
            ignored += 1
            disp.append(_disp(n, c["id"], "rejected", "do-not-flag"))
        else:
            if any(_norm(x) in pin.files for name in _claimed(c) for x in (name, syms.get(name))):
                c["downgraded"] = "claims-missing"  # the pinned tree has what the finding calls missing: no verifier call
            disp.append(_disp(n, c["id"], "downgraded" if "downgraded" in c else "accepted", c.get("downgraded", ok)))
            kept[c["id"]] = dict(c, shard=shard) if shard is not None else c
    return {"candidates": sorted(kept.values(), key=lambda c: c["id"]), "inspected": areas, "complete": complete,
            **({"summary": summary} if summary else {}), "declined": declined, "dropped": {"invalid": invalid, "do_not_flag": ignored}, "rejected": rejected,
            "repaired": repaired, "dispositions": disp}


def _reject(run, name, rows):
    """<run>/rejected/<name>.txt holds one `reason line` row per rejected wire line, replaced whole by a re-ingest of
    the same lane or batch and removed when none is left."""
    d, path = os.path.join(run, "rejected"), os.path.join(run, "rejected", name + ".txt")
    if rows:
        os.makedirs(d, mode=0o700, exist_ok=True)
        _put(path, "".join("%s %s\n" % (why if why.startswith("invalid-json") or why == "retired-object-form" else "failed-validation:" + why, ln)
                                  for why, ln in rows))
    elif os.path.exists(path):
        os.remove(path)


def ingest(run, text, lane="defects", shard=None, agent=None):
    """Ingest one lane's reply (stdin text, or the agent's capture) under the run lock; the status dict."""
    with _locked(run):
        if agent is None:
            return _ingest(run, text, lane, shard)
        calls = _plan(run).get("calls")
        want = next((c.get("agent") for c in calls if isinstance(c, dict) and c.get("lane") == lane and c.get("shard") == shard),
                    None) if isinstance(calls, list) else None
        key = lane if shard is None else "%s@%d" % (lane, shard)
        _bind(run, agent, key)
        out = _ingest(run, _take(run, agent, want, True), lane, shard)
        _consume(run, agent, key)
        return out


def _bind(run, agent, key, record=False):
    """<run>/agents.json maps an agent id to the one call it served (a captures file knows only the id): another call
    key refuses agent-reused, the same key again is a retry. `record` stores the binding after a success."""
    try:
        bound = target.load_json(run, "agents.json")
    except target.Refuse as r:  # only a missing file starts empty: a corrupt one would reset every binding
        if r.reason != "no-agents":
            raise
        bound = {}
    if record:
        _put(os.path.join(run, "agents.json"), json.dumps(dict(bound, **{agent: key}), sort_keys=True))
    elif bound.get(agent, key) != key:
        raise target.Refuse("agent-reused", "this agent id already served another call of the run")


def _take(run, agent, want, needs_inspected):
    """The reply the capture hook stored for `agent` (handback before stop), when it came from agent type `want` and
    holds what the step needs: an `inspected` line for a lane (`needs_inspected` true), else any wire line. Else
    empty-capture (a handback without it) or capture-incomplete (a stop capture without it: the handback is not captured
    yet), so closing words alone never stand in for a reply."""
    target.load_json(run, "target.json")
    for kind in capture.KINDS:
        try:
            with open(capture.path(os.path.dirname(os.path.abspath(run)), agent, kind), encoding="utf-8") as fh:
                got = json.load(fh)
        except (OSError, ValueError, RecursionError):
            continue
        if isinstance(got, dict) and isinstance(got.get("message"), str):
            if got.get("agent_type") != want:
                raise _refuse("agent-mismatch")
            if not any(o is not None and "inspected" in o and "path" not in o if needs_inspected else True for _, o, _ in _objects(got["message"])):
                raise _refuse("capture-incomplete" if kind == "stop" else "empty-capture")  # a stop capture holds the closing words only
            return got["message"].encode("utf-8", "replace").decode("utf-8")  # a lone surrogate cannot be stored
    raise _refuse("no-capture")


def _consume(run, agent, key):
    """After a successful --agent call: bind the agent id to its call and drop the capture."""
    _bind(run, agent, key, True)
    _drop(run, agent)


def _drop(run, agent):
    """Remove both capture files of `agent`."""
    for kind in capture.KINDS:
        with contextlib.suppress(OSError):
            os.remove(capture.path(os.path.dirname(os.path.abspath(run)), agent, kind))


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
    new = _read_lane(text, lane, tree, run, shard)
    if (lanes.get(key, {}).get("complete") is True and new.get("complete") is not True
            and (new["candidates"] or new["rejected"])):  # a partial retry of findings would lose the rest
        _counts(run, key)  # a refused retry is still a try
        raise target.Refuse("no-inspected-line", "the reply has findings but no inspected line, so it would replace the complete "
                            "lane with an incomplete one: send the full corrected reply, the inspected line included")
    lanes[key] = new  # a re-ingest replaces only that lane
    rejected, repaired, disp = lanes[key].pop("rejected"), lanes[key].pop("repaired"), lanes[key].pop("dispositions")
    every = [c for n in sorted(lanes) for c in lanes[n]["candidates"]]
    cands, merged = _dedupe([c for c in every if "downgraded" not in c])  # a claims-missing one never absorbs a real one
    down = {c["id"]: c for c in every if "downgraded" in c and c["id"] not in {x["id"] for x in cands}}
    cands = sorted(cands + list(down.values()), key=lambda c: c["id"])
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
    lost = [(why, ln) for why, ln in rejected if why not in nit.REFUSED]  # the rejected file and the disposition keep a refused nit
    _counts(run, key, len(lost), {key: repaired}, lambda n: n == key or n == "serial" or n.startswith("batch-"),
            [_lost_id(ln, lane) for _, ln in lost], {c["id"] for c in lanes[key]["candidates"]},
            lanes[key].get("complete") is True)
    state = {n: "complete" if lanes[n].get("complete") is True else "incomplete" for n in sorted(lanes)}
    _put(os.path.join(run, "candidates.json"), json.dumps(
        {"candidates": cands, "dropped": dropped, "inspected": [a for n in sorted(lanes) for a in lanes[n]["inspected"]],
         "lanes": lanes, "lane_state": state, "merged": merged, "capped_ids": sorted(cut), "doomed_ids": sorted(doomed),
         "verify_calls": _batches(queue, hand, _plan(run).get("agent_prefix", "review-code"))}, sort_keys=True, indent=2))
    return {"status": "ok", "ids": [c["id"] for c in queue if c["axis"] != "spec"],
            "spec_ids": [c["id"] for c in queue if c["axis"] == "spec"], "dropped": dropped, "handoff": hand,
            "capped": len(cut), "lanes": state, "merged": merged, "repaired": repaired, "dispositions": disp,
            "lane_dropped": lanes[key]["dropped"],
            "rejected": [{"lane": key, "index": i, "check": why} for i, (why, _) in enumerate(rejected, 1)]}


def verdicts(run, text, batch=None, agent=None):
    """Store one verifier batch's reply (stdin text, or the agent's capture) under the run lock."""
    with _locked(run):
        return _verdicts(run, text, batch, agent)


def _replies(run):
    return os.path.join(run, "replies", "verify")


def _one(parsed, allowed, cand_ids, pin, spec, copied, got, paths):
    """Fold one stored reply (its _objects rows) into got (first verdict per id wins); returns (count of ids outside
    `allowed`, rejected rows, repaired line count, disposition rows).
    allowed None is a serial reply (any queued id); a set is a batch, whose refuted needs a cite that validates against
    the pinned tree `pin` in any file (the code elsewhere may already cover the claim) and that no other refuted id of the
    reply uses (review-code-8dt). A refuted that cannot stand is stored `unverifiable` with `downgraded` naming why."""
    foreign, mine, bad, fixed, disp, at = 0, [], [], 0, [], {}  # at: id -> index of its row in disp
    for n, (ln, o, fix) in enumerate(parsed, 1):
        fixed += fix
        o = o and {k: o.get(k) for k in VERDICT_KEYS}
        if o is None or not isinstance(o.get("id"), str) or o.get("verdict") not in VERDICTS:
            why = _syntax(ln) if o is None else "bad-type"
            bad.append((why, ln))
            disp.append(_disp(n, _did(o and o.get("id")), "rejected", why))
            continue
        if allowed is not None and o["id"] not in allowed:
            foreign += 1
            disp.append(_disp(n, _did(o["id"]), "rejected", "other-batch"))
            continue
        if o["id"] not in cand_ids or o["id"] in got:
            disp.append(_disp(n, _did(o["id"]), "rejected", "unknown-id" if o["id"] not in cand_ids else "duplicate"))
            continue
        ev = o.get("evidence")
        cites = [e for e in (ev if isinstance(ev, list) else []) if isinstance(e, dict)
                 and _cited(pin, e.get("path"), e.get("line"), e.get("line"), e.get("quote")) is not None]
        reason = o.get("reason")
        if fix and any(_hostile(pin, p) for p in {paths[o["id"]]} | {e["path"] for e in cites}):
            cites = []  # a repaired line may not carry evidence out of a file that holds wire tokens (dc5)
        if o["id"] in spec and isinstance(reason, str) and copied(reason):  # CTX-03: the line is dropped
            disp.append(_disp(n, o["id"], "rejected", "copied-from-spec"))
            continue
        kind, down = o["verdict"], None  # a downgrade is a refuted verdict that cannot stand, and says why
        if kind == "refuted":
            if fix:  # a repaired line never refutes: text read from a quote must not drop a finding
                kind, down = "unverifiable", "repaired"
            elif allowed is not None and not cites:
                kind, down = "unverifiable", "quote-from-checkout" if any(isinstance(e, dict) and _from_checkout(
                    pin, e.get("path"), e.get("quote")) for e in (ev if isinstance(ev, list) else [])) else "no-cite"
        got[o["id"]] = dict({"verdict": kind, "reason": reason if isinstance(reason, str) else "",
                             "cites": [{k: e[k] for k in CITE_KEYS} for e in cites]}, **({"downgraded": down} if down else {}))
        mine.append(o["id"])
        at[o["id"]] = len(disp)
        disp.append(_disp(n, o["id"], "downgraded" if down else "accepted", down or ("repaired" if fix else None)))
    key = lambda e: (os.path.normpath(e["path"]), e["line"], _ws(e["quote"]))  # noqa: E731
    used = [key(e) for i in mine if allowed is not None and got[i]["verdict"] == "refuted" for e in got[i]["cites"]]
    for i in mine:
        if got[i]["verdict"] == "refuted" and any(used.count(key(e)) > 1 for e in got[i]["cites"]):
            got[i].update(verdict="unverifiable", downgraded="shared-cite")  # one cite cannot refute two findings
            disp[at[i]] = _disp(disp[at[i]]["index"], i, "downgraded", "shared-cite")
    return foreign, bad, fixed, disp


def _verdicts(run, text, batch, agent=None):
    """verdicts() without the lock."""
    cand = target.load_json(run, "candidates.json")
    _counts(run)
    ids = {c["id"] for c in cand["candidates"] if c["category"] != "sota"} - set(cand.get("capped_ids", []))  # a cut one never reached a verifier
    queued = {c["id"] for c in _queue(cand["candidates"], cand.get("doomed_ids", ()))} - set(cand.get("capped_ids", []))  # what status owes
    paths = {c["id"]: c["path"] for c in cand["candidates"]}
    spec = {c["id"] for c in cand["candidates"] if c["axis"] == "spec"}
    copied = _spec_copy(run) if spec else None
    calls = {c["batch"]: set(c["ids"]) for c in cand.get("verify_calls", []) if isinstance(c, dict)}
    if batch is None and calls and _plan(run).get("mode") == "parallel":  # it would replace every stored batch (review-code-3k5)
        raise target.Refuse("batch-required", "plan mode is parallel: pass --batch for each verify call")
    if batch is not None and batch not in calls:
        raise target.Refuse("bad-batch", "--batch is not a batch of candidates.json verify_calls")
    if agent is not None:
        if batch is None:
            raise target.Refuse("batch-required", "--agent needs --batch")
        _bind(run, agent, "batch-%d" % batch)
        text = _take(run, agent, next(c.get("agent") for c in cand["verify_calls"] if isinstance(c, dict) and c.get("batch") == batch), False)
        objs = [o for _, o, _ in _objects(text) if o is not None]  # a line that does not parse is rejected below, by name
        if objs and not any(isinstance(o.get("id"), str) and o["id"] in calls[batch] for o in objs):  # another batch's reply
            raise _refuse("agent-mismatch")
    store = _replies(run)
    if batch is None:  # the single-call path: this reply replaces every stored one, as it always replaced verdicts.json
        shutil.rmtree(os.path.dirname(store), ignore_errors=True)
        for n in _rejected(run)[1]:
            if n != "serial":
                os.remove(os.path.join(run, "rejected", "batch-" + n + ".txt"))
    os.makedirs(store, mode=0o700, exist_ok=True)
    _put(os.path.join(store, "serial.txt" if batch is None else "%d.txt" % batch), text)
    got, foreign, repaired, fixes, lost, disp, rej = {}, 0, 0, {}, 0, [], []
    names = [n for n in os.listdir(store) if re.fullmatch(r"serial\.txt|[1-9][0-9]*\.txt", n)]
    replies = []
    for n in sorted(names, key=lambda n: (n != "serial.txt", int(n[:-4]) if n[0].isdigit() else 0)):
        k = int(n[:-4]) if n[0].isdigit() else None
        if k is None or k in calls:
            with open(os.path.join(store, n), encoding="utf-8") as fh:  # each stored batch has its own repair budget
                replies.append((k, list(_objects(fh.read(), [INGEST_STEPS]))))  # (one hostile batch starves no other)
    # every stored batch is judged again, so all their cites share one ls-tree and one cat-file
    cited = set(paths.values()) | {e.get("path") for _, parsed in replies for _, o, _ in parsed
                                   if o and isinstance(o.get("evidence"), list) for e in o["evidence"] if isinstance(e, dict)}
    pin = _Pinned(run, cited, cited)
    for k, parsed in replies:
        f, bad, fixed, rows = _one(parsed, None if k is None else calls[k], ids, pin, spec, copied, got, paths)
        if k == batch:
            disp, rej = rows, [{"batch": batch if k else "serial", "index": i, "check": why} for i, (why, _) in enumerate(bad, 1)]
        _reject(run, "serial" if k is None else "batch-%d" % k, bad)
        fixes["serial" if k is None else "batch-%d" % k] = fixed
        lost += len(bad) if k == batch else 0
        foreign, repaired = foreign + (f if k == batch else 0), repaired + (fixed if k == batch else 0)
    _put(os.path.join(run, "verdicts.json"), json.dumps(got, sort_keys=True, indent=2))
    _counts(run, "serial" if batch is None else "batch-%d" % batch, lost, fixes, lambda n: n == "serial" or n.startswith("batch-"))
    if agent is not None:
        _consume(run, agent, "batch-%d" % batch)
    conf = sorted(i for i, v in got.items() if v["verdict"] == "confirmed" and v["cites"])
    ref = sorted(i for i, v in got.items() if v["verdict"] == "refuted")
    # confirmed, refuted and unverifiable describe every stored batch (verdicts.json is cumulative); batch_ids names the ids of
    # this call, no_verdict the queued ids with no stored verdict (not yet ingested, or their line was rejected)
    out = {"status": "ok", "confirmed": conf, "refuted": ref, "unverifiable": sorted(set(got) - set(conf) - set(ref)),
           "cumulative": ["confirmed", "refuted", "unverifiable"],
           "no_verdict": sorted(queued - set(got)), "batch_ids": sorted(queued if batch is None else calls[batch]),
           "repaired": repaired, "dispositions": disp, "rejected": rej}
    return out if batch is None else dict(out, foreign=foreign)


def discard(run):
    """Drop every lane result, so a serial re-plan never renders lanes from the parallel attempt (SKILL step 7)."""
    target.load_json(run, "target.json")
    with _locked(run):
        return _discard(run)


def _discard(run):
    """Remove the derived files of a run so a re-plan starts clean; captures are left alone."""
    for d in ("handoff", "repro", "replies", "rejected"):
        shutil.rmtree(os.path.join(run, d), ignore_errors=True)
    for f in ("candidates.json", "verdicts.json", "counters.json", "agents.json"):
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


def _repro(run, fid, modes=None):
    """The repro outcome of a finding; with `modes`, only a record that repro.py wrote in one of them (older records have none: None)."""
    try:
        with open(os.path.join(run, "repro", fid + ".json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        return rec.get("outcome") if modes is None or rec.get("mode") in modes else None
    except (OSError, ValueError, AttributeError):
        return None


NOT_RUN_ITEM = r"(?:defects|standards|spec|sota)(?:@[0-9]+)?|shard [0-9]+|repro|serial"


# plan.py skips that mean there was nothing to run on (no spec given, no dependency to check), not a lane that failed to run
NOT_A_FAILURE_SKIPS = ("no spec", "no dependency or import names in the change")


def _not_run(run, led, skipped, lanes, serial, findings):
    """What this run did not do, from data it already has, in a closed vocabulary (a pack name or free text never passes):
    plan skipped[] `what`s, lanes not complete, serial mode, and repro when trust is not own or a finding has no repro
    outcome while the ledger lists tests or config (the case step 9 could have run)."""
    out = {s.get("what") for s in skipped if isinstance(s.get("what"), str) and s.get("why") not in NOT_A_FAILURE_SKIPS} | {n for n, v in lanes.items() if v != "complete"}
    out = {n for n in out if re.fullmatch(NOT_RUN_ITEM, n)} | ({"serial"} if serial else set())
    if ledger.stored_trust(led) != "own" or (not serial and (led.get("tests") or led.get("config"))
                                               and any(_repro(run, i["id"]) is None for i in findings)):
        out.add("repro")
    return sorted(out)


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
            cls, reason = ("reproduced" if _repro(run, c["id"], ("run", None)) == "reproduced" else "traced"), v["reason"]
        elif kind == "unverifiable" and cites and not serial and c["category"] == "test" and _repro(run, c["id"], ("base",)) == "reproduced":
            cls, reason = "reproduced", v["reason"]  # "passes on the merge base" is observed by repro.py --base, not by reading
        else:
            cls = "inferred" if cites else "speculative"
            reason = v["reason"] if kind == "unverifiable" else "no-valid-cite" if kind == "confirmed" else (
                "verifier-cap" if c["id"] in capped else c.get("downgraded", "no-verdict"))
        origin = "existing" if whole else "introduced" if any(
            n in added.get(c["path"], ()) for n in range(c["line"], c["end_line"] + 1)) else "pre-existing"
        sev = SEVERITIES[max(SEVERITIES.index(c["severity"]), SEVERITIES.index(_cap(cls, c["category"])),
                             SEVERITIES.index(CEILING.get(c["category"], SEVERITIES[0])))]
        # VERI-05; Blocking and Required stay (VERI-06); a cut id never got a verdict, so it shows as verifier-cap
        if origin == "pre-existing" and sev in SEVERITIES[2:] and c["id"] not in capped:
            dropped["do_not_flag"] += 1
            continue
        items.append(dict({k: x for k, x in c.items() if k != "downgraded"}, severity=sev, evidence=cites, evidence_class=cls, score=SCORE[cls],
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
        label = "Needs discussion"  # a lane line or verdict that was dropped may have held a finding or a refutation
    areas = sorted(set(cand["inspected"]))  # checked against the lane's own tree at ingest
    skipped = plan.get("skipped")
    skipped = [s for s in skipped if isinstance(s, dict)] if isinstance(skipped, list) else []
    if label == "No blocking findings" and _defects_skipped(skipped):
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
    doc = {"schema_version": SCHEMA_VERSION, "summary": summary, "not_run": _not_run(run, led, skipped, lanes, serial, findings),
            "target": {"kind": tj["kind"], "head_sha": tj["head_sha"], "merge_base": tj["merge_base"],
                       "label": led["label"], "not_a_change": whole},
            "trust": "own" if ledger.stored_trust(led) == "own" else "external",
            "verdict": label, "findings": findings, "unverifiable": unverifiable, "dropped": dropped, "skipped": skipped, "declined": declined,
            "shards": shards, "mode": "serial" if serial else "parallel", "tier": plan["tier"] if isinstance(plan.get("tier"), str) else None,
            "lost_guarantees": [x for x in plan["lost_guarantees"] if isinstance(x, str)]
            if isinstance(plan.get("lost_guarantees"), list) else [],
            "basis": {"pinned_head": tj.get("pinned_head"), "pinned_tree": tj.get("pinned_tree"), "difference": tj.get("difference_count"),
                      "lost_capabilities": [x for x in plan.get("lost_capabilities", []) if isinstance(x, str)]
                      if isinstance(plan.get("lost_capabilities"), list) else []},
            "lanes": lanes, "merged": cand.get("merged", 0),
            "lean_lines": sum(i["end_line"] - i["line"] + 1 for i in findings if i["category"] == "lean"),  # an estimate
            "inspected": {"files": tj["included"], "areas": areas,
                          "ledger_counts": {k: len(led[k]) for k in ledger.CATEGORIES}}}
    if plan.get("nit"):  # --nit: the count rides in the summary; it never changes the verdict
        doc["nit"] = sum(1 for i in findings if i["severity"] == "nit")
    if isinstance(led.get("capped", {}).get("adr"), int) and led["capped"]["adr"] > 0:  # ADRs the spec lane never saw
        doc["adr_omitted"] = led["capped"]["adr"]
    flagged, routine = ledger.hidden_notes(led["hidden_text"])  # signalled spans singly, the rest as a count per file
    hc = [{"file": h.get("path", h["file"]), "line": h["line"], "kind": h["kind"], "length": h["length"], "name": False,
           "signal": h["signal"], **({"provenance": h["provenance"]} if h.get("provenance") else {})} for h in flagged]
    hc += [{"file": h["file"], "line": h.get("line"), "kind": "hidden-chars", "codepoints": h["codepoints"],
            "name": h.get("name") is True} for h in led["hidden_chars"]]
    if rej or rejb or lost:
        doc.update(rejected=rej, rejected_batches=rejb, no_verdict=lost)
    if repaired:
        doc.update(repaired=repaired)
    if gone:  # lines dropped by any ingest, kept until discard unless a later ingest of the lane recovers them: a retry that omits them must not clear the warning (dc5)
        doc.update(lost=gone)
    won = {n: k for n, k in cnt["recovered"].items() if k}
    if won:  # lost lines whose finding a later ingest of the lane carried (_counts): reported, but they no longer force Needs discussion
        doc.update(recovered=won)
    if hc:  # OUT-02: notes outside the evidence gate; positions and sizes, never the payload
        doc.update(hidden_content=hc[:MAX_HIDDEN], hidden_content_omitted=max(0, len(hc) - MAX_HIDDEN))
    if routine:
        doc.update(hidden_routine=routine)
    return doc


_LEVEL = {"blocking": "error", "required": "warning", "suggestion": "note", "nit": "note", "noted": "note"}


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
                     "properties": dict({"evidenceClass": f["evidence_class"], "severity": f["severity"],
                                        "origin": f["origin"], "score": f["score"], "axis": f["axis"]},
                                       **{k: f[k] for k in ("basis", "rule") if k in f})} for f in fs]
        + [{"ruleId": "hidden-content", "level": "note", "message": {"text": "hidden content in a change: " + h["kind"]},
            "locations": [{"physicalLocation": dict({"artifactLocation": {"uri": quote(h["file"])}},
                                                    **({"region": {"startLine": h["line"]}} if h["line"] is not None else {}))}],
            "properties": {k: h[k] for k in ("length", "codepoints", "name") if k in h and h[k] is not None}} for h in hc],
        "properties": {"inspectedAreas": areas[:MAX_AREAS], "inspectedAreasOmitted": max(0, len(areas) - MAX_AREAS)}}]}


def basis_line(b, lead):
    """What the review was judged against (gh#48): the pinned head, or the pinned tree for an in-place target, and the
    checkout difference size. Object ids and a count only, so the line holds no repository text."""
    n = b["difference"]
    return "%s%s, checkout difference %d path%s" % (lead, ("head " + b["pinned_head"]) if b["pinned_head"] else (
        "tree " + b["pinned_tree"]), n, "" if n == 1 else "s")


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
    out = ["Summary"] + ["%s change summary (model text, unverified): %s" % (e(x["lane"]), e(x["text"])) for x in doc["summary"]]
    out += ["verdict: " + doc["verdict"], "target: %s %s (merge base %s), ledger %s" % (
        e(t["kind"]), e(t["head_sha"]), e(t["merge_base"]), e(t["label"]))]
    if t["not_a_change"]:
        out.append("Target is not a change: whole-file findings")
    out += ["mode: " + doc["mode"]] + (["tier: " + e(doc["tier"])] if doc["tier"] else [])
    out += ["lost guarantee: " + e(g) for g in doc["lost_guarantees"]]
    out += [basis_line(doc["basis"], "basis: pinned ")] if doc["basis"]["pinned_tree"] else []
    out += ["lost capability: " + e(c) for c in doc["basis"]["lost_capabilities"]]
    ig = doc.get("integrity")
    if ig:
        rows = (["integrity warning: %s (working tree) %s" % (e(p), ig["cause"]) for p in ig["working_tree"]]
                + (["integrity warning: index " + ig["cause"]] if ig["index"] else [])
                + ["integrity note: %s %s" % (e(p), ig["cause"]) for p in ig["knowledge"]])
        out += (rows[:MAX_HIDDEN] + (["integrity: +%d more in --format json" % (len(rows) - MAX_HIDDEN)] if len(rows) > MAX_HIDDEN else [])
                if rows else ["integrity: no change to the checkout, index or tool knowledge during the run"])
    out += ["findings: %d, unverifiable: %d" % (len(fs), sum(1 for i in doc["unverifiable"] if i["category"] != "sota")),
            "dropped: refuted %d, do-not-flag %d, invalid %d" % (d["refuted"], d["do_not_flag"], d["invalid"]),
            "trust: " + doc["trust"]]
    out += ["nits: %d" % doc["nit"]] if "nit" in doc else []
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
    out += ["Not run: " + ", ".join(doc["not_run"])] if doc["not_run"] else []
    out += ["Lean: -%d lines possible (estimate)" % doc["lean_lines"]] if doc["lean_lines"] else []
    if (doc.get("rejected") or doc.get("rejected_batches") or doc.get("no_verdict") or doc.get("repaired") or doc.get("lost")
            or doc.get("recovered")):
        out += ["", "Warning"]
        out += ["rejected lines: %d (%s)" % (sum(doc["rejected"].values()), ", ".join(
            "%s %d" % (e(n), k) for n, k in sorted(doc["rejected"].items())))] if doc.get("rejected") else []
        out += ["rejected verdict lines: %d (%s)" % (sum(doc["rejected_batches"].values()), ", ".join(
            "batch %s %d" % (e(n), k) for n, k in sorted(doc["rejected_batches"].items())))] if doc.get("rejected_batches") else []
        out += ["repaired lines: %d (%s)" % (sum(doc["repaired"].values()), ", ".join(
            "%s %d" % (e(n), k) for n, k in sorted(doc["repaired"].items())))] if doc.get("repaired") else []
        out += ["lost lines: %d (%s)" % (sum(doc["lost"].values()), ", ".join(
            "%s %d" % (e(n), k) for n, k in sorted(doc["lost"].items())))] if doc.get("lost") else []
        out += ["recovered lines: %d (%s)" % (sum(doc["recovered"].values()), ", ".join(
            "%s %d" % (e(n), k) for n, k in sorted(doc["recovered"].items())))] if doc.get("recovered") else []
        out += ["no verdict: %d (%s)" % (len(doc["no_verdict"]), ", ".join(doc["no_verdict"]))] if doc.get("no_verdict") else []
    out.append("")

    def section(head, rows):
        return [head] + (rows or ["none"]) + [""]

    for ax in AXES:
        out.append(ax.capitalize())
        sota = [i for i in doc["unverifiable"] if i["category"] == "sota"] if ax == "standards" else []
        unv = [i for i in doc["unverifiable"] if i["axis"] == ax and i not in sota]
        if (ax != "defects" or _defects_skipped(doc["skipped"])) and not unv and not sota and not any(i["axis"] == ax for i in fs) and not (ax == "spec" and doc["declined"]):
            why = [s.get("why") for s in doc["skipped"] if s.get("what") == ax]
            out += ["%s: skipped (%s)" % (ax.capitalize(), e(why[0])) if why else "none", ""]
            continue
        for head, pick in (("Blocking", ("blocking",)), ("Required", ("required",)),
                           ("Suggestions", ("suggestion", "noted"))):
            out += section(head, [x for i in fs if i["axis"] == ax and i["severity"] in pick for x in item(i)])
        nits = [i for i in fs if i["axis"] == ax and i["severity"] == "nit"]
        out += section("Nits", nit.rows(nits, e)) if nits else []
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
    if doc.get("hidden_content") or doc.get("hidden_routine"):
        out += section("Hidden content (notes)", ["- %s%s  %s%s%s%s%s%s" % (
            e(h["file"]), ":%d" % h["line"] if h["line"] is not None else "", h["kind"],
            ", length %d" % h["length"] if "length" in h else "",
            ", codepoints " + " ".join(str(c) for c in h["codepoints"]) if "codepoints" in h else "",
            ", file name" if h["name"] else "", ", " + h["signal"] if h.get("signal") else "",
            ", added by the change" if h.get("provenance") == "added" else "")
            for h in doc.get("hidden_content", [])]
            + (["(+%d more)" % doc["hidden_content_omitted"]] if doc.get("hidden_content_omitted") else [])
            + ["- %s  %d routine hidden span%s removed (%s)" % (
                e(r["file"]), r["count"], "" if r["count"] == 1 else "s",
                ", ".join("%s %d" % (k, n) for k, n in sorted(r["kinds"].items()))) + (
                ", %d of them added by the change" % r["added"] if r.get("added") else "")
                for r in doc.get("hidden_routine", [])])
    out += section("Areas inspected", ["- " + e(a) for a in doc["inspected"]["areas"]]
                   + (["- %d ADR file%s omitted by the cap" % (doc["adr_omitted"], "" if doc["adr_omitted"] == 1 else "s")]
                      if doc.get("adr_omitted") else []))
    return "\n".join(out + ["Verdict", doc["verdict"] + " (advisory only)", ""])


def _integrity(run, tj):
    """gh#51: what changed in the checkout, its index and tool knowledge since target.py's snapshot, or None when the
    run has no snapshot or the comparison fails. It reports only: never a refusal, the verdict or a finding."""
    try:
        with open(os.path.join(run, "integrity.json"), encoding="utf-8") as fh:
            snap = json.load(fh)
        target._NO_FILTERS.update(target._no_filters(tj["repo"]))  # the snapshot's git ran without filters too
        return target.integrity_changes(tj["repo"], snap)
    except (OSError, ValueError, KeyError, TypeError, AttributeError, target.Refuse):
        return None


def render(run, fmt):
    doc = build(run)
    doc["integrity"] = _integrity(run, target.load_target(run))  # render only: post.py's build runs no status
    if fmt == "text":
        return _text(doc)
    return json.dumps(_sarif(doc) if fmt == "sarif" else doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"


def _usage():
    """The refusal for a bad command line."""
    return target.Refuse("usage", "usage: report.py ingest --run DIR [--lane defects|standards|spec|sota] [--shard N] [--agent ID] | verdicts --run DIR [--batch K] [--agent ID] | render --run DIR [--format text|json|sarif] | discard --run DIR | status --run DIR")


def main(argv):
    """CLI entry: run one subcommand and print its one JSON line (render prints the report)."""
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
        while len(tail) >= 2 and tail[0] in (("--lane", "--shard", "--agent") if sub == "ingest" else ("--batch", "--agent")) and tail[0] not in opts:
            opts[tail[0]], tail = tail[1], tail[2:]
        lane = opts.get("--lane", "defects")
        if tail or lane not in LANE_AXIS:
            raise _usage()
        if "--shard" in opts and not re.fullmatch(r"[1-9][0-9]{0,8}", opts["--shard"]):
            raise target.Refuse("bad-shard", "--shard is not a positive integer")
        if "--batch" in opts and not re.fullmatch(r"[1-9][0-9]{0,8}", opts["--batch"]):
            raise target.Refuse("bad-batch", "--batch is not a positive integer")
        agent = opts.get("--agent")
        if agent is not None and not capture.ID.fullmatch(agent):
            raise target.Refuse("bad-agent", "--agent is not an agent id")
        text = None if agent else sys.stdin.buffer.read().decode("utf-8", "replace")  # the capture replaces stdin
        shard, batch = (int(opts[k]) if k in opts else None for k in ("--shard", "--batch"))
        out = ingest(run, text, lane, shard, agent) if sub == "ingest" else verdicts(run, text, batch, agent)
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
