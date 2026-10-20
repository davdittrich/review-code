"""Choose lanes, models and caps for one run and write <run>/plan.json (mode 0600). Stdlib only, Python 3.9 grammar.

    plan.py plan --run DIR [--tier quick|standard|deep] [--sota|--no-sota] [--serial] [--ai]
                                    stdout: one JSON line (status, reason, detail, tier, mode, calls (count),
                                    skipped, lost_guarantees, confirm, preview)
    stdin (optional, not a TTY): key=value lines, keys in CONFIG_KEYS; empty values and `${...}` (an unset
    userConfig reference) are ignored; anything else invalid refuses `bad-config`, naming only the key.

plan.json (sort_keys): version, tier, mode (parallel|serial), calls[{lane, axis, agent, model, effort, input, shard,
prompt}], verifier{agent, model, effort}, spec_verifier{...}, caps{max_verifier_calls, max_shards, max_packs,
wave_size}, max_turns{name: n}, packs, shards, skipped[{what, why}], lost_guarantees, sota_inventory, preview,
confirm, effective_overrides{key: value}, ai. The orchestrator obeys it; report.py reads caps.max_verifier_calls and skipped.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import fnmatch
import json
import os
import re
import shutil

import ledger
import report
import sanitize
import target

STDOUT_KEYS = {"status", "reason", "detail", "tier", "mode", "calls", "skipped", "lost_guarantees", "confirm", "preview",
               "wave_size", "verifier", "spec_verifier"}
DEFAULT_TIER = "standard"
# (model, effort) per role. Unvalidated defaults: there is no eval corpus to tune them against. The quick verifier
# is opus so it differs from the sonnet finder: the verifier must not share the finder's model.
TIERS = {
    "quick": {"lanes": ("defects", "standards"), "finder": ("sonnet", "low"), "verifier": ("opus", "medium"),
              "max_verifier_calls": 10, "max_shards": 3, "max_packs": 2},
    "standard": {"lanes": ("defects", "standards", "spec"), "finder": ("opus", "medium"),
                 "verifier": ("sonnet", "high"), "max_verifier_calls": 25, "max_shards": 6, "max_packs": 4},
    "deep": {"lanes": ("defects", "standards", "spec", "sota"), "finder": ("opus", "xhigh"),
             "verifier": ("sonnet", "high"), "max_verifier_calls": 60, "max_shards": 12, "max_packs": 9},
}
LANE_AGENT = {"defects": "review-code:finder", "standards": "review-code:standards", "spec": "review-code:spec",
              "sota": "review-code:sota"}
# Ceilings, not per-tier values: maxTurns is frontmatter-only (sub-agents doc), so a call cannot lower it.
MAX_TURNS = {"finder": 40, "standards": 40, "spec": 30, "sota": 20, "verifier": 30, "spec-verifier": 30}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
ALIASES = ("opus", "sonnet", "haiku", "fable")  # the family aliases plugin.json offers
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,63}")
CAP_BOUNDS = {"max_verifier_calls": (1, 200), "max_shards": (1, 50), "max_packs": (1, 9)}
CONFIG_KEYS = {"default_tier", "finder_model", "finder_effort", "verifier_model", "verifier_effort"} | set(CAP_BOUNDS)
MAX_STDIN = 65536
# The harness refuses a 9th concurrent subagent ("You can run 8 subagents at once", live run 2026-10-19), so a wave stops at 8.
WAVE_SIZE = 8


def parse_config(text):
    """Validated {key: value} from key=value lines. Never echoes a rejected value (T-04-18)."""
    cfg = {}
    for line in text.splitlines():
        key, eq, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not (key or eq or val):
            continue
        if not eq or key not in CONFIG_KEYS:
            raise target.Refuse("bad-config", "unknown or malformed config line")
        if not val or val.startswith("${"):
            continue  # unset userConfig reference: the tier's value stands
        if key in CAP_BOUNDS:
            lo, hi = CAP_BOUNDS[key]
            ok = re.fullmatch(r"[0-9]{1,4}", val) is not None and lo <= int(val) <= hi
        elif key.endswith("_model"):
            ok = MODEL_RE.fullmatch(val) is not None
        else:
            ok = val in (EFFORTS if key.endswith("_effort") else TIERS)
        if not ok:
            raise target.Refuse("bad-config", "invalid value for " + key)
        cfg[key] = int(val) if key in CAP_BOUNDS else val
    return cfg


def _read_config():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    text = sys.stdin.read(MAX_STDIN + 1)
    if len(text) > MAX_STDIN:
        raise target.Refuse("bad-config", "config input too large")
    return parse_config(text)

# Domain packs. Row: (name, file globs, content regex, content rule applies only to files matching these globs).
# A file matches by glob (on the path or its base name) or by the regex over its scanned lines: the added lines of
# change.diff for a change target, the first SCAN_BYTES of its tree/ copy for path and repo targets. Heuristics: a
# hit loads a pack for the lane to read, it is never a finding. llm-generated-code loads only through the ai flag
# or the deep tier (ccr-contract: authorship is not knowable from a diff). Names come from this table only.
LLM_PACK = "llm-generated-code"
PACKS = (
    ("python", ("*.py", "pyproject.toml"), None, None),
    ("r", ("*.R", "*.r", "DESCRIPTION", "NAMESPACE"), None, None),
    ("js-ts", ("*.js", "*.jsx", "*.ts", "*.tsx", "*.mjs", "*.cjs", "package.json"), None, None),
    ("sql", ("*.sql",), None, None),
    ("data-migrations", ("migrations/*", "*/migrations/*", "migrate/*", "*/migrate/*"),
     re.compile(r"\b(?:CREATE|ALTER|DROP)\b", re.I), ("*.sql",)),
    ("security-owasp", (), re.compile(
        r"\bsubprocess\b|\beval\(|\bexec\(|innerHTML|\bpickle\b|yaml\.load\b|os\.system\b"
        r"|\b(?:select|insert\s+into|update|delete\s+from)\b[^\n]*(?:[\"']\s*(?:%|\+)|\.format\(|\{[^}]*\})", re.I), None),
    ("concurrency", (), re.compile(
        r"\b(?:[Tt]hread\w*|asyncio|await|\w*Lock|[Mm]utex|goroutine|Promise\.all|multiprocessing)\b"), None),
    ("api-compat", ("__init__.py", "index.ts", "index.js", "openapi*", "*.proto", "NAMESPACE", "package.json"), None, None),
    (LLM_PACK, (), None, None),
)
SCAN_BYTES = 65536  # per file read for content rules
LINE_MAX = 2000  # per line, so the regexes above stay cheap on a minified line
REFS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "references")


def _glob(path, globs):
    base = path.rpartition("/")[2]
    return any(fnmatch.fnmatchcase(path, g) or fnmatch.fnmatchcase(base, g) for g in globs)


def _scan_lines(run, tj):
    """{path: [(line number, text)]} of included files: added lines of change.diff, or the head of each tree/ copy."""
    inc, out = set(tj["included"]), {}
    if tj["kind"] in ("path", "repo"):
        tree = os.path.realpath(os.path.join(run, "tree"))
        for p in sorted(inc):
            fp = os.path.realpath(os.path.join(tree, p))
            if not fp.startswith(tree + os.sep):
                continue
            try:
                with open(fp, "rb") as fh:
                    data = fh.read(SCAN_BYTES)
            except OSError:
                continue
            out[p] = [(i, ln[:LINE_MAX]) for i, ln in enumerate(data.decode("utf-8", "replace").split("\n"), 1)]
        return out
    with open(os.path.join(run, "change.diff"), encoding="utf-8", errors="replace") as fh:
        diff, used = fh.read(), {}
    for _, path, tag, _, n, body, _ in sanitize.walk(diff):
        if tag == "+" and path in inc and used.get(path, 0) < SCAN_BYTES:
            used[path] = used.get(path, 0) + len(body) + 1
            out.setdefault(path, []).append((n, body[:LINE_MAX]))
    return out


def _match_packs(files, lines, with_llm):
    """[(pack name, sorted matching files)] ranked by file count descending, then PACKS order."""
    found = []
    for name, globs, rx, only in PACKS:
        if name == LLM_PACK:
            hit = list(files) if with_llm else []
        else:
            hit = [p for p in files if _glob(p, globs) or (rx is not None and (only is None or _glob(p, only))
                                                          and any(rx.search(t) for _, t in lines.get(p, ())))]
        if hit:
            found.append((name, sorted(hit)))
    return sorted(found, key=lambda f: -len(f[1]))  # stable: PACKS order breaks ties


def _ref(*parts):
    with open(os.path.join(REFS, *parts), encoding="utf-8") as fh:
        return fh.read()


def _put_sections(path, sections):
    """Replace or append `## head` sections at the end of a run input file; a rerun never doubles one."""
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    for head, _ in sections:
        text = re.sub(r"^## %s\n.*?(?=^## |\Z)" % re.escape(head), "", text, flags=re.M | re.S)
    target.write_private(path, text.rstrip("\n") + "".join("\n\n## %s\n\n%s\n" % hb for hb in sections))


def _install_packs(run, packs):
    """Copy packs [(name, files)] and smells.md under run/inputs and list them in the defects and standards inputs."""
    inp = os.path.join(run, "inputs")
    shutil.rmtree(os.path.join(inp, "packs"), ignore_errors=True)
    for name, _ in packs:
        ledger.write(os.path.join(inp, "packs", name + ".md"), _ref("packs", name + ".md"))
    ledger.write(os.path.join(inp, "smells.md"), _ref("smells.md"))
    listed = "\n".join("- " + json.dumps(os.path.join(inp, "packs", n + ".md")) for n, _ in packs) or "none"
    _put_sections(os.path.join(inp, "defects.md"), [("Domain packs", listed)])
    _put_sections(os.path.join(inp, "standards.md"), [
        ("Domain packs", listed), ("Smells", "- " + json.dumps(os.path.join(inp, "smells.md")))])


# Module shards (path and repo targets). 400000 bytes is about 100k tokens at 4 bytes per token, which leaves room in
# a 200k-token lane context for the ledger, the diff and the lane's output.
SHARD_BYTES = 400000
CHURN_DAYS = 180  # about two release cycles
CHURN_COMMITS = 5000  # shortcut: only the last 5000 commits count, upgrade when a monorepo needs more
RISK_TOKENS = ("auth", "login", "passw", "secret", "token", "crypt", "session", "admin", "payment", "billing",
               "upload", "sql", "migrat", "exec", "shell", "serial", "pickle", "permission")


def _file_stat(repo, p):
    """(bytes, lines) of a working-copy file; (0, 0) when unreadable."""
    size = lines = 0
    try:
        with open(os.path.join(repo, p), "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                size, lines = size + len(chunk), lines + chunk.count(b"\n")
    except OSError:
        return 0, 0
    return size, lines


def _groups(files, size):
    """Ordered file lists: top-level directory groups (root files form "."), a group above SHARD_BYTES split by its
    second path component, consecutive small groups merged in path order up to SHARD_BYTES. shortcut: one level of
    split only, so a single huge directory below that stays one shard; upgrade when one appears."""
    by = {}
    for p in sorted(files):
        by.setdefault(p.split("/")[0] if "/" in p else ".", []).append(p)
    groups = []
    for top in sorted(by):
        if top != "." and sum(size[p] for p in by[top]) > SHARD_BYTES:
            sub = {}
            for p in by[top]:
                parts = p.split("/")
                sub.setdefault(parts[1] if len(parts) > 2 else ".", []).append(p)
            groups += [sub[k] for k in sorted(sub)]
        else:
            groups.append(by[top])
    out, cur, tot = [], [], 0
    for g in groups:
        n = sum(size[p] for p in g)
        if cur and tot + n > SHARD_BYTES:
            out.append(cur)
            cur, tot = [], 0
        cur, tot = cur + g, tot + n
    return out + [cur] if cur else out


def _churn(repo, names):
    """{path: lines added + deleted over CHURN_DAYS} from one git log call; empty when git fails."""
    out = target.git(["-c", "core.quotepath=off", "log", "--numstat", "--format=", "--no-renames",
                      "--since=%d.days" % CHURN_DAYS, "--max-count=%d" % CHURN_COMMITS], repo, check=False)
    churn = {}
    for ln in out.splitlines():
        added, _, rest = ln.partition("\t")
        deleted, _, path = rest.partition("\t")
        if path in names and added.isdigit() and deleted.isdigit():
            churn[path] = churn.get(path, 0) + int(added) + int(deleted)
    return churn


def _rank_shards(tj, led, groups, stats):
    """[(risk, files)] riskiest first. Risk = files whose path holds a RISK_TOKENS entry, plus 1 when no file of the
    shard is a test or has a test in the ledger, plus churn over the shard's current line count. Weights are
    unvalidated: ties break by first path."""
    churn = _churn(tj["repo"], {p for g in groups for p in g})
    tested = {e["file"] for e in led.get("tests", []) if isinstance(e, dict) and isinstance(e.get("file"), str)}
    ranked = []
    for g in groups:
        tokens = sum(1 for p in g if any(t in p.lower() for t in RISK_TOKENS))
        untested = 0 if any(p in tested or ledger._is_test(p) for p in g) else 1
        ranked.append((tokens + untested + sum(churn.get(p, 0) for p in g) / max(1, sum(stats[p][1] for p in g)), g))
    return sorted(ranked, key=lambda r: (-r[0], r[1][0]))


def _build_shard(run, tj, i, files, trust, packs):
    """<run>/shards/<i>/: target.json for its files, empty change.diff, own ledger and tree, the run's trust answer,
    and the global packs that match its files."""
    d = os.path.join(run, "shards", str(i))
    os.makedirs(d, mode=0o700)
    target.write_private(os.path.join(d, "target.json"), json.dumps(dict(tj, included=files, run=d), sort_keys=True, indent=2))
    target.write_private(os.path.join(d, "change.diff"), "")
    ledger.build(d)
    if trust in ledger.TRUST_VALUES:
        ledger.trust_set(d, trust)
    mine = set(files)
    _install_packs(d, [(n, [f for f in fs if f in mine]) for n, fs in packs if mine & set(fs)])
    return d


# SOTA inventory: dependency, import, library and called API names and pinned versions only, so the web lane never
# sees repository text. Path and line stay in plan.json; the lane's prompt numbers each item instead (WR-04).
INVENTORY_MAX = 50  # keeps the task message small and bounds the lane's search budget
NAME_RE = re.compile(r"[A-Za-z0-9@/._-]{1,100}")
VERSION_RE = re.compile(r"[0-9][0-9A-Za-z.+_-]{0,39}")
KINDS = ("dependency", "import", "api")  # inventory order: manifest pins outrank imports and calls under the cap
_CALL = re.compile(r"(?<![\w.])([A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+)\s*\(")
# Call roots kept without an import in the change: modules whose algorithm or API choices age (weak hashes, unsafe
# deserialisers, non-crypto randomness). shortcut: a fixed list, Python and JS only; extend when a lane misses one.
KNOWN_ROOTS = frozenset(("hashlib", "hmac", "random", "pickle", "marshal", "shelve", "ssl", "crypt", "tempfile",
                         "subprocess", "os", "yaml", "xml", "datetime", "telnetlib", "ftplib", "Math", "crypto"))
_V = r"""([^\s,;"')\]=]+)"""  # a candidate version, kept only when VERSION_RE matches all of it
_PIN_PY = re.compile(r"(?<![<>!~=])===?\s*" + _V)  # an exact pin; ranges and wildcards carry no version
_PIN_NPM = re.compile(r':\s*"=?v?' + _V + '"')
_PIN_R = re.compile(r"\(\s*==\s*" + _V + r"\s*\)")
_DEP = r"([A-Za-z0-9][A-Za-z0-9._-]*)"
_PY_IMPORT = re.compile(r"^\s*(?:from\s+([A-Za-z_][\w.]*)\s+import\b|import\s+([A-Za-z_][\w., ]*))")
_JS_IMPORT = re.compile(r"""(?:\bfrom\s+|\brequire\(\s*|\bimport\s+)['"]([^'"\s]+)['"]""")
_R_LIB = re.compile(r"""\b(?:library|require)\(\s*["']?([A-Za-z][\w.]*)""")
_REQ = re.compile(r"^\s*" + _DEP + r"\s*(?:[<>=!~;\[@]|$)")
# shortcut: a quoted bare word such as a keyword also passes; upgrade to a TOML parse if that noise matters
_TOML_DEP = re.compile(r"""^\s*["']""" + _DEP + r"""\s*(?:[<>=!~;\[@]|["'])""")
_PKG_JSON = re.compile(r'^\s*"(@?[A-Za-z0-9][A-Za-z0-9._/-]*)"\s*:\s*"(?:[\^~<>=]*\d|latest|\*)')
_DESC = re.compile(r"^(?:Imports:\s*|\s+)([A-Za-z][A-Za-z0-9.]*)\s*(?:\([^)]*\))?\s*,?\s*$")
_JS_EXT = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs")


def _code(text, marker):
    """The line without its comment. shortcut: a marker inside a string also cuts the line (a URL before an import
    loses it); a block comment is dropped when it opens or continues the line."""
    t = text.lstrip()
    return "" if t.startswith(("/*", "*")) else re.sub(r"/\*.*?\*/", "", text).split(marker)[0]


def _bare(code, start):
    """True when no quote opens before `start`: the match is code, not text inside a string."""
    return not re.search("[\'\"`]", code[:start])


def _names(path, text):
    """[(kind, name, version or None)] of one line: imports, or a manifest entry with its exact pinned version."""
    base = path.rpartition("/")[2]
    if base.endswith(".py"):
        m = _PY_IMPORT.match(text)
        if not m:
            return []
        if m.group(1):
            return [("import", m.group(1).split(".")[0], None)]
        return [("import", x.split()[0].split(".")[0], None) for x in m.group(2).split(",") if x.split()]
    if base.endswith(_JS_EXT):  # T-04-21: comment text and string text are not code, so neither becomes a name
        code = _code(text, "//")
        return [("import", "/".join(n.split("/")[:2]) if n.startswith("@") else n.split("/")[0], None)
                for m in _JS_IMPORT.finditer(code) if _bare(code, m.start()) for n in m.groups()
                if not n.startswith((".", "/"))]
    if base.endswith((".R", ".r")):
        code = _code(text, "#")
        return [("import", m.group(1), None) for m in _R_LIB.finditer(code) if _bare(code, m.start())]
    rx, pin = ((_REQ, _PIN_PY) if fnmatch.fnmatchcase(base, "requirements*.txt") else
               (_TOML_DEP, _PIN_PY) if base == "pyproject.toml" else (_PKG_JSON, _PIN_NPM) if base == "package.json"
               else (_DESC, _PIN_R) if base == "DESCRIPTION" else (None, None))
    m = rx.match(text) if rx else None
    if not m or m.group(1) in ("version", "R"):
        return []
    v = pin.search(text, m.end(1))
    return [("dependency", m.group(1), v.group(1) if v and VERSION_RE.fullmatch(v.group(1)) else None)]


def _inventory(lines):
    """[{item, kind, name, path, line[, version]}] of safe names, first location per (kind, name, version),
    dependencies first, at most INVENTORY_MAX, item numbered from 1."""
    seen, out = set(), []
    for path in sorted(lines):
        found = [(n, x) for n, text in lines[path] for x in _names(path, text)]
        roots = KNOWN_ROOTS | {x[1] for _, x in found if x[0] == "import"} if path.endswith((".py",) + _JS_EXT) else ()
        for n, text in lines[path]:  # a call is code only: the comment after # or // is cut first
            body = text.split("#" if path.endswith(".py") else "//")[0]
            found += [(n, ("api", c, None)) for c in _CALL.findall(body) if c.split(".")[0] in roots]
        for n, (kind, name, ver) in found:
            if (kind, name, ver) not in seen and NAME_RE.fullmatch(name):
                seen.add((kind, name, ver))
                out.append(dict({"kind": kind, "name": name, "path": path, "line": n}, **({"version": ver} if ver else {})))
    out.sort(key=lambda i: KINDS.index(i["kind"]))  # stable: path and line order within a kind
    return [dict(i, item=n) for n, i in enumerate(out[:INVENTORY_MAX], 1)]


def _estimate(call):
    """Input tokens, as bytes over 4 (a rough average for English and code): the call's tree, input file and diff."""
    if call["input"] is None:
        return (len(call["prompt"].encode("utf-8")) + 3) // 4
    base = os.path.dirname(os.path.dirname(call["input"]))
    total = 0
    for root, _, names in os.walk(os.path.join(base, "tree")):
        total += sum(os.path.getsize(os.path.join(root, n)) for n in names if os.path.isfile(os.path.join(root, n)))
    for f in (call["input"], os.path.join(base, "change.diff")):
        total += os.path.getsize(f) if os.path.isfile(f) else 0
    return (total + 3) // 4


def _preview(doc):
    """Lines built only from numbers, fixed words, tier, model and effort values, lane and pack names and shard ids."""
    ver, calls, caps = doc["verifier"], doc["calls"], doc["caps"]
    agents = sum(1 for c in calls if c["agent"] != "main-thread")
    verifier_cap = caps["max_verifier_calls"] if doc["mode"] == "parallel" else 0
    out = ["Tier %s, mode %s" % (doc["tier"], doc["mode"])]
    if doc["shards"] or any(x["what"].startswith("shard ") for x in doc["skipped"]):
        out.append("Shards: %d of %d kept" % (len(doc["shards"]), len(doc["shards"]) + sum(
            1 for x in doc["skipped"] if x["what"].startswith("shard "))))
    for c in calls:
        out.append("Call %s%s: %s, about %d input tokens (estimate)" % (
            c["lane"], ", shard %d" % c["shard"] if c["shard"] else "",
            "%s/%s" % (c["model"], c["effort"]) if c["model"] else "main thread", _estimate(c)))
    b = report.VERIFY_BATCH  # the cap counts ids; a call holds up to b ids of one code path, so calls lie between cap/b and cap
    out.append("Verifier ids: up to %d (calls of up to %d ids on one code path, so %d to %d calls), %s" % (
        verifier_cap, b, -(-verifier_cap // b), verifier_cap, "%s/%s" % (ver["model"], ver["effort"]) if ver["model"] else "main thread"))
    out.append("Agent calls: %d (%d lane calls plus up to %d verifier calls)" % (agents + verifier_cap, agents, verifier_cap))
    out.append("Turn ceilings: " + ", ".join("%s %d" % kv for kv in sorted(doc["max_turns"].items())))
    out.append("Packs: " + (", ".join(x["name"] for x in doc["packs"]) or "none"))
    return out + ["Estimate only: real token use varies; no prices are shown."]


BOOL_FLAGS = {"--sota": ("sota", True), "--no-sota": ("sota", False), "--serial": ("serial", True),
              "--ai": ("ai", True)}
# Lost-guarantee sentences are fixed text plus a validated model name; never repository text (T-04-19).
SERIAL_LOST = ["no independent verifier: candidates are self-scored and nothing is Blocking",
               "no lane isolation: the main thread reads target code while holding Bash",
               "Spec axis skipped: the main thread never opens inputs/spec.md",
               "SOTA skipped: no isolated web lane"]
HEURISTIC = "(heuristic: settings env may not reach this script)"


def _env_lost():
    lost = []
    if os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "").strip().lower() in ("1", "true", "yes"):
        lost.append("per-lane models ignored: CLAUDE_CODE_SUBAGENT_MODEL_FORCE is set " + HEURISTIC)
    if os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"):
        lost.append("per-lane effort ignored: CLAUDE_CODE_EFFORT_LEVEL is set " + HEURISTIC)
    return lost


def plan(run, tier, cfg, flag_tier=False, sota=None, serial=False, ai=False):
    tj = target.load_target(run)
    led = target.load_json(run, "ledger.json")
    spec = led.get("spec")
    spec_none = not isinstance(spec, dict) or spec.get("status") == "none"
    t = TIERS[tier]
    fm, fe = cfg.get("finder_model", t["finder"][0]), cfg.get("finder_effort", t["finder"][1])
    vm, ve = cfg.get("verifier_model", t["verifier"][0]), cfg.get("verifier_effort", t["verifier"][1])
    caps = {k: cfg.get(k, t[k]) for k in CAP_BOUNDS}
    lanes = [x for x in t["lanes"] if x != "sota" or sota is not False]
    if sota and "sota" not in lanes:
        lanes.append("sota")
    skipped, calls = [], []
    lost = []
    if serial:
        lanes = ["defects", "standards"]
        skipped = [{"what": "spec", "why": "serial: the main thread never opens inputs/spec.md"},
                   {"what": "sota", "why": "serial: no isolated web lane"}]
        lost = list(SERIAL_LOST)
    else:
        if not spec_none and "spec" not in lanes:  # the report must not show a clean Spec axis that never ran
            skipped.append({"what": "spec", "why": "tier " + tier})
        # shortcut: only two distinct plain aliases prove different tiers; inherit or a full ID is unknown, upgrade
        # when a model registry ships.
        if fm.lower() == vm.lower():
            lost.append("verifier not on a different tier: finders and verifier both run on " + fm)
        elif not {fm.lower(), vm.lower()} <= set(ALIASES):
            lost.append("verifier tier not checked: finders run on %s, the verifier on %s; only distinct aliases are "
                        "compared" % (fm, vm))
        lost += _env_lost()
    lines = _scan_lines(run, tj)
    inventory = _inventory(lines) if "sota" in lanes else []
    ranked = _match_packs(tj["included"], lines, ai or tier == "deep")
    packs, dropped = ranked[:caps["max_packs"]], ranked[caps["max_packs"]:]
    skipped += [{"what": "pack " + n, "why": "pack cap"} for n, _ in dropped]
    _install_packs(run, packs)
    shards = []
    if tj["kind"] in ("path", "repo"):
        stats = {p: _file_stat(tj["repo"], p) for p in tj["included"]}
        groups = _groups(tj["included"], {p: v[0] for p, v in stats.items()})
        if len(groups) > 1:  # one group is the parent run itself
            shutil.rmtree(os.path.join(run, "shards"), ignore_errors=True)
            os.makedirs(os.path.join(run, "shards"), mode=0o700)
            for i, (risk, files) in enumerate(_rank_shards(tj, led, groups, stats), 1):
                if i > caps["max_shards"]:
                    skipped.append({"what": "shard %d" % i, "why": "shard cap: %d files" % len(files), "files": len(files)})
                else:
                    shards.append({"id": i, "run": _build_shard(run, tj, i, files, ledger._stored(led), packs),
                                   "files": files, "risk": round(risk, 3)})
    for lane in lanes:
        if lane == "spec" and spec_none:
            skipped.append({"what": "spec", "why": "no spec"})
        elif lane == "sota" and not inventory:
            skipped.append({"what": "sota", "why": "no dependency or import names in the change"})
        elif lane == "sota":
            calls.append({"lane": lane, "axis": report.LANE_AXIS[lane], "agent": LANE_AGENT[lane], "model": fm, "effort": fe,
                          "input": None, "shard": None,
                          "prompt": "SOTA lane. Inventory: " + json.dumps([{k: v for k, v in i.items() if k not in ("path", "line")}
                                                                           for i in inventory], sort_keys=True, separators=(",", ":"))})
        else:
            for sh in (shards if lane in ("defects", "standards") else None) or [None]:
                path = os.path.join(sh["run"] if sh else run, "inputs", lane + ".md")
                calls.append({"lane": lane, "axis": report.LANE_AXIS[lane], "agent": "main-thread" if serial else LANE_AGENT[lane],
                              "model": None if serial else fm, "effort": None if serial else fe, "input": path,
                              "shard": sh["id"] if sh else None,
                              "prompt": "%s lane%s. Input: %s" % (lane.capitalize(), ", shard %d" % sh["id"] if sh else "", path)})
    ver = {"model": vm, "effort": ve}
    doc = {"version": 1, "tier": tier, "mode": "serial" if serial else "parallel", "calls": calls,
           "verifier": dict(ver, agent="review-code:verifier"),
           "spec_verifier": dict(ver, agent="review-code:spec-verifier"),
           "caps": dict(caps, wave_size=WAVE_SIZE),
           "max_turns": dict(MAX_TURNS),
           "packs": [{"name": n, "path": os.path.join("inputs", "packs", n + ".md"), "files": f} for n, f in packs],
           "shards": shards, "skipped": skipped, "lost_guarantees": lost,
           "sota_inventory": inventory, "preview": [], "confirm": tier == "deep" or tj["kind"] in ("path", "repo"), "ai": ai,
           "effective_overrides": {k: v for k, v in sorted(cfg.items())  # the declared default is no override
                                   if k != "default_tier" or not (flag_tier or v == DEFAULT_TIER)}}
    if serial:
        doc["verifier"] = doc["spec_verifier"] = {"agent": "main-thread", "model": None, "effort": None}
    doc["preview"] = _preview(doc)
    target.write_private(os.path.join(run, "plan.json"), json.dumps(doc, sort_keys=True, indent=2))
    return {"status": "ok", "tier": tier, "mode": doc["mode"],
            "calls": [{k: c[k] for k in ("lane", "agent", "prompt", "model", "effort", "shard", "input") if k != "input" or serial}
                      for c in calls],  # SKILL steps 7 and 8 read these instead of plan.json
            "wave_size": WAVE_SIZE, "verifier": doc["verifier"], "spec_verifier": doc["spec_verifier"], "skipped": skipped,
            "lost_guarantees": lost, "confirm": doc["confirm"], "preview": doc["preview"]}


def _usage():
    return target.Refuse("usage", "usage: plan.py plan --run DIR [--tier quick|standard|deep] "
                                  "[--sota|--no-sota] [--serial] [--ai]")


def _parse(argv):
    if argv[:2] != ["plan", "--run"] or len(argv) < 3:
        raise _usage()
    opts, i = {}, 3
    while i < len(argv):
        a = argv[i]
        if a == "--tier" and "tier" not in opts and i + 1 < len(argv) and argv[i + 1] in TIERS:
            opts["tier"] = argv[i + 1]
            i += 2
        elif a in BOOL_FLAGS and BOOL_FLAGS[a][0] not in opts:
            opts[BOOL_FLAGS[a][0]] = BOOL_FLAGS[a][1]
            i += 1
        else:
            raise _usage()
    return argv[2], opts


def main(argv):
    try:
        run, opts = _parse(argv)
        cfg = _read_config()
        tier = opts.get("tier") or cfg.get("default_tier", DEFAULT_TIER)
        out, rc = plan(run, tier, cfg, "tier" in opts, opts.get("sota"), opts.get("serial", False),
                       opts.get("ai", False)), 0
    except target.Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason, "detail": r.detail}, 1
    except (OSError, KeyError, ValueError, TypeError) as e:  # one JSON line, never a traceback
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    target.emit(out, STDOUT_KEYS)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
