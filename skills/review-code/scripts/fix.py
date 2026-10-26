"""Apply verified findings' fixes to the user's tree, one at a time, behind a green test command.

Stdlib only, Python 3.9 grammar. The plugin's only script that lets an agent edit the user's tree: the fixer
agent holds Edit, this script owns everything around it (plan, baseline, snapshot, tests, revert, report).
Runs the stored test command only after ledger.trust_gate passes. Creates no commit, ref or stash entry.
Prints one JSON line; never a traceback.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import bisect
import hashlib
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
from typing import List, Optional, Tuple

import ledger
import report
import repro
import sanitize
import target
import weaken

TIMEOUT = 540  # seconds; read at call time. references/fix.md sets a 600000 ms Bash call: 60 s left for the kill and restore
MAX_CHANGED = 60  # changed lines per fix: the + and - lines of a zero-context diff
TIERS = {"simple": ("sonnet", "medium"), "complex": ("opus", "high")}
_MANIFEST = re.compile(
    r"(?:requirements.*\.txt|constraints.*\.txt|setup\.py|go\.mod|go\.work|Gemfile|.*\.gemspec|Pipfile|"
    r"build\.gradle|build\.gradle\.kts|settings\.gradle|settings\.gradle\.kts|pom\.xml|NAMESPACE|.*\.csproj|"
    r".*\.fsproj|.*\.vbproj|Directory\.Build\.props|Directory\.Packages\.props|packages\.config|composer\.json|"
    r"Jenkinsfile|CMakeLists\.txt|.*\.cabal|mix\.exs)\Z")
_RUNNER = re.compile(
    r"(?:(?:jest|vitest|karma|webpack|babel|playwright|cypress|mocha|ava)\.conf(?:ig)?(?:\.\w+)+|"
    r"(?:jest\.setup|setupTests)(?:\.\w+)+|noxfile\.py|Rakefile|build\.rs|pytest\.ini|tox\.ini)\Z")
# multi-purpose files: they judge the tests only when the test command names them (cmd_files), not by basename
OUTCOMES = ("fixed", "declined", "no-change", "stale", "reverted-violation", "reverted-too-large", "reverted-tests",
            "reverted-timeout", "reverted-weakened", "reverted-interrupt", "tampered", "bad-path", "test-path", "not-covered",
            "not-reached")
RECOVERABLE = ("reverted-tests", "reverted-too-large", "reverted-timeout", "reverted-weakened")  # restored, and the tree is trusted: the run goes on
HALTING = ("reverted-violation", "reverted-interrupt", "tampered")  # the tree can no longer be trusted: the run stops
REVERTED = RECOVERABLE + HALTING  # every failed outcome; all are restored except tampered, which leaves the tree as found
STDOUT_KEYS = ("status", "reason", "detail", "digest", "apply", "protected", "test_files", "skipped", "id", "brief", "path", "line", "model",
               "effort", "agent", "seal", "outcome", "exit", "tail", "fixed", "reverted", "declined", "halted", "verified", "not_reached")
HEX = re.compile(r"[0-9a-f]{64}\Z")
USAGE = ("usage: fix.py plan --run DIR [--fix-tests] (test command on stdin, one word per line) | baseline --run DIR --digest HEX | "
         "begin --run DIR | finish --run DIR --seal HEX --status applied|declined [--code CODE] [--line N] | "
         "report --run DIR")
DECLINE_CODES = ("line-mismatch", "quote-not-unique", "scope-multi-file", "needs-context")


_e = sanitize.escape_line


def _fixdir(run: str) -> str:
    return os.path.join(run, "fix")


def _json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


STATE_KEYS = ("version", "digest", "refs", "git_files", "done", "accepted", "untracked", "in_flight", "halted",
              "side_effects")  # what baseline writes


def _typed(st) -> bool:
    """state.json holds the types fix.py writes, so no consumer meets an AttributeError or KeyError."""
    if not isinstance(st, dict) or any(k not in st for k in STATE_KEYS):
        return False
    cur = st["in_flight"]
    return (all(isinstance(st[k], str) for k in ("digest", "refs")) and isinstance(st["version"], int)
            and (st["halted"] is None or isinstance(st["halted"], str))  # baseline writes None or a reason
            and all(isinstance(st[k], dict) for k in ("done", "accepted"))
            and all(isinstance(v, dict) for v in st["done"].values())
            and all(isinstance(st[k], list) for k in ("untracked", "side_effects", "git_files"))
            and (cur is None or (isinstance(cur, dict) and isinstance(cur.get("id"), str)
                                 and isinstance(cur.get("path"), str) and isinstance(cur.get("mode"), int))))


def _state(run: str) -> dict:
    try:
        st = _json(os.path.join(_fixdir(run), "state.json"))
    except FileNotFoundError:
        raise target.Refuse("no-baseline")
    except (ValueError, RecursionError):  # ValueError includes a decode error
        st = None
    if not _typed(st):
        raise target.Refuse("tampered", "fix/state.json is unreadable, so the run directory changed while a fixer ran; "
                            "the tree is left as it is and no git command ran; check .git/config and git status by "
                            "hand; the snapshots are the .pre files in the run's fix/ directory")
    return st


def _save(run: str, st: dict) -> None:
    _restore(os.path.join(_fixdir(run), "state.json"), json.dumps(st, sort_keys=True, indent=2).encode("utf-8"),
             0o600)  # temp file then replace: an interruption never leaves a partial state.json


def _tier(f: dict) -> Tuple[str, str]:
    hard = (f["category"] in report.BLOCKING or f["severity"] == "blocking" or f["end_line"] - f["line"] >= 5
            or len(f["fix"]) > 300)
    return TIERS["complex" if hard else "simple"]


def _protected(path: str) -> bool:
    """Paths flagged in the preview and the brief as the riskiest edits: dot components, lockfiles, manifests, config and CI files."""
    parts = path.split("/")
    base = parts[-1]
    return (any(p.startswith(".") for p in parts) or base in target._LOCKFILES or base.endswith(".lock")
            or path.endswith(ledger._CFG_EXT) or bool(ledger._CFG_ROOT.match(base)) or bool(_MANIFEST.match(base))
            or path in ledger._CI_FILES)


def _judges(path: str, cmd_files: set) -> bool:
    """Files that decide whether the tests pass: test files, fixtures, runner configs, the test command's own script.
    A spec/ directory counts only at the repo root (RSpec), so src/spec/parser.py stays fixable."""
    d, _, base = path.rpartition("/")
    dirs = d.split("/") if d else []
    return (path in cmd_files or ledger._is_fixture(path) or bool(_RUNNER.match(base)
            or ledger._TEST_NAME.match(base) or (ledger._TEST_DIRS - {"spec"}) & set(dirs)) or dirs[:1] == ["spec"])


def _editable_test(path: str) -> bool:
    """The part of what judges the tests that --fix-tests may edit: files named like tests. Fixtures, runner configs, the
    test command's script and any helper in a test directory change what the judge runs, so they stay protected."""
    base = path.rpartition("/")[2]
    return bool(ledger._TEST_NAME.match(base)) and not ledger._is_fixture(path) and not _RUNNER.match(base)


def _covered(path: str, cmd_files: set, known: set) -> bool:
    """Is a test file one the post-fix run can reach: in the target or the ledger's tests, or a command word names it or a
    directory above it. shortcut: names, not an execution trace; a command that ignores a named file still passes."""
    return (target.shown(path) in known or path in cmd_files
            or any(w == "." or path.startswith(w + "/") for w in cmd_files))


def _live(repo: str, path) -> Optional[str]:
    """Real path of a tracked regular file inside repo reached without a symlink, else None."""
    if not isinstance(path, str) or not path or "\0" in path or os.path.isabs(path):
        return None
    norm = os.path.normpath(path)
    if norm == ".." or norm.startswith("../"):
        return None
    joined = os.path.join(os.path.realpath(repo), norm)
    if os.path.realpath(joined) != joined or not os.path.isfile(joined) or os.path.islink(joined):
        return None
    try:
        listed = target.git_bytes(["ls-files", "-z", "--", norm], repo, check=False)
        return joined if listed == norm.encode("utf-8") + b"\0" else None
    except UnicodeError:
        return None


def _locate(repo: str, path, quote) -> Tuple[Optional[str], Optional[int], Optional[str]]:
    """(real path, line, None) when the finding's file is live and its quote occurs once; else (real or None, None, the
    skip reason): bad-path for a file that is not live (or has a .git component), stale for a quote that moved or went."""
    real = None if ".git" in str(path).split("/") else _live(repo, path)
    line = None if real is None else _relocate(_read(real).decode("utf-8", "replace"), quote)
    return real, line, "bad-path" if real is None else "stale" if line is None else None


def _relocate(text: str, quote) -> Optional[int]:
    """1-based line where the whitespace-folded quote starts, when it occurs exactly once in the folded text."""
    q = report._ws(quote) if isinstance(quote, str) else ""
    if not q:
        return None
    toks = [(m.start(), m.group()) for m in re.finditer(r"\S+", text)]
    folded = " ".join(t for _, t in toks)
    offs, o = [], 0
    for _, t in toks:
        offs.append(o)
        o += len(t) + 1
    first = folded.find(q)
    if first < 0 or folded.find(q, first + 1) >= 0:
        return None
    return text.count("\n", 0, toks[bisect.bisect_right(offs, first) - 1][0]) + 1


def _read(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def _changes(repo: str) -> List[Tuple[str, str]]:
    """(XY, path) per entry of `git status --porcelain=v1 -z`; a rename or copy yields both its paths."""
    parts = target.git_bytes(["status", "--porcelain=v1", "-z", "--untracked-files=all"], repo).split(b"\0")
    out, i = [], 0
    while i < len(parts):
        ent = parts[i]
        i += 1
        if len(ent) < 4:
            continue
        xy = ent[:2].decode("ascii", "replace")
        out.append((xy, ent[3:].decode("utf-8", "surrogateescape")))
        if ("R" in xy or "C" in xy) and i < len(parts):
            out.append((xy, parts[i].decode("utf-8", "surrogateescape")))
            i += 1
    return out


def _git_files(repo: str) -> list:
    """The repository's config, attributes and exclude files, named exactly (a CR or 0xff in the git directory's name)."""
    return [os.path.join(repo, target._rev_path("--git-path", repo, p)) for p in ("config", "info/attributes", "info/exclude")]


def _refs(repo: str) -> str:
    head = target.git(["rev-parse", "HEAD"], repo).strip()
    listing = target.git_bytes(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags", "refs/stash"],
                               repo)  # bytes: two non-UTF-8 ref names must not both read U+FFFD
    return hashlib.sha256(head.encode() + b"\n" + listing).hexdigest()


def _seal(run: str, fid: str) -> str:
    """Hash of every file the fixer could rewrite to subvert the revert. Plain reads only: no git call."""
    fd = _fixdir(run)
    paths = [os.path.join(fd, "plan.json"), os.path.join(fd, "state.json"), os.path.join(fd, fid + ".pre"),
             os.path.join(run, "target.json"), os.path.join(run, "ledger.json")]
    try:
        files = _json(paths[1]).get("git_files", [])
    except (OSError, ValueError, AttributeError):
        files = []
    h = hashlib.sha256()
    for p in paths + [x for x in files if isinstance(x, str)]:
        try:
            data = _read(p)
        except OSError:
            data = None
        h.update(p.encode("utf-8", "replace") + b"\0")
        h.update(b"absent\0" if data is None else b"%d\0" % len(data) + data + b"\0")
    return h.hexdigest()


def _restore(path: str, data: bytes, mode: int) -> None:
    """Put data back byte-exact: temp file in the same directory, then an atomic replace."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    target.write_atomic(path, data, mode)


def _quarantine(run: str, key: str, repo: str, paths: List[str], move: bool) -> List[str]:
    """Keep bytes fix.py is about to overwrite or the fixer left behind: copy (move false) or move (move true)
    each path into fix/quarantine/<key>/<relpath>, directories 0700. A repeat of one path gets .1, .2 ..."""
    top, out = os.path.realpath(repo), []
    for rel in paths:
        src, parts = os.path.join(top, rel), ["quarantine", key] + rel.split("/")
        isdir = os.path.isdir(src) and not os.path.islink(src)
        if os.path.isabs(rel) or ".." in parts or not os.path.lexists(src) or isdir:
            continue
        d = _fixdir(run)
        for part in parts[:-1]:
            d = os.path.join(d, part)
            try:
                os.mkdir(d, 0o700)
            except FileExistsError:
                pass
            os.chmod(d, 0o700)
        dst, n = os.path.join(d, parts[-1]), 0
        while os.path.lexists(dst):
            n += 1
            dst = os.path.join(d, "%s.%d" % (parts[-1], n))
        if move:
            shutil.move(src, dst)
        else:
            shutil.copy2(src, dst, follow_symlinks=False)
        out.append(rel)
    return out


def _undo(run: str, key: str, repo: str, writes: dict, head: List[str], idx: List[str], stray: List[str]) -> None:
    """The one restore path. Quarantine the current bytes first, then: writes {rel: (bytes, mode)} are put back,
    idx lists paths whose index entry returns to HEAD, head lists tracked paths git restores from HEAD (index and
    tree), stray lists new untracked paths that move into the quarantine."""
    top = os.path.realpath(repo)
    _quarantine(run, key, repo, list(writes) + head, False)
    for rel, (data, mode) in writes.items():
        _restore(os.path.join(top, rel), data, mode)
    if idx:
        target.git_bytes(["restore", "--source=HEAD", "--staged", "--"] + idx, repo)
    if head:
        target.git_bytes(["restore", "--source=HEAD", "--staged", "--worktree", "--"] + head, repo)
    _quarantine(run, key, repo, stray, True)


def _names(paths: List[str]) -> str:
    shown = ", ".join(sanitize.escape_for_code(p)[0] for p in paths[:20])
    return shown + (" and %d more" % (len(paths) - 20) if len(paths) > 20 else "")


def _changed_lines(a: bytes, b: bytes) -> int:
    """+ and - lines of a zero-context diff."""
    return sum(len(x) for x in weaken.changed(a, b))


def _tests(run: str, st: dict) -> dict:
    """Run the stored command once in the checkout `_root` returns (the worktree holding the reviewed head; else the repository top). pass: exit 0 and no timeout or launch error."""
    raw = _read(os.path.join(_fixdir(run), "plan.json"))
    if hashlib.sha256(raw).hexdigest() != st["digest"]:
        raise target.Refuse("digest-mismatch")
    command = json.loads(raw.decode("utf-8"))["command"]
    ledger.trust_gate(run, command)
    home, tmp = (os.path.join(_fixdir(run), n) for n in ("home", "tmp"))
    for d in (home, tmp):
        shutil.rmtree(d, ignore_errors=True)
        os.mkdir(d, 0o700)
    env = {k: os.environ[k] for k in repro.ENV_KEEP if k in os.environ}
    env.update(HOME=home, TMPDIR=tmp)
    try:
        res = repro._execute(command, _root(target.load_target(run)), env, "", timeout=TIMEOUT)  # empty id: only the exit counts
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    tail = sanitize.escape_for_code(res["tail"])[0]
    if res["outcome"] in ("timeout", "error"):
        return {"outcome": res["outcome"], "exit": res["exit"], "tail": tail}
    return {"outcome": "pass" if res["exit"] == 0 else "fail", "exit": res["exit"], "tail": tail}


def _root(tj: dict) -> str:
    """The checkout holding the reviewed head: where fix mode tests and edits. Runs from before the key fall back to repo."""
    return tj.get("root") or tj["repo"]


def _elsewhere(tj: dict) -> bool:
    """The reviewed head sits in a worktree other than the repository top (realpath: root is stored resolved)."""
    return os.path.realpath(_root(tj)) != os.path.realpath(tj["repo"])


def _moved(tj: dict, st: dict) -> Optional[str]:
    if target.git(["rev-parse", "HEAD"], _root(tj)).strip() != tj["head_sha"]:
        return "head-moved"
    return "refs-moved" if _refs(_root(tj)) != st["refs"] else None


def _kept(top: str, xy: str, p: str, a: dict) -> bool:
    """An accepted file still holds exactly the bytes its fix was accepted with."""
    try:
        return xy == " M" and hashlib.sha256(_read(os.path.join(top, p))).hexdigest() == a["sha"]
    except OSError:
        return False


def _post(run: str, top: str, p: str, a: dict) -> Tuple[bytes, int]:
    try:
        mode = stat.S_IMODE(os.stat(os.path.join(top, p)).st_mode)
    except OSError:
        mode = 0o644  # shortcut: a deleted accepted file loses its exec bit, upgrade by recording the mode
    return _read(os.path.join(_fixdir(run), a["id"] + ".post")), mode


def _sweep(run: str, tj: dict, st: dict, key: str, entry: Optional[Tuple[str, bytes]] = None) -> Optional[str]:
    """After a test run. Tracked files the tests changed go back (current bytes quarantined first, accepted fixes
    from their .post, the rest from HEAD) and are listed in side_effects; new untracked files stay and are listed
    in untracked. entry (path, bytes) is the fix under test. Answers head-moved, refs-moved or "tests changed
    <path>" when the run broke the fix under test, else None."""
    repo = _root(tj)
    top = os.path.realpath(repo)
    moved = _moved(tj, st)
    if moved:
        return moved
    writes, head, idx, side = {}, [], [], []
    for xy, p in _changes(repo):
        a = st["accepted"].get(p)
        if xy == "??":
            if p not in st["untracked"]:
                st["untracked"].append(p)
        elif entry and p == entry[0]:
            if xy != " M":  # staged by the tests: the bytes are judged below, the index goes back now
                idx.append(p)
                side.append(p)
        elif a is None or not _kept(top, xy, p, a):
            side.append(p)
            if a is None:
                head.append(p)
            else:
                writes[p] = _post(run, top, p, a)
                idx += [p] * (xy[0] not in " ?")
    if side:
        _undo(run, key, repo, writes, head, idx, [])
        st["side_effects"] += [x for x in side if x not in st["side_effects"]]
    if entry:
        try:
            now = _read(os.path.join(top, entry[0]))
        except OSError:
            now = None
        if now != entry[1]:
            return "tests changed " + _names([entry[0]])
    return None


def plan(run: str, words: List[str], fix_tests: bool = False) -> dict:
    tj = target.load_target(run)
    if tj["kind"] in ("staged", "unstaged", "worktree"):
        raise target.Refuse("uncommitted-target")  # UD-3: only a committed target whose HEAD is checked out
    if tj["kind"] in ledger.WHOLE and tj.get("dirty"):
        raise target.Refuse("dirty-pin")  # their tree/ held working-copy content
    if any("\0" in w for w in words):
        raise target.Refuse("bad-command")
    ledger.trust_gate(run, words)
    repo = _root(tj)
    if target.git(["rev-parse", "HEAD"], repo).strip() != tj["head_sha"]:
        raise target.Refuse("head-not-checked-out")
    if _changes(repo):
        raise target.Refuse("dirty-tree")  # FIX-04: a clean start, untracked files included
    if os.path.exists(os.path.join(_fixdir(run), "state.json")):
        raise target.Refuse("already-run")  # FIX-03: one run per review
    # shortcut: a relative script word only; an absolute path into the repo is not matched, upgrade with os.path.relpath
    cmd_files = {os.path.normpath(w) for w in words if not os.path.isabs(w)}
    entries, skipped = [], []
    known = fix_tests and {e["file"] for e in target.load_json(run, "ledger.json").get("tests", []) if isinstance(e, dict)} | set(tj["included"])
    found = report.build(run)["findings"]
    for f in found:
        fix_text = f.get("fix")
        derived = not isinstance(fix_text, str) or not fix_text.strip()  # no fix text: the fixer derives the edit
        why, line = None, None
        if f["severity"] == "nit":  # report-only: never located, never briefed
            skipped.append({"id": f["id"], "path": f["path"], "why": "nit"})
            continue
        if f.get("evidence_class") == report.STRICT_CLASS:  # --strict source-read finding: report-only (gh#82)
            skipped.append({"id": f["id"], "path": f["path"], "why": "inferred"})
            continue
        judges = _judges(f["path"], cmd_files)
        if judges and not (fix_tests and _editable_test(f["path"]) and f["path"] not in cmd_files):  # a named file may be the script itself
            why = "test-path"  # a fix there could weaken the tests that judge it; --fix-tests opts in for test files only
        elif judges and not _covered(f["path"], cmd_files, known):
            why = "not-covered"  # the post-fix run would not judge this edit
        else:
            _, line, why = _locate(repo, f["path"], f["quote"])
        if why:
            skipped.append({"id": f["id"], "path": f["path"], "why": why})
            continue
        e = {k: f[k] for k in ("id", "path", "severity", "category", "origin", "title", "trigger", "quote")}
        e["fix"], e["derived"] = ("" if derived else fix_text), derived
        e["protected"] = _protected(f["path"])  # the riskiest edits: flagged in the preview, not refused
        e["test_file"] = judges  # only reachable with --fix-tests: flagged and counted like a protected path
        e["line"], e["end_line"] = line, line + (f["end_line"] - f["line"])
        e["model"], e["effort"] = _tier(e)
        entries.append(e)
    if not entries:
        counts = {}
        for x in skipped:
            counts[x["why"]] = counts.get(x["why"], 0) + 1
        raise target.Refuse("nothing-to-fix", "skipped: " + (", ".join("%s %d" % kv for kv in sorted(counts.items()))
                                                             or "none"))
    doc = {"version": 1, "head_sha": tj["head_sha"], "command": words, "entries": entries, "skipped": skipped}
    prot, tf = sum(e["protected"] for e in entries), sum(e["test_file"] for e in entries)
    text = ["review-code fix plan: %d to apply, %d skipped%s%s; tests: %s" % (
        len(entries), len(skipped), "; %d protected path" % prot if prot else "", "; %d test file" % tf if tf else "",
        _e(shlex.join(words)))]
    if _elsewhere(tj):
        text.append("checkout: " + _e(repo) + " (edits and tests run here)")
    for k, e in enumerate(entries, 1):
        text.append("%d/%d %s %s:%d %s %s %s %s/%s%s%s" % (k, len(entries), e["id"], _e(e["path"]), e["line"],
                                                        e["severity"], e["category"], e["origin"], e["model"],
                                                        e["effort"], " protected path" if e["protected"] else "",
                                                        " test file" if e["test_file"] else ""))
        for label in ("title", "fix"):
            rows = ["derived by fixer: no fix text"] if label == "fix" and e["derived"] else \
                [sanitize.escape_for_code(r)[0] for r in e[label].split("\n")]
            text += ["  %s: %s" % (label, rows[0])] + ["      " + r for r in rows[1:]]
    if skipped:
        text.append("== skipped ==")
        text += ["%s %s %s" % (s["id"], _e(s["path"]), s["why"]) for s in skipped]
    body = "\n".join(text)
    if len(body) > target.MAX_PREVIEW:
        raise target.Refuse("preview-too-large")
    fd = _fixdir(run)
    os.makedirs(fd, mode=0o700, exist_ok=True)
    os.chmod(fd, 0o700)
    data = json.dumps(doc, sort_keys=True, indent=2).encode("utf-8")
    target.write_atomic(os.path.join(fd, "plan.json"), data)
    return {"status": "ok", "text": body, "digest": hashlib.sha256(data).hexdigest(), "apply": len(entries),
            "protected": prot, "test_files": tf, "skipped": len(skipped)}


def baseline(run: str, digest: str) -> dict:
    tj = target.load_target(run)
    fd = _fixdir(run)
    if os.path.exists(os.path.join(fd, "state.json")):
        raise target.Refuse("already-run")
    try:
        raw = _read(os.path.join(fd, "plan.json"))
    except OSError:
        raise target.Refuse("no-plan")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise target.Refuse("digest-mismatch")
    ledger.trust_gate(run, json.loads(raw.decode("utf-8"))["command"])
    repo = _root(tj)
    files = _git_files(repo)
    st = {"version": 1, "digest": digest, "refs": _refs(repo), "git_files": files, "done": {}, "accepted": {},
          "untracked": [], "in_flight": None, "halted": "baseline-interrupted", "side_effects": []}
    _save(run, st)  # before the run: a second baseline or plan refuses already-run, and a hard kill leaves it halted
    try:
        res = _tests(run, st)
        moved = _sweep(run, tj, st, "baseline")
    except BaseException:
        st["halted"] = "baseline-interrupted"  # no green baseline: begin must not hand out a fix
        _save(run, st)
        raise
    reason = "baseline-red" if res["outcome"] != "pass" else moved  # None, head-moved or refs-moved
    st["halted"] = reason
    _save(run, st)
    if reason:
        return {"status": "halted", "reason": reason, "exit": res["exit"], "tail": res["tail"]}
    return {"status": "ok"}


def _fixer_agent(run: str) -> str:
    """<agent_prefix>:fixer, the prefix recorded in the run's plan.json (review-code without one)."""
    try:
        return target.load_json(run, "plan.json").get("agent_prefix", "review-code") + ":fixer"
    except target.Refuse:
        return "review-code:fixer"


def begin(run: str) -> dict:
    st = _state(run)
    if st["halted"]:
        raise target.Refuse("halted")
    if st["in_flight"]:
        raise target.Refuse("in-flight")
    tj = target.load_target(run)
    repo = _root(tj)
    fd = _fixdir(run)
    raw = _read(os.path.join(fd, "plan.json"))
    if hashlib.sha256(raw).hexdigest() != st["digest"]:
        raise target.Refuse("digest-mismatch")
    if target.git(["rev-parse", "HEAD"], repo).strip() != tj["head_sha"]:
        raise target.Refuse("head-moved")
    if _refs(repo) != st["refs"]:
        raise target.Refuse("refs-moved")
    top = os.path.realpath(repo)
    for xy, p in _changes(repo):  # only accepted fixes and files a test run left behind may differ from HEAD
        a = st["accepted"].get(p)
        kept = a is not None and _kept(top, xy, p, a)
        if not kept and not (xy == "??" and p in st["untracked"]):
            raise target.Refuse("tree-changed")
    for e in json.loads(raw.decode("utf-8"))["entries"]:
        if e["id"] in st["done"]:
            continue
        real, line, why = _locate(repo, e["path"], e["quote"])
        if why:
            st["done"][e["id"]] = {"outcome": why, "exit": None}
            continue
        data = _read(real)
        target.write_atomic(os.path.join(fd, e["id"] + ".pre"), data)
        nonce = secrets.token_hex(8)
        brief = ["# Fix finding %s" % e["id"], "repo: " + json.dumps(os.path.realpath(repo)),
                 "file: " + json.dumps(real), "lines: %d-%d" % (line, line + (e["end_line"] - e["line"]))]
        brief += ["protected path: this file is a lockfile, manifest, config or CI file; keep the edit minimal"] * e["protected"]
        brief += ["test file: this file judges the tests; fix the defect without removing, skipping or weakening any assertion"] * e["test_file"]
        brief += ["%s:\n%s" % (k, ledger.block(e[k], nonce)) for k in ("title", "trigger", "quote")]
        brief.append("fix: none. No fix text exists for this finding; derive the smallest edit that resolves it from the "
                     "title, trigger and quote above." if e["derived"] else "fix:\n" + ledger.block(e["fix"], nonce))
        target.write_private(os.path.join(fd, e["id"] + ".md"), "\n".join(brief) + "\n")
        st["in_flight"] = {"id": e["id"], "path": e["path"], "mode": stat.S_IMODE(os.stat(real).st_mode)}
        _save(run, st)
        return {"status": "ok", "id": e["id"], "brief": os.path.join(fd, e["id"] + ".md"), "path": e["path"],
                "line": line, "model": e["model"], "effort": e["effort"], "agent": _fixer_agent(run), "seal": _seal(run, e["id"])}
    _save(run, st)
    return {"status": "done"}


def _settle(run: str, tj: dict, st: dict, cur: dict, status: str):
    """Judge one fixer call after the seal held: (outcome, detail, test result). Restores on every revert."""
    repo, fid, path = _root(tj), cur["id"], cur["path"]
    top, fd = os.path.realpath(repo), _fixdir(run)
    real = os.path.join(top, path)
    pre = _read(os.path.join(fd, fid + ".pre"))
    entry = {path: (pre, cur["mode"])}
    moved = _moved(tj, st)
    if moved:
        _undo(run, fid, repo, entry, [], [], [])
        return "reverted-violation", moved, None
    writes, head, idx, stray, bad = {}, [], [], [], []
    for xy, p in _changes(repo):
        a = st["accepted"].get(p)
        staged = xy[0] not in " ?"
        if p == path:
            if xy != " M":
                bad.append(p)
                writes[p] = entry[p]
                idx += [p] * staged
        elif a is not None:
            if not _kept(top, xy, p, a):
                bad.append(p)
                writes[p] = _post(run, top, p, a)
                idx += [p] * staged
        elif xy == "??":
            if p not in st["untracked"]:
                bad.append(p)
                stray.append(p)
        else:
            bad.append(p)
            head.append(p)
    try:
        now = _read(real)
    except OSError:
        now = None
    if status == "declined" and now != pre and path not in bad:
        bad.append(path)
    if bad:
        if now != pre:
            writes[path] = entry[path]
        _undo(run, fid, repo, writes, head, idx, stray)
        return "reverted-violation", _names(bad), None
    if status == "declined":
        return "declined", None, None
    if now == pre:
        return "no-change", None, None
    n = _changed_lines(pre, now)
    if n > MAX_CHANGED:
        _undo(run, fid, repo, entry, [], [], [])
        return "reverted-too-large", "%d changed lines, the limit is %d" % (n, MAX_CHANGED), None
    why = weaken.weakened(path, pre, now) if _editable_test(path) else None
    if why:
        _undo(run, fid, repo, entry, [], [], [])
        return "reverted-weakened", why, None
    res = _tests(run, st)
    broke = _sweep(run, tj, st, fid, (path, now))
    if broke:
        _undo(run, fid, repo, entry, [], [], [])
        return "reverted-violation", broke, res
    if res["outcome"] == "pass":
        target.write_atomic(os.path.join(fd, fid + ".post"), now)
        st["accepted"][path] = {"id": fid, "sha": hashlib.sha256(now).hexdigest()}
        return "fixed", None, res
    _undo(run, fid, repo, entry, [], [], [])
    return ("reverted-timeout" if res["outcome"] == "timeout" else "reverted-tests"), None, res


def _snapshots_hold(run: str, st: dict) -> bool:
    """Plain reads: every kept fix's .post still hashes to the sha recorded when it was accepted."""
    try:
        return all(hashlib.sha256(_read(os.path.join(_fixdir(run), a["id"] + ".post"))).hexdigest() == a["sha"]
                   for a in st["accepted"].values())
    except OSError:
        return False


def finish(run: str, seal: str, status: str, code: Optional[str] = None, line: Optional[int] = None) -> dict:
    if status not in ("applied", "declined") or not HEX.match(seal) or \
            ((code is not None or line is not None) and status != "declined"):
        raise target.Refuse("usage", USAGE)
    if code not in DECLINE_CODES:
        code = None  # the code is LLM output: an unknown one is dropped, never a reason to leave the entry in flight
    st = _state(run)
    cur = st["in_flight"]
    if not cur:
        if all(n[:-4] in st["done"] for n in os.listdir(_fixdir(run)) if n.endswith(".pre")):
            raise target.Refuse("not-in-flight")  # nothing pending: a repeat of a finish that recorded its outcome
        # a snapshot has no recorded outcome, so state.json lost its in-flight entry: no seal to compare, no git call,
        # no restore
        st["halted"] = "tampered"
        _save(run, st)
        return {"status": "halted", "outcome": "tampered"}
    fid, detail, res = cur["id"], None, None
    if not secrets.compare_digest(_seal(run, fid), seal) or not _snapshots_hold(run, st):
        outcome = "tampered"  # decided on plain file reads: no git call, no restore of a possibly rewritten snapshot
    else:
        tj = target.load_target(run)  # read only after the seal held: the fixer could have rewritten it
        try:
            outcome, detail, res = _settle(run, tj, st, cur, status)
        except BaseException as e:  # Ctrl-C, SIGTERM (as SystemExit), any error: the tree goes back, then re-raise
            _undo(run, fid, _root(tj), {cur["path"]: (_read(os.path.join(_fixdir(run), fid + ".pre")), cur["mode"])},
                  [], [], [])
            st["done"][fid] = {"outcome": "reverted-interrupt", "exit": None, "detail": type(e).__name__}
            st["in_flight"], st["halted"] = None, "reverted-interrupt"
            _save(run, st)
            raise
    st["done"][fid] = {"outcome": outcome, "exit": res["exit"] if res else None}
    if detail:
        st["done"][fid]["detail"] = detail
    if outcome == "declined":  # the fixer's own reason, kept off the other outcomes: a violation is not a decline
        st["done"][fid].update({k: v for k, v in (("code", code), ("line", line)) if v is not None})
    if res and outcome in REVERTED:
        st["done"][fid]["tail"] = res["tail"]
    st["in_flight"] = None
    halts = outcome in HALTING
    if halts:
        st["halted"] = outcome
    _save(run, st)
    out = {"status": "halted" if halts else "ok", "outcome": outcome}
    if detail:
        out["detail"] = detail
    if res and outcome in REVERTED:
        out["exit"], out["tail"] = res["exit"], res["tail"]
    return out


def report_(run: str) -> dict:
    st = _state(run)
    fd, cur = _fixdir(run), st["in_flight"]
    tampered = st["halted"] == "tampered"
    pend = sorted(n[:-4] for n in os.listdir(fd) if n.endswith(".pre") and n[:-4] not in st["done"])
    unsafe = tampered or cur is not None or bool(pend)  # then no git command runs: a rewritten config may carry filters
    tj = None if unsafe else target.load_target(run)
    repo = None if unsafe else _root(tj)
    try:
        doc = _json(os.path.join(fd, "plan.json"))
        entries, skipped = doc["entries"], doc.get("skipped", [])
    except (OSError, ValueError, KeyError, RecursionError):
        entries, skipped = [], []
    text, counts = [], {}
    if not unsafe and _elsewhere(tj):
        text.append("checkout: " + _e(repo) + " (run git diff there)")
    for e in entries:
        d = st["done"].get(e["id"], {})
        outcome = d.get("outcome", "in-flight" if cur and cur["id"] == e["id"] else
                        "not-reached" if st["halted"] else "pending")
        why = [d["detail"]] if d.get("detail") else ([d["code"]] if "code" in d else []) + (["line %d" % d["line"]] if "line" in d else [])
        if outcome == "reverted-tests" and d.get("exit") is not None:
            why.append("exit %s" % d["exit"])
        counts[outcome] = counts.get(outcome, 0) + 1
        text.append("%s %s:%d %s%s" % (e["id"], _e(e["path"]), e["line"], outcome,
                                       " (%s)" % _e(", ".join(why)) if why else ""))
    for x in skipped:  # verified findings the plan left out: each one still appears once, with its reason
        counts[x["why"]] = counts.get(x["why"], 0) + 1
        text.append("%s %s %s" % (x["id"], _e(x["path"]), x["why"]))
    verified = len(entries) + len(skipped)
    text.append("verified findings: %d = %s" % (verified, " + ".join("%d %s" % (n, k) for k, n in sorted(counts.items()))
                                                or "0"))
    if st["halted"] and not tampered:
        text.append("halted: " + _e(st["halted"]))
    if tampered:
        text.append("TAMPERED: the run directory or the git config changed while the fixer ran. The tree is left as it "
                    "is and no git command ran. Check .git/config and git status by hand.")
    elif not unsafe:
        stat_out = target.git(["diff", "--stat", "--no-color", "--no-ext-diff"], repo)
        text += ["== changed files ==", sanitize.escape_for_code(stat_out)[0].rstrip("\n")]
    quarantined = sorted(os.path.relpath(os.path.join(d, n), os.path.join(fd, "quarantine"))
                         for d, _, names in os.walk(os.path.join(fd, "quarantine")) for n in names)
    for title, items in (("restored after tests", st["side_effects"]),
                         ("quarantined", quarantined[:50] + ["and %d more" % (len(quarantined) - 50)] *
                          (len(quarantined) > 50)),
                         ("untracked files left by tests", st["untracked"])):
        if items:
            text += ["== %s ==" % title] + [_e(x) for x in items]
    if cur:
        text += ["== in flight ==", "%s (the file may hold a partial edit); snapshot: %s" % (
            _e(cur["path"]), os.path.join(fd, cur["id"] + ".pre")),
            "no git command ran: the fixer call did not finish and may have changed .git/config; check .git/config "
            "and git status by hand"]
    elif pend:
        text += ["== in flight =="] + ["snapshot: " + os.path.join(fd, i + ".pre") for i in pend]
        text.append("no git command ran: a snapshot has no recorded outcome, so a fixer call did not finish "
                    "normally and may have changed .git/config; check .git/config and git status by hand")
    if not unsafe:
        known = set(st["accepted"]) | set(st["untracked"]) | ({cur["path"]} if cur else set())
        odd = [p for _, p in _changes(repo) if p not in known]
        if odd:
            text += ["== unexpected changes =="] + [_e(p) for p in odd]
    tails = [(i, d) for i, d in st["done"].items() if d.get("tail")]
    if tails:
        nonce = secrets.token_hex(8)
        text.append("== test output (untrusted) ==")
        for i, d in tails:
            text += ["%s %s exit %s" % (i, d["outcome"], d["exit"]), ledger.block(d["tail"], nonce)]
    if not unsafe:
        text.append("HEAD and refs: " + ("unchanged" if _refs(repo) == st["refs"] else "MOVED"))
    text += ["nothing was committed, staged or pushed by fix.py; review the changes with git diff%s before committing" %
             (" in " + _e(repo) if not unsafe and _elsewhere(tj) else ""),
            "passing tests do not prove a fix is right where the tests do not cover it",
            "a new import of a package no manifest declares is not checked"]
    outs = [v.get("outcome") for v in st["done"].values()]
    return {"status": "ok", "text": "\n".join(text), "fixed": outs.count("fixed"),
            "reverted": sum(1 for o in outs if o in REVERTED), "declined": outs.count("declined"),
            "halted": 1 if st["halted"] else 0, "verified": verified,
            "not_reached": counts.get("not-reached", 0)}


def _term(signum, frame):
    for name in ("SIGINT", "SIGTERM", "SIGHUP"):  # one-shot: a second signal must not abort the restore the first one starts
        if getattr(signal, name, None):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    if signum == signal.SIGINT:
        raise KeyboardInterrupt  # main() prints the halted line; the restore handlers run first
    raise SystemExit(143)  # unwinds through the restore handlers; the default action would skip them


def main(argv: List[str], stdin: Optional[object] = None) -> int:
    flags = {"plan": ["--run"], "baseline": ["--run", "--digest"], "begin": ["--run"],
             "finish": ["--run", "--seal", "--status"], "report": ["--run"]}
    try:
        sub, opts = (argv[0], argv[1:]) if argv else ("", [])
        fix_tests = sub == "plan" and "--fix-tests" in opts  # a bare flag among the --name value pairs
        if fix_tests:
            opts = [o for o in opts if o != "--fix-tests"]
        names = opts[0::2]
        need = flags.get(sub, [])
        extra = names[len(need):]  # finish only: --code and --line, once each, in either order
        allowed = {"--code", "--line"} if sub == "finish" else set()
        if sub not in flags or names[:len(need)] != need or len(opts) != 2 * len(names) \
                or len(set(extra)) != len(extra) or not set(extra) <= allowed:
            raise target.Refuse("usage", USAGE)
        v = dict(zip(names, opts[1::2]))
        hexes = [v[k] for k in ("--digest", "--seal") if k in v]
        # presence, not value: --line 0 is not "absent"
        if not all(HEX.match(h) for h in hexes) or v.get("--status", "applied") not in ("applied", "declined") \
                or (extra and v["--status"] != "declined"):
            raise target.Refuse("usage", USAGE)
        run = v["--run"]
        if sub in ("baseline", "finish"):
            for name in ("SIGINT", "SIGTERM", "SIGHUP"):  # a hangup would otherwise skip the restore; Windows has no SIGHUP
                if getattr(signal, name, None):
                    signal.signal(getattr(signal, name), _term)
        if sub == "plan":
            text = (stdin or sys.stdin.buffer).read().decode("utf-8", "replace")
            out = plan(run, [w.rstrip("\r") for w in text.split("\n") if w.rstrip("\r")], fix_tests)
        elif sub == "baseline":
            out = baseline(run, v["--digest"])
        elif sub == "begin":
            out = begin(run)
        elif sub == "finish":
            out = finish(run, v["--seal"], v["--status"], v.get("--code"),
                         int(v["--line"]) if re.fullmatch(r"[1-9][0-9]{0,8}", v.get("--line", "")) else None)
        else:
            out = report_(run)
    except target.Refuse as r:
        out = {"status": "refuse", "reason": r.reason}
        if r.detail:
            out["detail"] = r.detail
    except (OSError, KeyError, ValueError, TypeError, RecursionError) as e:
        out = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}
    except KeyboardInterrupt:  # finish and baseline restored before re-raising
        out = {"status": "halted", "reason": "interrupted"}
    if out.get("text"):
        print(out["text"])
    target.emit(out, STDOUT_KEYS)
    return 0 if out["status"] in ("ok", "done") and not out.get("reverted") else 1  # a report exits 1 when an entry failed


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
