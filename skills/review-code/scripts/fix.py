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
import difflib
import hashlib
import itertools
import json
import os
import re
import secrets
import shlex
import shutil
import signal
import stat
import tempfile
from typing import List, Optional, Tuple

import ledger
import report
import repro
import sanitize
import target

ID_RE = report.ID_RE
TIMEOUT = 540  # seconds; read at call time. SKILL.md sets a 600000 ms Bash call: 60 s left for the kill and restore
MAX_CHANGED = 60  # changed lines per fix: the + and - lines of a zero-context diff
MAX_PREVIEW = 25000  # same bound and reason as post.py
TIERS = {"simple": ("sonnet", "medium"), "complex": ("opus", "high")}
_MANIFEST = re.compile(
    r"(?:requirements.*\.txt|constraints.*\.txt|setup\.py|go\.mod|go\.work|Gemfile|.*\.gemspec|Pipfile|"
    r"build\.gradle|build\.gradle\.kts|settings\.gradle|settings\.gradle\.kts|pom\.xml|NAMESPACE|.*\.csproj|"
    r".*\.fsproj|.*\.vbproj|Directory\.Build\.props|Directory\.Packages\.props|packages\.config|composer\.json|"
    r"Jenkinsfile|CMakeLists\.txt|.*\.cabal|mix\.exs)\Z")
_RUNNER = re.compile(r"(?:(?:jest|vitest|karma|webpack|babel|playwright|cypress|mocha|ava)\.conf(?:ig)?(?:\.\w+)+|"
                     r"(?:jest\.setup|setupTests)(?:\.\w+)+|noxfile\.py|Rakefile|build\.rs)\Z")
# test names ledger._TEST_NAME lacks
_TEST_MORE = re.compile(r"(?:.*\.(?:test|spec)\.[cm][jt]s|.*\.cy\.[jt]sx?|.*_spec\.rb|.*Tests?\.cs)\Z")
OUTCOMES = ("fixed", "declined", "no-change", "stale", "reverted-violation", "reverted-too-large", "reverted-tests",
            "reverted-timeout", "reverted-interrupt", "tampered")
REVERTED = tuple(o for o in OUTCOMES if o.startswith("reverted-")) + ("tampered",)  # each one halts the run
STDOUT_KEYS = ("status", "reason", "detail", "digest", "apply", "skipped", "id", "brief", "path", "line", "model",
               "effort", "seal", "outcome", "exit", "tail", "fixed", "reverted", "declined", "halted")
HEX = re.compile(r"[0-9a-f]{64}\Z")
USAGE = ("usage: fix.py plan --run DIR (test command on stdin, one word per line) | baseline --run DIR --digest HEX | "
         "begin --run DIR | finish --run DIR --seal HEX --status applied|declined | report --run DIR")


def _e(x) -> str:
    """Untrusted text, one line, escaped for a terminal."""
    return sanitize.escape_for_code(" ".join(str(x).split()))[0]


def _fixdir(run: str) -> str:
    return os.path.join(run, "fix")


def _json(path: str):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _put(path: str, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.chmod(path, 0o600)


def _target(run: str) -> dict:
    try:
        rec = _json(os.path.join(run, "target.json"))
        rec["repo"], rec["kind"], rec["head_sha"]
        return rec
    except (OSError, ValueError, KeyError, TypeError):
        raise target.Refuse("no-target")


STATE_KEYS = ("version", "digest", "refs", "git_files", "done", "accepted", "untracked", "in_flight", "halted",
              "side_effects")  # what baseline writes


def _typed(st) -> bool:
    """state.json holds the types fix.py writes, so no consumer meets an AttributeError or KeyError."""
    if not isinstance(st, dict) or any(k not in st for k in STATE_KEYS):
        return False
    cur = st["in_flight"]
    return (all(isinstance(st[k], dict) for k in ("done", "accepted"))
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
    """Paths no fix may touch: dot components, lockfiles, manifests, config and CI files (A5)."""
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
    return (path in cmd_files or ledger._is_fixture(path) or bool(_RUNNER.match(base) or _TEST_MORE.match(base)
            or ledger._TEST_NAME.match(base) or (ledger._TEST_DIRS - {"spec"}) & set(dirs)) or dirs[:1] == ["spec"])


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
        out.append((xy, ent[3:].decode("utf-8", "replace")))
        if ("R" in xy or "C" in xy) and i < len(parts):
            out.append((xy, parts[i].decode("utf-8", "replace")))
            i += 1
    return out


def _refs(repo: str) -> str:
    head = target.git(["rev-parse", "HEAD"], repo).strip()
    listing = target.git(["for-each-ref", "--format=%(refname) %(objectname)", "refs/heads", "refs/tags", "refs/stash"],
                         repo)
    return hashlib.sha256((head + "\n" + listing).encode("utf-8")).hexdigest()


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
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".fix-")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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
    """+ and - lines of a zero-context diff; latin-1 maps bytes 1:1 so only b"\n" splits lines."""
    d = difflib.unified_diff(a.decode("latin-1").split("\n"), b.decode("latin-1").split("\n"), n=0, lineterm="")
    return sum(1 for ln in itertools.islice(d, 2, None) if ln[0] in "+-")  # islice drops the two headers

def _tests(run: str, st: dict) -> dict:
    """Run the stored command once in the repository top. pass: exit 0 and no timeout or launch error."""
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
        res = repro._execute(command, _target(run)["repo"], env, "", timeout=TIMEOUT)  # empty id: only the exit counts
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    tail = sanitize.escape_for_code(res["tail"])[0]
    if res["outcome"] in ("timeout", "error"):
        return {"outcome": res["outcome"], "exit": res["exit"], "tail": tail}
    return {"outcome": "pass" if res["exit"] == 0 else "fail", "exit": res["exit"], "tail": tail}


def _moved(tj: dict, st: dict) -> Optional[str]:
    if target.git(["rev-parse", "HEAD"], tj["repo"]).strip() != tj["head_sha"]:
        return "head-moved"
    return "refs-moved" if _refs(tj["repo"]) != st["refs"] else None


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
    repo = tj["repo"]
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


def plan(run: str, words: List[str]) -> dict:
    tj = _target(run)
    if tj["kind"] in ("staged", "unstaged", "worktree"):
        raise target.Refuse("uncommitted-target")  # UD-3: only a committed target whose HEAD is checked out
    if tj["kind"] in ("path", "repo") and tj.get("dirty"):
        raise target.Refuse("dirty-pin")  # their tree/ held working-copy content
    if any("\0" in w for w in words):
        raise target.Refuse("bad-command")
    ledger.trust_gate(run, words)
    repo = tj["repo"]
    if target.git(["rev-parse", "HEAD"], repo).strip() != tj["head_sha"]:
        raise target.Refuse("head-not-checked-out")
    if _changes(repo):
        raise target.Refuse("dirty-tree")  # FIX-04: a clean start, untracked files included
    if os.path.exists(os.path.join(_fixdir(run), "state.json")):
        raise target.Refuse("already-run")  # FIX-03: one run per review
    # shortcut: a relative script word only; an absolute path into the repo is not matched, upgrade with os.path.relpath
    cmd_files = {os.path.normpath(w) for w in words if not os.path.isabs(w)}
    entries, skipped = [], []
    for f in report.build(run)["findings"]:
        fix_text = f.get("fix")
        why, line = None, None
        if not isinstance(fix_text, str) or not fix_text.strip():
            why = "no-fix"
        elif _protected(f["path"]):
            why = "protected-path"
        elif _judges(f["path"], cmd_files):
            why = "test-path"  # a fix there could weaken the tests that judge it
        else:
            real = _live(repo, f["path"])
            line = None if real is None else _relocate(_read(real).decode("utf-8", "replace"), f["quote"])
            why = "bad-path" if real is None else "stale" if line is None else None
        if why:
            skipped.append({"id": f["id"], "path": f["path"], "why": why})
            continue
        e = {k: f[k] for k in ("id", "path", "severity", "category", "origin", "title", "trigger", "quote", "fix")}
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
    text = ["review-code fix plan: %d to apply, %d skipped; tests: %s" % (len(entries), len(skipped),
                                                                          _e(shlex.join(words)))]
    for k, e in enumerate(entries, 1):
        text.append("%d/%d %s %s:%d %s %s %s %s/%s" % (k, len(entries), e["id"], _e(e["path"]), e["line"],
                                                      e["severity"], e["category"], e["origin"], e["model"],
                                                      e["effort"]))
        for label in ("title", "fix"):
            rows = [sanitize.escape_for_code(r)[0] for r in e[label].split("\n")]
            text += ["  %s: %s" % (label, rows[0])] + ["      " + r for r in rows[1:]]
    if skipped:
        text.append("== skipped ==")
        text += ["%s %s %s" % (s["id"], _e(s["path"]), s["why"]) for s in skipped]
    body = "\n".join(text)
    if len(body) > MAX_PREVIEW:
        raise target.Refuse("preview-too-large")
    fd = _fixdir(run)
    os.makedirs(fd, mode=0o700, exist_ok=True)
    os.chmod(fd, 0o700)
    data = json.dumps(doc, sort_keys=True, indent=2).encode("utf-8")
    _put(os.path.join(fd, "plan.json"), data)
    return {"status": "ok", "text": body, "digest": hashlib.sha256(data).hexdigest(), "apply": len(entries),
            "skipped": len(skipped)}


def baseline(run: str, digest: str) -> dict:
    tj = _target(run)
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
    repo = tj["repo"]
    files = [os.path.join(repo, target.git(["rev-parse", "--git-path", p], repo).strip())
             for p in ("config", "info/attributes", "info/exclude")]
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


def begin(run: str) -> dict:
    st = _state(run)
    if st["halted"]:
        raise target.Refuse("halted")
    if st["in_flight"]:
        raise target.Refuse("in-flight")
    tj = _target(run)
    repo = tj["repo"]
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
        real = _live(repo, e["path"])
        line = None if real is None else _relocate(_read(real).decode("utf-8", "replace"), e["quote"])
        if line is None:
            st["done"][e["id"]] = {"outcome": "stale", "exit": None}
            continue
        data = _read(real)
        _put(os.path.join(fd, e["id"] + ".pre"), data)
        nonce = secrets.token_hex(8)
        brief = ["# Fix finding %s" % e["id"], "repo: " + json.dumps(os.path.realpath(repo)),
                 "file: " + json.dumps(real), "lines: %d-%d" % (line, line + (e["end_line"] - e["line"]))]
        brief += ["%s:\n%s" % (k, ledger._block(e[k], nonce)) for k in ("title", "trigger", "quote", "fix")]
        target.write_private(os.path.join(fd, e["id"] + ".md"), "\n".join(brief) + "\n")
        st["in_flight"] = {"id": e["id"], "path": e["path"], "mode": stat.S_IMODE(os.stat(real).st_mode)}
        _save(run, st)
        return {"status": "ok", "id": e["id"], "brief": os.path.join(fd, e["id"] + ".md"), "path": e["path"],
                "line": line, "model": e["model"], "effort": e["effort"], "seal": _seal(run, e["id"])}
    _save(run, st)
    return {"status": "done"}


def _settle(run: str, tj: dict, st: dict, cur: dict, status: str):
    """Judge one fixer call after the seal held: (outcome, detail, test result). Restores on every revert."""
    repo, fid, path = tj["repo"], cur["id"], cur["path"]
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
    res = _tests(run, st)
    broke = _sweep(run, tj, st, fid, (path, now))
    if broke:
        _undo(run, fid, repo, entry, [], [], [])
        return "reverted-violation", broke, res
    if res["outcome"] == "pass":
        _put(os.path.join(fd, fid + ".post"), now)
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


def finish(run: str, seal: str, status: str) -> dict:
    if status not in ("applied", "declined") or not HEX.match(seal):
        raise target.Refuse("usage", USAGE)
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
        tj = _target(run)  # read only after the seal held: the fixer could have rewritten it
        try:
            outcome, detail, res = _settle(run, tj, st, cur, status)
        except BaseException as e:  # Ctrl-C, SIGTERM (as SystemExit), any error: the tree goes back, then re-raise
            _undo(run, fid, tj["repo"], {cur["path"]: (_read(os.path.join(_fixdir(run), fid + ".pre")), cur["mode"])},
                  [], [], [])
            st["done"][fid] = {"outcome": "reverted-interrupt", "exit": None, "detail": type(e).__name__}
            st["in_flight"], st["halted"] = None, "reverted-interrupt"
            _save(run, st)
            raise
    st["done"][fid] = {"outcome": outcome, "exit": res["exit"] if res else None}
    if detail:
        st["done"][fid]["detail"] = detail
    if res and outcome in REVERTED:
        st["done"][fid]["tail"] = res["tail"]
    st["in_flight"] = None
    if outcome in REVERTED:
        st["halted"] = outcome
    _save(run, st)
    out = {"status": "halted" if outcome in REVERTED else "ok", "outcome": outcome}
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
    repo = None if unsafe else _target(run)["repo"]
    try:
        entries = _json(os.path.join(fd, "plan.json"))["entries"]
    except (OSError, ValueError, KeyError):
        entries = []
    text = []
    for e in entries:
        d = st["done"].get(e["id"], {})
        outcome = d.get("outcome", "in-flight" if cur and cur["id"] == e["id"] else "pending")
        text.append("%s %s:%d %s%s" % (e["id"], _e(e["path"]), e["line"], outcome,
                                       " (%s)" % _e(d["detail"]) if d.get("detail") else ""))
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
            text += ["%s %s exit %s" % (i, d["outcome"], d["exit"]), ledger._block(d["tail"], nonce)]
    if not unsafe:
        text.append("HEAD and refs: " + ("unchanged" if _refs(repo) == st["refs"] else "MOVED"))
    text += ["nothing was committed, staged or pushed by fix.py; review the changes with git diff before committing",
            "passing tests do not prove a fix is right where the tests do not cover it",
            "a new import of a package no manifest declares is not checked"]
    outs = [v.get("outcome") for v in st["done"].values()]
    return {"status": "ok", "text": "\n".join(text), "fixed": outs.count("fixed"),
            "reverted": sum(1 for o in outs if o in REVERTED), "declined": outs.count("declined"),
            "halted": 1 if st["halted"] else 0}


def _term(signum, frame):
    for name in ("SIGTERM", "SIGHUP"):  # one-shot: a second signal must not abort the restore the first one starts
        if getattr(signal, name, None):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    raise SystemExit(143)  # unwinds through the restore handlers; the default action would skip them


def main(argv: List[str], stdin: Optional[object] = None) -> int:
    flags = {"plan": ["--run"], "baseline": ["--run", "--digest"], "begin": ["--run"],
             "finish": ["--run", "--seal", "--status"], "report": ["--run"]}
    try:
        sub, opts = (argv[0], argv[1:]) if argv else ("", [])
        if sub not in flags or opts[0::2] != flags[sub] or len(opts) != 2 * len(flags[sub]):
            raise target.Refuse("usage", USAGE)
        v = dict(zip(flags[sub], opts[1::2]))
        if ("--digest" in v and not HEX.match(v["--digest"])) or ("--seal" in v and not HEX.match(v["--seal"])) \
                or v.get("--status", "applied") not in ("applied", "declined"):
            raise target.Refuse("usage", USAGE)
        run = v["--run"]
        if sub in ("baseline", "finish"):
            for name in ("SIGTERM", "SIGHUP"):  # a hangup would otherwise skip the restore; Windows has no SIGHUP
                if getattr(signal, name, None):
                    signal.signal(getattr(signal, name), _term)
        if sub == "plan":
            text = (stdin or sys.stdin.buffer).read().decode("utf-8", "replace")
            out = plan(run, [w.rstrip("\r") for w in text.split("\n") if w.rstrip("\r")])
        elif sub == "baseline":
            out = baseline(run, v["--digest"])
        elif sub == "begin":
            out = begin(run)
        elif sub == "finish":
            out = finish(run, v["--seal"], v["--status"])
        else:
            out = report_(run)
    except target.Refuse as r:
        out = {"status": "refuse", "reason": r.reason}
        if r.detail:
            out["detail"] = r.detail
    except (OSError, KeyError, ValueError, TypeError) as e:
        out = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}
    except KeyboardInterrupt:  # finish and baseline restored before re-raising
        out = {"status": "halted", "reason": "interrupted"}
    if out.get("text"):
        print(out["text"])
    print(json.dumps({k: x for k, x in out.items() if k in STDOUT_KEYS}, sort_keys=True))
    return 0 if out["status"] in ("ok", "done") else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
