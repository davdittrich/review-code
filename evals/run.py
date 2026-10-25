#!/usr/bin/env python3
"""Eval driver for review-code: seeded-defect cases, scored recall/precision, gated against a baseline.

Stdlib only. Importing this module runs nothing. See evals/README.md.
Exit: 0 ok, 1 a RED gate / lint failure / case without a document, 2 usage or cost abort.
"""
import argparse
import datetime
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CASES = os.path.join(HERE, "cases")
BASELINE = os.path.join(HERE, "baseline.json")
OUT = os.path.join(HERE, ".out")

COUNTED = ("blocking", "required", "suggestion")  # nit and noted never count
TOL = 2
GREEN_P, RED_P = 0.25, 0.10
LANES = ("defects", "standards", "spec")
LEAK = re.compile(r"\b(bug|seeded|fixme|defect)", re.I)
# stated heuristic when the baseline holds no cost: USD per run
HEURISTIC_USD = {"full": 5.0, "cheap": 1.0}


# ---- cases -----------------------------------------------------------------------------------

def load_cases(only=None):
    out = {}
    for fn in sorted(os.listdir(CASES)):
        if fn.endswith(".json") and (only is None or fn[:-5] == only):
            with open(os.path.join(CASES, fn), encoding="utf-8") as fh:
                out[fn[:-5]] = json.load(fh)
    return out


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _loc_errors(case, loc, what):
    errs = []
    if not isinstance(loc, dict) or not isinstance(loc.get("path"), str) or not _is_int(loc.get("line")):
        return ["%s: needs path and integer line" % what]
    txt = case["change_files"].get(loc["path"], case["base_files"].get(loc["path"]))
    if txt is None:
        return ["%s: path %s is in neither base_files nor change_files" % (what, loc["path"])]
    end = loc.get("end_line", loc["line"])
    if not _is_int(end) or end < loc["line"] or loc["line"] < 1 or end > len(txt.split("\n")):
        errs.append("%s: line range %s-%s is outside %s" % (what, loc["line"], end, loc["path"]))
    return errs


def check_case(case):
    """Schema errors of one fixture (empty list = valid)."""
    keys = {"prompt", "base_files", "change_files", "defects", "decoys", "accept", "change_message"}
    if not isinstance(case, dict):
        return ["case must be an object"]
    errs = ["unknown key %s" % k for k in case if k not in keys]
    if not isinstance(case.get("prompt"), str) or not case["prompt"]:
        errs.append("prompt must be a non-empty string")
    if "change_message" in case and not isinstance(case["change_message"], str):
        errs.append("change_message must be a string")
    for k in ("base_files", "change_files"):
        v = case.get(k)
        if not isinstance(v, dict) or not all(isinstance(a, str) and isinstance(b, str) for a, b in v.items()):
            errs.append("%s must map path to text" % k)
    if errs or not case["change_files"]:
        return errs or ["change_files must not be empty"]
    for k in ("defects", "decoys", "accept"):
        if not isinstance(case.get(k, []), list):
            errs.append("%s must be a list" % k)
    if errs:
        return errs
    seen = set()
    for d in case.get("defects", []):
        if not isinstance(d, dict) or not isinstance(d.get("id"), str) or d["id"] in seen:
            errs.append("defect needs a unique string id")
            continue
        seen.add(d["id"])
        errs += _loc_errors(case, d, "defect " + d["id"])
        if d.get("lane") not in LANES:
            errs.append("defect %s: lane must be one of %s" % (d["id"], "/".join(LANES)))
        cats = d.get("categories")
        if cats is not None and not (isinstance(cats, list) and cats and all(isinstance(c, str) for c in cats)):
            errs.append("defect %s: categories must be a non-empty list of strings" % d["id"])
        for a in d.get("also", []):
            errs += _loc_errors(case, a, "defect %s also" % d["id"])
    for k in ("decoys", "accept"):
        for i, d in enumerate(case.get(k, [])):
            errs += _loc_errors(case, d, "%s[%d]" % (k, i))
    return errs


def lint_case(name, case):
    """Schema errors plus answer-leaking words near planted lines."""
    errs = [] if re.match(r"^[a-z0-9][a-z0-9-]*$", name) else ["case name must be lowercase-hyphen"]
    errs += check_case(case)
    if errs:
        return errs
    if LEAK.search(case["prompt"]) or LEAK.search(case.get("change_message", "")):
        errs.append("prompt or change_message names the answer")
    for d in case.get("defects", []):
        for loc in [d] + d.get("also", []):
            txt = case["change_files"].get(loc["path"], case["base_files"].get(loc["path"])).split("\n")
            lo, hi = max(0, loc["line"] - 1 - 3), loc.get("end_line", loc["line"]) + 3
            for i in range(lo, min(hi, len(txt))):
                if LEAK.search(txt[i]):
                    errs.append("%s:%d near defect %s: leaking word" % (loc["path"], i + 1, d["id"]))
    for i, dc in enumerate(case.get("decoys", [])):
        for d in case.get("defects", []):
            if any(_overlaps(dict(dc, end_line=dc.get("end_line", dc["line"])), l) for l in [d] + d.get("also", [])):
                errs.append("decoy[%d] lies within %d lines of defect %s: a finding there would score as a hit" % (i, TOL, d["id"]))
    return errs


# ---- scoring ---------------------------------------------------------------------------------

def _overlaps(f, loc):
    if f.get("path") != loc["path"] or not _is_int(f.get("line")):
        return False
    lo, hi = f["line"], f["end_line"] if _is_int(f.get("end_line")) else f["line"]
    return lo <= loc.get("end_line", loc["line"]) + TOL and hi >= loc["line"] - TOL


def score(doc, case, lane=None):
    """Score one review document. None when there is no document (an error run).

    lane: only defects of that lane count; findings on the others are neutral."""
    if not isinstance(doc, dict) or not isinstance(doc.get("findings"), list):
        return None
    defects = [d for d in case.get("defects", []) if lane in (None, d["lane"])]
    neutral = list(case.get("accept", []))
    if lane:
        neutral += [x for d in case.get("defects", []) if d["lane"] != lane for x in [d] + d.get("also", [])]
    hit = {d["id"]: False for d in defects}
    fp_decoy = fp_other = 0
    for f in doc["findings"]:
        if not isinstance(f, dict) or f.get("severity") not in COUNTED:
            continue
        d = next((d for d in defects if any(_overlaps(f, l) for l in [d] + d.get("also", []))
                  and (not d.get("categories") or f.get("category") in d["categories"])), None)
        if d:
            hit[d["id"]] = True  # a second finding on the same defect collapses into the first
        elif any(_overlaps(f, n) for n in neutral):
            continue
        elif any(_overlaps(f, c) for c in case.get("decoys", [])):
            fp_decoy += 1
        else:
            fp_other += 1
    tp = sum(hit.values())
    return {"hit": hit, "tp": tp, "fp": fp_decoy + fp_other, "fp_decoy": fp_decoy, "fp_other": fp_other}


def aggregate(scores):
    """Pool the per-run scores of one case; None entries are error runs."""
    ok = [s for s in scores if s]
    ids = sorted(ok[0]["hit"]) if ok else []
    per = {i: [sum(s["hit"][i] for s in ok), len(ok)] for i in ids}
    tp, fp = sum(s["tp"] for s in ok), sum(s["fp"] for s in ok)
    return {"n_runs": len(ok), "errors": len(scores) - len(ok), "hits": tp, "n": len(ids) * len(ok),
            "defects": per, "tp": tp, "fp": fp, "findings": tp + fp,
            "recall": tp / (len(ids) * len(ok)) if ids and ok else None,
            "precision": tp / (tp + fp) if tp + fp else None}


# ---- gate ------------------------------------------------------------------------------------

def gate_p(hits_new, n_new, hits_base, n_base):
    """P(X <= hits_new | n_new, p0), p0 = (hits_base + 1) / (n_base + 2): the chance of a result this bad if nothing changed."""
    p0 = (hits_base + 1) / (n_base + 2)
    return min(1.0, sum(math.comb(n_new, x) * p0 ** x * (1 - p0) ** (n_new - x) for x in range(hits_new + 1)))


def status_of(p):
    return "RED" if p < RED_P else "AMBER" if p < GREEN_P else "GREEN"


def gate(agg, case, base, lane=None):
    """(status, p) of one aggregated case against its baseline entry; p is None when not gated by recall."""
    if agg["n_runs"] == 0:
        return "ERROR", None
    if not any(d for d in case.get("defects", []) if lane in (None, d["lane"])):
        return ("RED" if agg["fp"] else "AMBER" if lane else "GREEN"), None  # no-defect case: zero findings in every run; a cheap run is never above AMBER
    if not base:
        return "NEW", None
    if lane:  # compare only the lane's defects against the baseline's per-defect counts
        per = [base["defects"][i] for i in agg["defects"] if i in base["defects"]]
        hb, nb = sum(k for k, _ in per), sum(n for _, n in per)
    else:
        hb, nb = base["hits"], base["n"]
    if nb == 0:
        return "NEW", None
    p = gate_p(agg["hits"], agg["n"], hb, nb)
    st = status_of(p)
    return ("AMBER" if lane and st == "GREEN" else st), p  # a cheap run is indicative, never above AMBER


# ---- documents -------------------------------------------------------------------------------

def extract_doc(text):
    """The last JSON object in text that looks like the review document, else None."""
    dec, found = json.JSONDecoder(), None
    for m in re.finditer(r"\{", text or ""):
        try:
            obj, _ = dec.raw_decode(text, m.start())
        except ValueError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("findings"), list) and "schema_version" in obj:
            found = obj
    return found


# ---- baseline --------------------------------------------------------------------------------

def tiers_hash():
    """Hash of plan.py TIERS. plan.py imports its sibling scripts, so their dir is on sys.path for the load; a failure raises."""
    d = os.path.join(ROOT, "skills", "review-code", "scripts")
    sys.path.insert(0, d)
    try:
        spec = importlib.util.spec_from_file_location("_plan", os.path.join(d, "plan.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    finally:
        sys.path.remove(d)
    return hashlib.sha256(json.dumps(mod.TIERS, sort_keys=True, default=str).encode()).hexdigest()[:16]


def git_sha():
    p = subprocess.run(["git", "-C", ROOT, "rev-parse", "--short", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    return p.stdout.decode().strip() or None


def claude_version():
    try:
        return subprocess.run(["claude", "--version"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL).stdout.decode().strip() or None
    except OSError:
        return None


def load_baseline():
    try:
        with open(BASELINE, encoding="utf-8") as fh:
            b = json.load(fh)
    except (OSError, ValueError):
        b = {}
    b.setdefault("version", 1)
    b.setdefault("meta", {})
    b.setdefault("cases", {})
    return b


def meta_warnings(base):
    """Per case: its own meta against now. A case without one (written before meta went per case, and --accept no longer
    refreshes the global meta) is told to re-accept, never compared to the global values."""
    now = {"tiers_hash": tiers_hash(), "claude_version": claude_version()}
    out = []
    for n, c in sorted(base["cases"].items()):
        m = c.get("meta")
        if not m:
            out.append("baseline %s predates per-case meta: re-accept it" % n)
            continue
        out += ["baseline %s %s %r differs from now %r" % (n, k, m.get(k), v) for k, v in now.items()
                if m.get(k) is not None and m.get(k) != v]
    return out


# ---- ADAPTER: the only code that touches the live `claude` CLI (all of it UNVERIFIED, see README) --------

def run_claude(repo, plugin, case, lean, budget):
    """Run one headless review in repo; returns (text, cost_usd or None). Raises on a failed or unparsable call."""
    flags = "--json --own" + (" --lean" if lean else "")
    prompt = ("%s Use the review-code skill on the last commit (HEAD~1..HEAD) with %s. "
              "Reply with the JSON review document it prints and nothing else." % (case["prompt"], flags))
    # least privilege: the skill pre-approves its own scripts (SKILL.md allowed-tools); dontAsk denies everything else,
    # and the lanes only need to read. UNVERIFIED against a live run: --allowedTools is variadic, so it goes last.
    cmd = ["claude", "-p", prompt, "--plugin-dir", plugin, "--output-format", "json", "--no-session-persistence",
           "--permission-mode", "dontAsk"]
    if budget:
        cmd += ["--max-budget-usd", "%.2f" % budget]
    cmd += ["--allowedTools", "Read", "Grep", "Glob", "Agent"]
    p = subprocess.run(cmd, cwd=repo, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=1800)
    out = p.stdout.decode("utf-8", "replace")
    try:
        obj = json.loads(out)
        text, cost = obj.get("result", ""), obj.get("total_cost_usd")
    except ValueError:
        text, cost = out, None
    if p.returncode != 0 and not text:
        raise RuntimeError("claude exited %d: %s" % (p.returncode, p.stderr.decode("utf-8", "replace")[-300:]))
    return text if isinstance(text, str) else json.dumps(text), cost if isinstance(cost, (int, float)) else None


# ---- running ---------------------------------------------------------------------------------

def _git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="eval", GIT_AUTHOR_EMAIL="eval@example.invalid",
               GIT_COMMITTER_NAME="eval", GIT_COMMITTER_EMAIL="eval@example.invalid", GIT_CONFIG_GLOBAL="/dev/null")
    subprocess.run(["git", "-C", repo] + list(args), check=True, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _write(repo, files):
    for rel, txt in files.items():
        path = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(txt)


def build_repo(case, repo):
    """base commit, then the change commit (HEAD~1..HEAD is the reviewed change)."""
    _git(repo, "init", "-q", "-b", "main")
    _write(repo, case["base_files"] or {"README.md": "base\n"})
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _write(repo, case["change_files"])
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", case.get("change_message", "change"))


def copy_plugin(dest):
    """Tracked plugin files without evals/ (lanes have Read: seeded data must not be reachable) and tests/."""
    p = subprocess.run(["git", "-C", ROOT, "ls-files", "-z"], stdout=subprocess.PIPE, check=True)
    for rel in p.stdout.decode().split("\0"):
        if rel and not rel.startswith(("evals/", "tests/")) and os.path.isfile(os.path.join(ROOT, rel)):
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            shutil.copy2(os.path.join(ROOT, rel), os.path.join(dest, rel))


def _known_cost(c):
    """A baseline cost is usable only when positive; None and 0.0 both mean unknown."""
    return isinstance(c, (int, float)) and not isinstance(c, bool) and c > 0


def estimate(names, runs, base, cheap):
    tot = 0.0
    for n in names:
        c = (base["cases"].get(n) or {}).get("cost")
        tot += runs * (c if _known_cost(c) and not cheap else HEURISTIC_USD["cheap" if cheap else "full"])
    return tot


def report(names, cases, scores_by, base, lane):
    """Print one line per case; returns the worst-status exit code."""
    code = 0
    for n in names:
        agg = aggregate(scores_by[n])
        st, p = gate(agg, cases[n], base["cases"].get(n), lane)
        rec = "-" if agg["recall"] is None else "%d/%d" % (agg["hits"], agg["n"])
        prec = "-" if agg["precision"] is None else "%.2f" % agg["precision"]
        per = " ".join("%s=%d/%d" % (i, k, m) for i, (k, m) in sorted(agg["defects"].items()))
        print("%-6s %-24s recall %-6s precision %-5s fp %d p=%s errors=%d  %s" % (
            st, n, rec, prec, agg["fp"], "-" if p is None else "%.3f" % p, agg["errors"], per))
        if st in ("RED", "ERROR"):
            code = 1
    return code


def rescore(path, names, cases, lane):
    out = {}
    for n in names:
        scores = []
        d = os.path.join(path, n)
        for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
            with open(os.path.join(d, fn), encoding="utf-8") as fh:
                raw = json.load(fh)
            scores.append(score(extract_doc(raw.get("text", "")), cases[n], lane))
        out[n] = scores
    return out


def run_cases(names, cases, runs, lane, max_cost, stamp):
    """The paid loop: (scores_by, costs) per case, or None when --max-cost-usd was exceeded. Raw documents go to OUT/stamp."""
    scores_by, costs, spent, k = {}, {}, 0.0, 0
    with tempfile.TemporaryDirectory() as tmp:
        plugin = os.path.join(tmp, "plugin")
        copy_plugin(plugin)
        for n in names:
            scores_by[n], costs[n] = [], 0.0
            for i in range(runs):
                k += 1
                repo = os.path.join(tmp, "r%d" % k)  # neutral: the skill writes this path into lane inputs
                os.makedirs(repo)
                build_repo(cases[n], repo)
                raw = {"case": n, "run": i}
                try:
                    raw["text"], raw["cost"] = run_claude(repo, plugin, cases[n], bool(lane),
                                                          None if max_cost is None else max(max_cost - spent, 0.01))
                except Exception as e:  # an errored run is excluded from n and flagged
                    raw["text"], raw["cost"], raw["error"] = "", None, str(e)
                d = os.path.join(OUT, stamp, n)
                os.makedirs(d, exist_ok=True)
                with open(os.path.join(d, "run%d.json" % i), "w", encoding="utf-8") as fh:
                    json.dump(raw, fh, indent=1)
                scores_by[n].append(score(extract_doc(raw["text"]), cases[n], lane))
                spent += raw["cost"] or 0.0
                costs[n] = None if costs[n] is None or raw["cost"] is None else costs[n] + raw["cost"]
                if max_cost is not None and spent > max_cost:
                    print("aborted: spent $%.2f over --max-cost-usd %.2f; raw documents in %s" % (spent, max_cost, os.path.join(OUT, stamp)), file=sys.stderr)
                    return None
    return scores_by, costs


def write_baseline(base, names, scores_by, costs):
    """Replace the baseline entries of names; each carries the meta of the run that produced it."""
    meta = {"git_sha": git_sha(), "claude_version": claude_version(), "tiers_hash": tiers_hash()}
    for n in names:
        agg = aggregate(scores_by[n])
        agg["cost"] = None if costs[n] is None else costs[n] / agg["n_runs"]  # None: some run's cost was unknown
        agg["meta"] = meta
        agg.pop("recall"), agg.pop("precision")
        base["cases"][n] = agg
    with open(BASELINE, "w", encoding="utf-8") as fh:
        json.dump(base, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print("baseline written: %s" % BASELINE)


def main(argv=None):
    ap = argparse.ArgumentParser(description="review-code eval driver (paid: runs the skill through `claude -p`)")
    ap.add_argument("command", nargs="?", choices=["lint"])
    ap.add_argument("--cheap", action="store_true", help="tier --lean once per case, Standards defects only; indicative")
    ap.add_argument("--case")
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--yes", action="store_true", help="skip the cost question")
    ap.add_argument("--max-cost-usd", type=float)
    ap.add_argument("--rescore", metavar="DIR", help="score saved documents again, free")
    ap.add_argument("--accept", action="store_true", help="write this run to evals/baseline.json")
    a = ap.parse_args(argv)
    cases = load_cases(a.case)
    if a.case and a.case not in cases:
        print("no such case: %s" % a.case, file=sys.stderr)
        return 2
    if a.command == "lint":
        bad = 0
        for n, c in cases.items():
            for e in lint_case(n, c):
                print("FAIL %s: %s" % (n, e))
                bad += 1
        print("lint: %d case(s), %d problem(s)" % (len(cases), bad))
        return 1 if bad else 0
    lane = "standards" if a.cheap else None
    names = [n for n, c in cases.items() if not lane or not c.get("defects") or any(d["lane"] == lane for d in c["defects"])]
    base = load_baseline()
    for w in meta_warnings(base):
        print("warning: " + w, file=sys.stderr)
    runs = 1 if a.cheap else a.runs
    if a.rescore and a.accept:
        print("--accept needs a paid run, not --rescore", file=sys.stderr)
        return 2
    if a.rescore:
        scores_by = rescore(a.rescore, names, cases, lane)
        costs = None
    else:
        if a.accept and a.cheap:
            print("--accept needs a full run, not --cheap", file=sys.stderr)
            return 2
        if shutil.which("claude") is None:
            print("claude not on PATH", file=sys.stderr)
            return 2
        est = estimate(names, runs, base, a.cheap)
        print("%d case(s) x %d run(s): estimated cost about $%.2f%s" % (
            len(names), runs, est, "" if not a.cheap and all(_known_cost(base["cases"].get(n, {}).get("cost")) for n in names) else " (heuristic, no baseline cost)"))
        if not a.yes:
            if not sys.stdin.isatty():
                print("stdin is not a terminal: pass --yes to run", file=sys.stderr)
                return 2
            if input("Run? [y/N] ").strip().lower() != "y":
                return 2
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        res = run_cases(names, cases, runs, lane, a.max_cost_usd, stamp)
        if res is None:
            return 2
        scores_by, costs = res
        print("raw documents: %s" % os.path.join(OUT, stamp))
    code = report(names, cases, scores_by, base, lane)
    if a.accept and not a.rescore:
        broken = [n for n in names if aggregate(scores_by[n])["errors"] or not aggregate(scores_by[n])["n_runs"]]
        if broken:
            print("--accept refused: errored or missing runs in %s; nothing written" % ", ".join(broken), file=sys.stderr)
            return 2
        write_baseline(base, names, scores_by, costs)
    return code


if __name__ == "__main__":
    sys.exit(main())
