"""Pin the review target to full SHAs and create the run directory.

Stdlib only, Python 3.9 grammar. Prints one JSON object with fixed keys; repository-sourced text
(file names, notes, messages) goes to run-dir files only, never to stdout.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import errno
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import tempfile
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Iterator, List, Optional, Tuple

import prior
import sanitize

MAX_PREVIEW = 25000  # Claude Code saves Bash output over 30000 characters (its default) to a file, unseen by the user
STDOUT_KEYS = {"status", "reason", "detail", "kind", "base_sha", "head_sha", "merge_base", "number", "run",
               "included_count", "excluded_count", "untracked_count", "candidates", "since_last"}
# No hook or fsmonitor program from the repository runs (SC5); names arrive unquoted, so escape_for_code covers them.
_GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false", "-c", "core.quotePath=false"]
_WIDE = "--unified=999999"
# --text: a .gitattributes `-diff` cannot turn hunks into "Binary files differ".
_DIFF_FLAGS = ["-p", "-M", "--text", "--unified=3", "--no-ext-diff", "--no-textconv", "--no-color", "--submodule=short", "--src-prefix=a/",
               "--dst-prefix=b/"]
SKIP_REASONS = {"empty-diff", "no-files", "only-excluded", "closed", "merged", "draft", "locked"}
_LOCKFILES = frozenset((
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "bun.lockb", "npm-shrinkwrap.json", "Cargo.lock",
    "poetry.lock", "uv.lock", "Pipfile.lock", "Gemfile.lock", "composer.lock", "go.sum", "renv.lock",
    "flake.lock", "pubspec.lock", "Podfile.lock", "packages.lock.json", "gradle.lockfile"))
# shortcut: a changed or referenced file over this size leaves review unread ("too-large"); stripping holds a
# file several times over in memory. Raise it when lanes can usefully read bigger files (6xl.75).
MAX_FILE = 4_000_000
_ARGV_BUDGET = 60000  # bytes of path arguments per git call; far below the kernel limit
OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # SHA-1 or SHA-256, lowercase only
PR_URL = re.compile(r"https?://([^/\s]+)/(.+?)/pull/([0-9]+)(?:[/?#].*)?", re.S)
MR_URL = re.compile(r"https?://([^/\s]+)/(.+?)/-/merge_requests/([0-9]+)(?:[/?#].*)?", re.S)
_DIGITS = re.compile(r"[0-9]+")
_REMOTE_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([^/:]+)(?::[0-9]+)?[:/](.+?)/?", re.I | re.S)
_PR_FIELDS = "state,isDraft,baseRefOid,headRefOid,baseRefName,url,number"


class Refuse(Exception):
    def __init__(self, reason: str, detail: str = ""):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


def require_utf8(texts, reason: str, detail: str) -> None:
    """Refuse with `reason` when any text holds a lone surrogate (a non-UTF-8 path read lossily, or a JSON \\udcxx escape): it
    cannot be written out as UTF-8."""
    try:
        for t in texts:
            t.encode("utf-8")
    except UnicodeEncodeError:
        raise Refuse(reason, detail)


_NO_FILTERS: dict = {}  # cwd -> its GIT_CONFIG_* entries (_no_filters); cleared after an outside command (repro._execute)


def _env(cwd: str) -> dict:
    """The env of every git call: no prompt, no index write and no filter program, whichever script calls (rooc)."""
    if cwd not in _NO_FILTERS:
        _NO_FILTERS[cwd] = {}  # the config read itself runs with none: reading config runs no filter
        try:
            _NO_FILTERS[cwd] = _no_filters(cwd)
        except BaseException:
            del _NO_FILTERS[cwd]
            raise
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0", **_NO_FILTERS[cwd])


NET_TIMEOUT = 300  # seconds per ls-remote or fetch; shortcut: one bound for both, split it if large fetches hit it


def _run(args: List[str], cwd: str, stdin: Optional[bytes] = None, env: Optional[dict] = None, net: bool = False):
    """net: a call that talks to a remote. It runs in its own session, so ssh has no terminal to prompt on (with
    SSH_ASKPASS_REQUIRE=never, BatchMode without overriding the user's ssh command), and a hang past NET_TIMEOUT
    kills the whole group: killing git alone leaves ssh holding the pipes open. A timeout returns exit code -9.
    Its stderr is /dev/null and returned empty: no net caller reads it, and an ssh ControlPersist master that keeps
    an inherited stderr pipe open would stall the read to NET_TIMEOUT after a successful call (j8uh)."""
    full = dict(_env(cwd), **(env or {}))
    if not net:
        p = subprocess.run(_GIT + list(args), cwd=cwd, env=full, input=stdin,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        return p.returncode, p.stdout, p.stderr
    p = subprocess.Popen(_GIT + list(args), cwd=cwd, env=dict(full, SSH_ASKPASS_REQUIRE="never"),
                         stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        out, _ =p.communicate(stdin, timeout=NET_TIMEOUT)
    except BaseException as e:  # a timeout, or Ctrl-C, which no longer reaches the new session itself
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        if p.stdin:
            try:
                p.stdin.close()
            except OSError:  # its flush met the dead reader; the descriptor is closed regardless
                pass
        p.stdout.close()  # never drain: a grandchild that left the group (setsid, a ControlPersist master) keeps
        p.wait()          # the pipe open for ever; git itself is dead, so the wait returns
        if not isinstance(e, subprocess.TimeoutExpired):
            raise
        return -9, b"", b""
    return p.returncode, out, b""


def git_bytes(args: List[str], cwd: str, stdin: Optional[bytes] = None, check: bool = True,
              env: Optional[dict] = None) -> bytes:
    rc, out, _ = _run(args, cwd, stdin, env)
    if check and rc != 0:
        raise Refuse("bad-ref", "a git command failed")
    return out if rc == 0 else b""


def git(args: List[str], cwd: str, stdin: Optional[bytes] = None, check: bool = True, env: Optional[dict] = None) -> str:
    return git_bytes(args, cwd, stdin, check, env).decode("utf-8", "replace")


def _toplevel(cwd: str) -> str:
    """The repository root, without .strip(): that would also drop a CR the directory name ends in (wb5s)."""
    return _rev_path("--show-toplevel", cwd)


def _rev_path(flag: str, cwd: str, *operand: str) -> str:
    """One rev-parse path, lossless (surrogateescape) with the one trailing LF dropped. One flag per call: git echoes -z
    back instead of NUL-separating, so two LF-joined paths are ambiguous when either holds an LF."""
    out = git_bytes(["rev-parse", flag, *operand], cwd).decode("utf-8", "surrogateescape")
    return out[:-1] if out.endswith("\n") else out


def _symref(cwd: str, *args: str) -> Optional[str]:
    """`symbolic-ref --quiet` output, lossless (surrogateescape) with the one trailing LF dropped; None on a non-zero exit."""
    rc, out, _ = _run(["symbolic-ref", "--quiet", *args], cwd)
    name = out.decode("utf-8", "surrogateescape")
    return None if rc != 0 else name[:-1] if name.endswith("\n") else name


def _head_branch(cwd: str) -> Optional[str]:
    """The checked-out branch name; None when detached."""
    return _symref(cwd, "--short", "HEAD")


def _no_filters(cwd: str) -> dict:
    """GIT_CONFIG_* entries that blank every clean, smudge and process filter the config defines and mark it not
    required, so no filter program runs while git reads or writes a working tree (gh#48): the bytes stay as stored."""
    # --list, not --get-regexp: in a UTF-8 locale git's regex `.` never matches an invalid byte, so that driver ran (215w)
    rc, out, _ = _run(["config", "-z", "--list"], cwd)
    keys = sorted({k for k in (r.split("\n", 1)[0] for r in out.decode("utf-8", "surrogateescape").split("\0"))
                   if k.startswith("filter.") and k.rsplit(".", 1)[-1] in ("clean", "smudge", "process")
                   and len(k.split(".")) > 2}) if rc == 0 else []
    pairs = [(k, "") for k in keys] + [(k, "false") for k in sorted({k.rsplit(".", 1)[0] + ".required" for k in keys})]
    n = os.environ.get("GIT_CONFIG_COUNT", "")
    base = int(n) if n.isdigit() else 0  # entries the caller set stay; ours follow them
    env = {}
    for i, (k, v) in enumerate(pairs, base):
        env["GIT_CONFIG_KEY_%d" % i], env["GIT_CONFIG_VALUE_%d" % i] = k, v
    return dict(env, GIT_CONFIG_COUNT=str(base + len(pairs))) if pairs else {}


CLI_TIMEOUT = 120  # seconds per gh or glab call


def cli_run(tool: str, args: List[str], cwd: str, stdin: Optional[bytes] = None):
    """The one gh/glab subprocess seam: argv list, no shell. Returns (exit code, stdout bytes, stderr bytes)."""
    try:
        p = subprocess.run([tool] + list(args), cwd=cwd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=CLI_TIMEOUT)
    except FileNotFoundError:
        raise Refuse(tool + "-missing", tool + " is not installed")
    except subprocess.TimeoutExpired:  # a hung gh or glab would block the run, or a submit after its create, for ever
        raise Refuse(tool + "-failed", "timeout")
    return p.returncode, p.stdout, p.stderr


def _cli_json(tool: str, args: List[str], cwd: str):
    rc, out, _ = cli_run(tool, args, cwd)
    if rc != 0:
        raise Refuse(tool + "-failed", tool + " exited with an error")
    try:
        return json.loads(out.decode("utf-8", "replace"))
    except ValueError:
        raise Refuse(tool + "-failed", tool + " printed invalid JSON")


def gh_json(args: List[str], cwd: str):
    return _cli_json("gh", args, cwd)


def glab_json(args: List[str], cwd: str):
    return _cli_json("glab", args, cwd)


def bd_json(args: List[str], cwd: str):
    return _cli_json("bd", args, cwd)


def load_target(run: str) -> dict:
    """<run>/target.json, or Refuse("no-target") when it is missing or lacks what every consumer reads."""
    try:
        with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
            tj = json.load(fh)
        tj["repo"], tj["head_sha"], tj["url"]
        if isinstance(tj["kind"], str) and isinstance(tj["included"], list):
            return tj
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        pass
    raise Refuse("no-target", "the run directory has no usable target.json")


def load_json(run: str, name: str) -> dict:
    """<run>/<name>, read fresh; a missing file refuses no-<stem>, anything but a JSON object bad-<stem>."""
    try:
        with open(os.path.join(run, name), encoding="utf-8") as fh:
            obj = json.load(fh)
    except FileNotFoundError:
        raise Refuse("no-" + name.split(".")[0], "the run directory has no " + name)
    except (OSError, ValueError, RecursionError):
        raise Refuse("bad-" + name.split(".")[0], name + " cannot be read")
    if not isinstance(obj, dict):
        raise Refuse("bad-" + name.split(".")[0], name + " is not an object")
    return obj


def _touch_run(path: str) -> None:
    """Refresh the run directory above `path`: _prune keys on its own mtime, which a write deeper down never moves."""
    d = os.path.dirname(os.path.abspath(path))
    while os.path.dirname(d) != d:
        up = os.path.dirname(d)
        if os.path.basename(d).startswith("review-code.") and os.path.basename(up) == "review-code":
            try:
                os.utime(d)
            except OSError:
                pass
            return
        d = up


def write_private(path: str, text: str) -> None:
    """Create or truncate `path` with mode 0600 (the run-directory layout promises owner-only files)."""
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
        fh.write(text)
    _touch_run(path)


def write_atomic(path: str, data: bytes, mode: int = 0o600) -> None:
    """Put `data` at `path` byte-exact with `mode`: temp file in the same directory, then an atomic replace, so a
    reader never sees a half file and a failed write leaves the old file and no temp."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-")
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
    _touch_run(path)


RETENTION_DAYS = 14


def _prune(base: str) -> None:
    """IN-01: remove run directories, and the hook's captures directory, not touched for RETENTION_DAYS, and from a
    fresher captures directory the files older than that (a capture kept after a refusal). Runs before the new one is
    made, so the current run is never a candidate; links and other names are left alone; a removal that fails is skipped."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    for name in os.listdir(base):
        path = os.path.join(base, name)
        try:
            st = os.lstat(path)
            if (name.startswith("review-code.") or name == "captures") and stat.S_ISDIR(st.st_mode) and st.st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
            elif name == "captures" and stat.S_ISDIR(st.st_mode):
                for f in os.listdir(path):
                    fst = os.lstat(os.path.join(path, f))
                    if stat.S_ISREG(fst.st_mode) and fst.st_mtime < cutoff:
                        os.remove(os.path.join(path, f))
        except OSError:
            pass


def new_run_dir(cwd: str) -> str:
    """Mode 0700 directory under the git dir: a read-only lane cannot read $TMPDIR (probe A3)."""
    base = _rev_path("--git-path", cwd, "review-code")
    base = os.path.join(cwd, base)
    os.makedirs(base, mode=0o700, exist_ok=True)
    _prune(base)
    return tempfile.mkdtemp(prefix="review-code.", dir=base)


def _split(raw: bytes) -> List[str]:
    """NUL-separated names, decoded as ledger.py does so a non-UTF-8 name round-trips instead of becoming U+FFFD."""
    return [n for n in raw.decode("utf-8", "surrogateescape").split("\0") if n]


def _commit_id(rev: str, cwd: str) -> str:
    """Full object id of a commit-ish, exactly as git prints it."""
    if not rev or rev.startswith("-"):
        raise Refuse("bad-ref", "the target is not a commit in this repository")
    rc, out, err = _run(["rev-parse", "--verify", rev + "^{commit}"], cwd, env={"LC_ALL": "C"})  # English: err is matched below
    if rc == 0:
        return out.decode("utf-8", "replace").strip()
    if b"ambiguous" in err:
        raise Refuse("ambiguous-ref", "the name matches several objects")
    raise Refuse("bad-ref", "the target is not a commit in this repository")


def _num(text: str) -> int:
    n = int(text)
    if n < 1:
        raise Refuse("bad-number", "a pull request number is a positive integer")
    return n


def _repo_key(url: str) -> Optional[str]:
    """Lowercase host/owner/repo for https, ssh and scp-like remote URLs; None for local paths."""
    m = _REMOTE_URL.fullmatch(url.strip())
    if not m:
        return None
    path = m.group(2)
    if path.lower().endswith(".git"):
        path = path[:-4]
    return (m.group(1) + "/" + path).lower()


def _forge_key(host: str, path: str) -> str:
    """Lowercase host/path without the web port, the form `_repo_key` gives a remote (an ssh port is not the web port)."""
    return (re.sub(r":[0-9]+\Z", "", host) + "/" + path).lower()


def _remotes(cwd: str) -> List[tuple]:
    """(name, repo key) from the raw remote URLs; `git remote get-url` would expand insteadOf."""
    rows = []
    # --list, not --get-regexp: in a UTF-8 locale git's regex `.` never matches an invalid byte, so a remote named with one vanished
    raw = git_bytes(["config", "-z", "--list"], cwd, check=False)
    for rec in raw.decode("utf-8", "surrogateescape").split("\0"):
        parts = rec.split("\n", 1)  # -z: NUL ends a record, LF ends the key; splitlines would cut at NEL, LS, PS in a value
        if len(parts) == 2 and parts[0].startswith("remote.") and parts[0].endswith(".url"):
            name = parts[0][len("remote."):-len(".url")]
            key = _repo_key(parts[1])
            if key and name and not name.startswith("-"):
                rows.append((name, key))
    return rows


def shown(name: str) -> str:
    """A file name as run-dir files show it: hidden characters escaped (escape_for_code)."""
    return sanitize.escape_for_code(name)[0]


def _q(name: str) -> str:
    """A ref name from the repository, quoted unless plainly safe: `reading` text goes to stdout."""
    return name if re.fullmatch(r"[A-Za-z0-9._/+@-]+", name) else json.dumps(shown(name))


def default_branch(cwd: str) -> Optional[tuple]:
    """(ref usable in git, local branch name) of the default branch, or None."""
    ref = _symref(cwd, "refs/remotes/origin/HEAD")
    if ref is not None:
        ref = ref[len("refs/remotes/"):]
        if _run(["rev-parse", "--verify", "--quiet", ref + "^{commit}"], cwd)[0] == 0:
            return ref, ref.split("/", 1)[-1]
    for name in ("main", "master"):
        if _run(["rev-parse", "--verify", "--quiet", "refs/heads/" + name + "^{commit}"], cwd)[0] == 0:
            return name, name
    return None


def _no_argument(cwd: str) -> List[dict]:
    if git(["status", "--porcelain", "--untracked-files=normal", "--ignore-submodules=none"], cwd).strip():  # 6xl.68
        return [{"kind": "worktree", "reading": "uncommitted changes against HEAD, untracked files included"}]
    default = default_branch(cwd)
    on = _head_branch(cwd)
    if default and on is not None and on != default[1]:
        return [{"kind": "branch", "base": default[0], "head": "HEAD",
                 "reading": "changes on HEAD since " + _q(default[0])}]
    raise Refuse("no-target", "name a target to review")


def resolve(argv: List[str], cwd: str) -> List[dict]:
    if not argv:
        return _no_argument(cwd)
    flags = [a for a in argv if a in ("--staged", "--unstaged", "--repo")]
    if flags:
        if len(argv) > 1:
            raise Refuse("mixed-target", "give one target: a ref or range, a PR number, paths, --staged, --unstaged or --repo")
        kind = flags[0][2:]
        return [{"kind": kind, "reading": {"staged": "staged changes", "unstaged": "unstaged changes",
                                           "repo": "the whole repository"}[kind]}]
    for arg in argv:
        if arg.startswith("-"):
            raise Refuse("option-like-argument", "the target must not start with a dash")
    if len(argv) > 1:
        return [_path_candidate(argv, cwd)]
    return _classify(argv[0], cwd)


def _reading(kind: str, n: int) -> str:
    return ("pull request #%d" if kind == "pr" else "merge request !%d") % n


def _forge_candidate(kind: str, m: "re.Match") -> dict:
    n = _num(m.group(3))
    sep = "/pull/" if kind == "pr" else "/-/merge_requests/"
    return {"kind": kind, "number": n, "repo_key": _forge_key(m.group(1), m.group(2)),
            "url": "https://%s/%s%s%d" % (m.group(1), m.group(2), sep, n),
            "reading": _reading(kind, n)}


def _classify(arg: str, cwd: str) -> List[dict]:
    """Candidate readings in fixed order. Explicit syntax is unique; bare words may have several readings."""
    m = PR_URL.fullmatch(arg)
    if m:
        return [_forge_candidate("pr", m)]
    m = MR_URL.fullmatch(arg)
    if m:
        return [_forge_candidate("mr", m)]
    if arg[:1] in ("#", "!") and _DIGITS.fullmatch(arg[1:]):
        n = _num(arg[1:])
        kind = "pr" if arg[0] == "#" else "mr"
        return [{"kind": kind, "number": n, "url": None,
                 "reading": _reading(kind, n)}]
    if arg.endswith("^!"):
        _commit_id(arg[:-2], cwd)
        return [{"kind": "commit", "rev": arg[:-2], "reading": "the single commit " + arg[:-2]}]
    if ".." in arg:
        left, _, right = arg.partition("...") if "..." in arg else arg.partition("..")
        try:
            _commit_id(left, cwd), _commit_id(right, cwd)
            return [{"kind": "range", "base": left, "head": right, "reading": "changes on %s since %s" % (right, left)}]
        except Refuse:
            pass  # not a range: may be a path that contains ".."
    if arg == "." and os.path.realpath(cwd) == os.path.realpath(_toplevel(cwd)):
        return [{"kind": "repo", "reading": "the whole repository"}]  # elsewhere '.' is a cwd path (6xl.78)
    cands, err = [], None
    if _DIGITS.fullmatch(arg):
        try:
            n = _num(arg)
            cands.append({"kind": "pr", "number": n, "url": None, "reading": _reading("pr", n)})
        except Refuse as e:
            err = e
    try:
        # shortcut: an all-digit arg under 7 chars is a PR number, not a hex abbreviation (git's default is 7); a commit needs 7+ digits or HEAD~N; a same-named path stays a second reading on purpose: the user is asked once
        if cands and len(arg) < 7:
            raise Refuse("bad-ref", "the target is not a commit in this repository")
        cid = _commit_id(arg, cwd)
    except Refuse as e:
        err = err or e
    else:
        cands.append({"kind": "branch", "base": arg, "head": "HEAD", "reading": "changes on HEAD since " + arg})
        cands.extend(_descendant_reading(arg, cid, cwd))
    if os.path.lexists(os.path.join(cwd, arg)):
        cands.append(_path_candidate([arg], cwd))
    if not cands:
        raise err
    return cands


def _descendant_reading(ref: str, cid: str, cwd: str) -> List[dict]:
    """HEAD is an ancestor of ref, so `ref...HEAD` is empty: offer `default...ref` as a second reading."""
    try:
        if cid == _commit_id("HEAD", cwd) or _run(["merge-base", "--is-ancestor", "HEAD", ref], cwd)[0] != 0:
            return []
    except Refuse:
        return []
    default = default_branch(cwd)
    if default and _commit_id(default[0], cwd) != cid:
        return [{"kind": "branch", "base": default[0], "head": ref,
                 "reading": "changes on %s since %s" % (ref, _q(default[0]))}]
    return []


def _path_candidate(paths: List[str], cwd: str) -> dict:
    if not all(os.path.lexists(os.path.join(cwd, p)) for p in paths):
        raise Refuse("mixed-target", "give one target: a ref or range, a PR number, paths, --staged, --unstaged or --repo")
    return {"kind": "path", "paths": list(paths), "reading": "files under " + ", ".join(paths)}


def _branch_ok(name: object, cwd: str) -> bool:
    return (isinstance(name, str) and bool(name) and not name.startswith("-") and "@{" not in name
            and _run(["check-ref-format", "--branch", name], cwd)[0] == 0)


def _oid_ok(value: object) -> bool:
    return isinstance(value, str) and OID.fullmatch(value) is not None


def _view_pr(cand: dict, cwd: str) -> dict:
    j = gh_json(["pr", "view", cand["url"] or str(cand["number"]), "--json", _PR_FIELDS], cwd)
    try:
        state, m = j["state"], PR_URL.fullmatch(j["url"])
        ok = (state in ("OPEN", "CLOSED", "MERGED") and m and j["number"] == cand["number"]
              and _oid_ok(j["baseRefOid"]) and _oid_ok(j["headRefOid"]) and _branch_ok(j["baseRefName"], cwd))
    except (KeyError, TypeError):
        ok = False
    if not ok:  # the reason reaches stdout, so it never comes from the forge's own text
        raise Refuse("gh-failed", "gh reported invalid pull request fields")
    if state != "OPEN":
        raise Refuse(state.lower(), "the pull request is " + state.lower())
    if j.get("isDraft"):
        raise Refuse("draft", "the author must mark it ready for review")
    return {"base": j["baseRefOid"], "head": j["headRefOid"], "branch": j["baseRefName"], "url": j["url"],
            "key": _forge_key(m.group(1), m.group(2)), "ref": "refs/pull/%d/head" % cand["number"], "notes": []}


def _view_mr(cand: dict, cwd: str) -> dict:
    j = glab_json(["mr", "view", cand["url"] or str(cand["number"]), "-F", "json"], cwd)
    try:
        state, refs, m = j["state"], j["diff_refs"], MR_URL.fullmatch(j["web_url"])
        ok = (state in ("opened", "closed", "merged", "locked") and m and j["iid"] == cand["number"]
              and _oid_ok(refs["base_sha"]) and _oid_ok(refs["head_sha"]) and _branch_ok(j["target_branch"], cwd))
    except (KeyError, TypeError):
        ok = False
    if not ok:
        raise Refuse("glab-failed", "glab reported invalid merge request fields")
    if state != "opened":
        raise Refuse(state, "the merge request is " + state)
    if j.get("draft") or j.get("work_in_progress"):
        raise Refuse("draft", "the author must mark it ready for review")
    return {"base": refs["base_sha"], "head": refs["head_sha"], "branch": j["target_branch"], "url": j["web_url"],
            "key": _forge_key(m.group(1), m.group(2)), "ref": "refs/merge-requests/%d/head" % cand["number"],
            "notes": ["merge request reading is untested against a live glab"]}


def _fetch(remote: str, refspecs: List[str], cwd: str) -> None:
    """Objects only: no destination, no FETCH_HEAD, no opportunistic ref update (empty --refmap=)."""
    rc, _, _ = _run(["-c", "transfer.fsckObjects=true",  # a tree naming `..` or `.git` is refused
                     "-c", "maintenance.auto=false", "-c", "gc.auto=0",  # no repack/prune of the user's objects
                     "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", "--refmap=",
                     "--", remote] + refspecs, cwd, net=True)
    if rc != 0:
        raise Refuse("fetch-failed", "git could not fetch the pull request objects")


def _have(oid: str, cwd: str) -> bool:
    return _run(["cat-file", "-e", oid + "^{commit}"], cwd)[0] == 0


# Changed-file listing: plumbing at the top level, so no subdirectory cwd narrows it.
_NAME_FLAGS = ["-r", "-z", "-M", "--numstat", "--no-ext-diff", "--no-textconv"]


def read_working(path: str, limit: int) -> Tuple[str, object]:
    """("ok", bytes) | ("symlink"|"special"|"missing", None) | ("too-large", size) for a working-copy file. It is
    opened without following a link or blocking on a FIFO, and judged on the open descriptor."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    except OSError as e:
        return ("symlink" if e.errno == errno.ELOOP else "missing"), None
    with os.fdopen(fd, "rb") as fh:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return "special", None
        data = fh.read(limit + 1) if st.st_size <= limit else b""
    return ("ok", data) if st.st_size <= limit and len(data) <= limit else ("too-large", max(st.st_size, len(data)))


def _special(path: str) -> bool:
    """A working-copy entry that is no regular file, symlink or directory (a FIFO, socket or device): git cannot
    diff it and reading it can block (6xl.90)."""
    try:
        m = os.lstat(path).st_mode
    except OSError:
        return False
    return not (stat.S_ISREG(m) or stat.S_ISLNK(m) or stat.S_ISDIR(m))


def _changed(sub: str, revs: List[str], top: str) -> Tuple[List[str], dict]:
    """(changed names, {new name: old name} renames) from `git <sub> --numstat -z`. A changed working-copy
    FIFO, socket or device is listed but kept out of the numstat run: git cannot hash it."""
    odd = [] if sub == "diff-tree" else [n for n in _split(git_bytes([sub, "-z", "--name-only"] + revs + ["--"], top))
                                          if _special(os.path.join(top, n))]
    keep_out = ["."] + [":(exclude,literal)" + n for n in odd] if odd else []
    raw = git_bytes([sub] + _NAME_FLAGS + revs + ["--"] + keep_out, top).decode("utf-8", "surrogateescape").split("\0")
    names, renames, i = odd, {}, 0
    while i < len(raw):
        f = raw[i].split("\t", 2)  # "added<TAB>deleted<TAB>path": a TAB inside the path stays in it (6xl.72)
        if len(f) == 3 and f[2] == "" and i + 2 < len(raw):  # a rename: "added<TAB>deleted<TAB>" NUL old NUL new
            names.append(raw[i + 2])
            renames[raw[i + 2]] = raw[i + 1]
            i += 3
            continue
        if len(f) == 3:
            names.append(f[2])
        i += 1
    return names, renames


def _plan_revs(base: str, head: str, mb: str, top: str, since: Optional[str] = None) -> dict:
    """`mb` is the trust anchor (rules, siblings, ledger context); `since`, when set, only starts the diff range."""
    start = since or mb
    names, renames = _changed("diff-tree", [start, head], top)
    return {"base": base, "head": head, "mb": mb, "names": names, "renames": renames, "untracked": [], "diff_base": since,
            "diff_cmd": ["git", "diff-tree", "-r"] + _DIFF_FLAGS + [start, head, "--"],
            "commits": git(["rev-list", "--reverse", start + ".." + head], top).split()}


def _last_review_head(info: dict, cwd: str) -> Optional[str]:
    """The head the poster's newest own review on this pull request was written for, or None (no such review, or the
    reviews could not be read: either way the caller reviews in full)."""
    m = PR_URL.fullmatch(info["url"])
    try:
        host = ["api", "--hostname", m.group(1)]
        me = gh_json(host + ["user"], cwd)["login"]
        reviews = []
        for no in range(1, 21):
            got = gh_json(host + ["repos/%s/pulls/%s/reviews?per_page=100&page=%d" % (m.group(2), m.group(3), no)], cwd)
            reviews += got
            if len(got) < 100:
                break
        return prior.read(reviews, [], me)["last_head"]
    except (Refuse, KeyError, TypeError, AttributeError, ValueError):
        return None


def _head_oid(cwd: str) -> Optional[str]:
    rc, out, _ = _run(["rev-parse", "--verify", "--quiet", "HEAD^{commit}"], cwd)
    return out.decode().strip() if rc == 0 else None  # None: an unborn HEAD


def _parent_key(kind: str, info: dict, cwd: str) -> Optional[str]:
    """The repository key of the one the change's base repository was forked from, or None when it is no fork."""
    host, path = info["key"].split("/", 1)
    if kind == "pr":
        j = gh_json(["repo", "view", "https://%s/%s" % (host, path), "--json", "isFork,parent"], cwd)
        p = j.get("parent") if isinstance(j, dict) else None
        o = p.get("owner") if isinstance(p, dict) else None
        name = "%s/%s" % (o.get("login"), p.get("name")) if isinstance(o, dict) and isinstance(o.get("login"), str) \
            and isinstance(p.get("name"), str) else None
    else:
        web = MR_URL.fullmatch(info["url"]).group(1)  # web host with its port; one argv, so no dash reads as a flag
        j = glab_json(["api", "--hostname=" + web, "projects/" + urllib.parse.quote(path, safe="")], cwd)
        p = j.get("forked_from_project") if isinstance(j, dict) else None
        name = p.get("path_with_namespace") if isinstance(p, dict) else None
    return _forge_key(host, name) if isinstance(name, str) else None


def _default_tip(remote: str, cwd: str) -> Tuple[str, str]:
    """(default branch, tip) of `remote`, read live with one ls-remote (nothing is written); remote-unreachable when unreadable."""
    rc, out, _ = _run(["ls-remote", "--symref", "--", remote, "HEAD"], cwd, net=True)
    rows = [ln.split("\t")[0] for ln in out.decode("utf-8", "surrogateescape").split("\n")]  # a ref name holds no LF, but may hold NEL, LS or PS
    branch = next((r[len("ref: refs/heads/"):] for r in rows if r.startswith("ref: refs/heads/")), None)
    tip = next((r for r in rows if OID.fullmatch(r)), None)
    if rc != 0 or not branch or not tip:
        raise Refuse("remote-unreachable", "the default branch tip could not be read live from " + _q(remote))
    return branch, tip


def _gate(remote: Optional[str], fork: Optional[str], cwd: str) -> None:
    """gh#49: a head that is not checked out is reviewed only from the main checkout on the upstream default branch at
    its tip, read live with one ls-remote (nothing is written); a fork's checkout may carry commits on that tip. For a fork
    `remote` is the parent's and `fork` the change's base repository: its default branch tip is read live too (a second
    ls-remote) and must contain the upstream tip, as must local HEAD. Nothing is fetched, so a fork tip this repository
    lacks cannot be tested and refuses."""
    if remote is None:
        raise Refuse("no-upstream-remote", "no local remote is the upstream repository")
    branch, tip = _default_tip(remote, cwd)
    linked = os.path.realpath(_rev_path("--absolute-git-dir", cwd)) != os.path.realpath(
        os.path.join(cwd, _rev_path("--git-common-dir", cwd)))
    on = _head_branch(cwd)
    synced = _run(["merge-base", "--is-ancestor", tip, "HEAD"], cwd)[0] == 0 if fork else _head_oid(cwd) == tip
    if synced and on == branch and not linked:
        if fork:
            fbranch, ftip = _default_tip(fork, cwd)
            if _run(["merge-base", "--is-ancestor", tip, ftip], cwd)[0] != 0:
                raise Refuse("checkout-not-synced", "the fork %s/%s (tip %s%s) does not contain the upstream tip %s of %s/%s; sync the fork first" % (
                    _q(fork), _q(fbranch), ftip[:12], "" if _have(ftip, cwd) else ", which this repository lacks", tip[:12], _q(remote), _q(branch)))
        return
    rc, out, _ = _run(["rev-list", "--left-right", "--count", tip + "...HEAD"], cwd)
    n = out.decode().split()
    dist = "%s behind and %s ahead" % tuple(n) if rc == 0 and len(n) == 2 else "behind by an unknown count" + (
        "" if _have(tip, cwd) else " (this repository lacks the tip)")  # no fetch to count: the gate writes nothing
    raise Refuse("checkout-not-synced", "%son %s, %s of the upstream tip %s of %s/%s; sync the main checkout first" % (
        "a linked worktree, " if linked else "", _q(on) if on else "a detached HEAD", dist, tip[:12], _q(remote), _q(branch)))


def _plan_forge(cand: dict, cwd: str, since_last: bool = False) -> dict:
    if cand.get("repo_key") and not any(k == cand["repo_key"] for _, k in _remotes(cwd)):
        raise Refuse("no-upstream-remote", "no configured remote matches the pull request's base repository")
    info = (_view_pr if cand["kind"] == "pr" else _view_mr)(cand, cwd)
    remote = next((n for n, k in _remotes(cwd) if k == info["key"]), None)
    if remote is None:
        raise Refuse("no-upstream-remote", "no configured remote matches the pull request's base repository")
    if info["head"] != _head_oid(cwd):
        parent = _parent_key(cand["kind"], info, cwd)
        _gate(next((n for n, k in _remotes(cwd) if k == parent), None) if parent else remote, remote if parent else None, cwd)
    _fetch(remote, [info["ref"], "refs/heads/" + info["branch"]], cwd)
    if not _have(info["head"], cwd):
        raise Refuse("head-moved", "the pull request head changed after it was read")
    if not _have(info["base"], cwd):
        _fetch(remote, [info["base"]], cwd)
        if not _have(info["base"], cwd):
            raise Refuse("fetch-failed", "the base commit is unavailable")
    mb = git(["merge-base", info["base"], info["head"]], cwd, check=False).strip()
    if not mb:
        raise Refuse("bad-ref", "no common ancestor")
    note = since = None
    if since_last:  # review only what changed since the poster's last review, or all of it, and say which
        last = _last_review_head(info, cwd)
        if last is None:
            note = "full review: no earlier review of this pull request by you could be read"
        elif not _have(last, cwd) or _run(["merge-base", "--is-ancestor", last, info["head"]], cwd)[0] != 0:
            note = "full review: the head of your last review is not an ancestor of this head (force-push or rebase)"
        elif git(["rev-list", "--merges", "--max-count=1", last + ".." + info["head"]], cwd, check=False).strip():
            note = "full review: another branch was merged into this pull request since your last review"
        else:
            since = last  # only the diff range starts here; rules and context stay on the true merge base
            note = "reviewing since your last review, %s..%s" % (last[:12], info["head"][:12])
    plan = _plan_revs(info["base"], info["head"], mb, cwd, since)
    plan.update(number=cand["number"], url=info["url"], notes=info["notes"] + ([note] if note else []), since_last=note)
    return plan


def _others(top: str) -> List[str]:
    return _split(git_bytes(["ls-files", "--others", "--exclude-standard", "-z"], top))


def _untracked(top: str, others: Optional[List[str]] = None) -> List[str]:
    """Untracked, non-ignored names. git prints a nested repository or worktree as `dir/`: a directory, never read as a
    file and never pinned (the documented "not seen: nested repositories" limit); _nested names it."""
    return [n for n in (_others(top) if others is None else others) if not n.endswith("/")]


def _nested(others: List[str]) -> List[str]:
    return [n[:-1] for n in others if n.endswith("/")]


def _head_plan(top: str, sub: str, extra: List[str], untracked: bool, diff_extra: List[str]) -> dict:
    """staged, unstaged and worktree: base = merge_base = head = HEAD, the change is the tree state."""
    head = _commit_id("HEAD", top)
    names, renames = _changed(sub, extra, top)
    raw = _others(top) if untracked else []
    other = _untracked(top, raw)
    return {"base": head, "head": head, "mb": head, "names": names, "renames": renames, "untracked": other,
            "nested": _nested(raw),
            "dirty": True,
            "diff_cmd": ["git", sub] + _DIFF_FLAGS + diff_extra + ["--"], "commits": []}


def _plan_files(paths: List[str], cwd: str, top: str) -> dict:
    """path and repo: the files themselves; no diff."""
    head = _commit_id("HEAD", top)
    root = os.path.realpath(top)
    specs = []
    for p in paths:
        rp = os.path.realpath(os.path.join(cwd, p))
        if rp != root and not rp.startswith(root + os.sep):
            raise Refuse("outside-repo", "the path is outside the repository")
        specs.append(os.path.relpath(rp, root))
    tracked = _split(git_bytes(["--literal-pathspecs", "ls-files", "-z", "--"] + specs, top))
    raw = _others(top)
    others = _untracked(top, raw)  # pin_tree pins them all
    # a literal pathspec, in Python: the path itself, or a directory above it ("." is the top)
    other = [n for n in others if not specs or any(sp == "." or n == sp or n.startswith(sp + "/") for sp in specs)]
    names = sorted(n for n in set(tracked) | set(other) if os.path.lexists(os.path.join(top, n)))
    present = set(names)
    explicit = {sp for sp in specs if not os.path.isdir(os.path.join(top, sp))}  # named files bypass every rule
    return {"base": head, "head": head, "mb": head, "names": names, "untracked": [n for n in other if n in present],
            "others": others, "diff_cmd": None, "commits": [], "explicit": explicit,
            "nested": [n for n in _nested(raw) if not specs or any(sp == "." or n == sp or n.startswith(sp + "/") for sp in specs)]}


def _plan_commit(rev: str, cwd: str) -> dict:
    head = _commit_id(rev, cwd)
    rc, out, _ = _run(["rev-parse", "--verify", "--quiet", head + "^1"], cwd)
    if rc == 0:
        base = out.decode("utf-8", "replace").strip()
    else:  # root commit: the empty tree of this repository's hash algorithm
        base = git(["hash-object", "-t", "tree", "--stdin"], cwd, stdin=b"").strip()
    plan = _plan_revs(base, head, base, cwd)
    plan["commits"] = [head]
    return plan


_MINIFIED = (".min.js", ".min.css", ".map")
_PB_NAMES = (".pb.go", "_pb2.py", ".snap")
_MARKER = re.compile(r"@generated|do not edit|auto-generated|automatically generated", re.I)
_LONG_LINE = 1000
_LONG_EXT = (".js", ".mjs", ".css", ".json")  # only these can be minified; a long line in .py is real source
def _scan(data: bytes) -> tuple:
    """(has NUL in the first 8000 bytes, longest line in characters, first ten lines, size in bytes)."""
    lines = data.split(b"\n")
    long_ = [l.rstrip(b"\r") for l in lines if len(l) > _LONG_LINE]
    longest = max((len(l.decode("utf-8", "replace")) for l in long_), default=0)
    return (b"\0" in data[:8000], longest, [l.decode("utf-8", "replace") for l in lines[:10]], len(data))


def cat_batch(lines: List[bytes], cwd: str, check: bool,
              limit: Optional[int] = None) -> Iterator[Tuple[List[bytes], Optional[bytes]]]:
    """(header fields, content) per input line of one `git cat-file --batch` run, read one object at a
    time so memory holds one object. Lines go in through a temporary file, so neither pipe can fill and
    deadlock. With `check`, a failing run refuses; a consumer that stops early is no failure (SIGPIPE). An
    object over `limit` bytes is read past in chunks and yields None (6xl.75)."""
    if not lines:
        return
    with tempfile.TemporaryFile() as fh:
        fh.write(b"".join(ln + b"\n" for ln in lines))
        fh.seek(0)
        p = subprocess.Popen(_GIT + ["cat-file", "--batch"], cwd=cwd, env=_env(cwd), stdin=fh,
                             stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        finished = False
        try:
            for _ in lines:
                head = p.stdout.readline()
                if not head:
                    break
                f = head.split()
                data = b""
                if len(f) >= 3 and f[-1].isdigit():  # "<oid> <type> <size>"; "<spec> missing" has no body
                    size = int(f[-1])
                    if limit is not None and size > limit:
                        while size > 0:
                            size -= len(p.stdout.read(min(size, 1 << 20)) or b"\0" * size)
                        data = None
                    else:
                        data = p.stdout.read(size)
                    p.stdout.read(1)
                yield f, data
            finished = True
        finally:
            p.stdout.close()
            rc = p.wait()
    if check and finished and rc != 0:
        raise Refuse("bad-ref", "a git command failed")


def tree_listing(rev: str, top: str) -> dict:
    """path -> (mode, oid) for every entry of a commit or tree, from `ls-tree -r -z`: exact names, any bytes."""
    out = {}
    for rec in git_bytes(["ls-tree", "-r", "-z", rev], top).split(b"\0"):
        meta, tab, path = rec.partition(b"\t")
        if tab:
            mode, _, oid = meta.split(b" ")
            out[path.decode("utf-8", "surrogateescape")] = (mode.decode(), oid.decode())
    return out


def index_listing(top: str) -> dict:
    """path -> (mode, oid) of each stage-0 index entry, from `ls-files -s -z`."""
    out = {}
    for rec in git_bytes(["ls-files", "-s", "-z"], top).split(b"\0"):
        meta, tab, path = rec.partition(b"\t")
        if tab:
            mode, oid, stage = meta.decode().split(" ")
            if stage == "0":
                out[path.decode("utf-8", "surrogateescape")] = (mode, oid)
    return out


def _blobs(listing: dict, names: List[str], top: str) -> dict:
    """{name: _scan(content)} for each name the listing holds as a blob, read by object id (6xl.82/.83): a
    name is never parsed by cat-file, so LF, CR and every other byte in it are harmless."""
    want = {n: listing[n][1] for n in names if n in listing and listing[n][0] != "160000"}
    uniq = sorted(set(want.values()))
    got = {}
    for oid, (f, data) in zip(uniq, cat_batch([o.encode() for o in uniq], top, False, MAX_FILE)):
        if len(f) >= 3 and f[-2] == b"blob":
            got[oid] = _scan(data) if data is not None else (False, 0, [], int(f[-1]))
    return {n: got[o] for n, o in want.items() if o in got}


def _files(names: List[str], top: str) -> dict:
    """_scan of regular working-tree files, read through read_working; a symlink, special or missing file is
    left out, and one over the cap gets only its size."""
    out = {}
    for n in names:
        status, got = read_working(os.path.join(top, n), MAX_FILE)
        if status == "ok":
            out[n] = _scan(got)
        elif status == "too-large":
            out[n] = (False, 0, [], got)
        elif status == "special":
            out[n] = None
    return out


def filter_files(kind: str, plan: dict, names: List[str], top: str) -> tuple:
    """(kept, excluded, notes): first matching rule wins; every exclusion carries a reason and a detail.

    Header and path rules apply only to files that exist at the base, so the change cannot hide its own code with a
    header marker, a directory name, a minified name or a long line (only lockfile names are dropped outright).
    """
    kept, excluded, notes, todo = [], [], [], []
    mb, explicit, nested = plan["mb"], plan.get("explicit", ()), set(plan.get("nested", ()))

    def drop(n, reason, detail):
        excluded.append({"path": n, "reason": reason, "detail": detail})

    working = kind in ("unstaged", "worktree", "path", "repo")
    # review-code-ea83: a tracked gitlink is "submodule" for every kind, as in ledger; mode 160000 in the index or head tree
    listing = index_listing(top) if kind == "staged" or working else tree_listing(plan["head"], top)
    base_listing = index_listing(top) if kind == "unstaged" else tree_listing(mb, top)  # 6xl.88: what the diff compares against
    links = {p for ls in (listing, base_listing) for p, (m, _) in ls.items() if m == "160000"}  # a removed gitlink too
    for n in names:
        base = n.rsplit("/", 1)[-1]
        if n in links:
            drop(n, "submodule", "a tracked gitlink (submodule), never read")
        elif n in nested:
            drop(n, "nested-repository", "an untracked nested repository or worktree, never read")
        elif working and os.path.isdir(os.path.join(top, n)) and not os.path.islink(os.path.join(top, n)):
            if n in listing:
                drop(n, "replaced-by-directory", "a tracked file replaced by a directory, never read")
            else:
                drop(n, "nested-repository", "a directory (nested repository or worktree), never read")
        elif n in explicit:
            todo.append(n)  # only the size cap applies to a file the user named
        elif base in _LOCKFILES:
            drop(n, "lockfile", "basename")
        else:
            todo.append(n)  # a minified name is judged below, with the "the change adds it" keep
    if todo:
        if kind in ("unstaged", "worktree", "path", "repo"):
            head = _files(todo, top)
        else:
            head = _blobs(listing, todo, top)
        base_ls = _blobs(base_listing, todo, top)
        for n in todo:
            parts = n.split("/")[:-1]
            migration = "migrations" in parts
            if n in head and head[n] is None:
                drop(n, "special", "not a regular file (FIFO, socket or device)")
                continue
            nul, longest, first, size = head.get(n, (False, 0, [], 0))
            size = max(size, base_ls.get(n, (False, 0, [], 0))[3])
            base = n.rsplit("/", 1)[-1]
            hit = None
            if size > MAX_FILE:  # 6xl.75: a resource limit, so it applies before every heuristic
                drop(n, "too-large", "%d bytes, over %d" % (size, MAX_FILE))
                continue
            if n in explicit:
                kept.append(n)
                continue
            if base.endswith(_MINIFIED):
                hit = ("minified", "name pattern", "minified")
            elif n in head and not nul and longest > _LONG_LINE and base.endswith(_LONG_EXT):
                hit = ("minified", "longest line %d" % longest, "minified")
            if hit is None and not migration:
                lines = base_ls[n][2] if n in base_ls else first
                mark = next((i for i, l in enumerate(lines, 1) if _MARKER.search(l)), 0)
                if mark:
                    hit = ("generated", "header line %d" % mark, "header")
                else:
                    for comp, why in (("dist", "generated"), ("build", "generated"), ("vendor", "vendored"),
                                      ("node_modules", "vendored")):
                        if comp in parts:
                            hit = (why, "path rule %s/" % comp, "path")
                            break
                    else:
                        suffix = next((x for x in _PB_NAMES if base.endswith(x)), None)
                        if suffix:
                            hit = ("generated", "path rule *" + suffix, "path")
            if hit is None and (nul or base_ls.get(n, (False,))[0]):
                hit = ("binary", "NUL byte", None)  # git's own rule: a NUL in the first 8000 bytes of either side
            if hit is None:
                kept.append(n)
            elif hit[2] and n not in base_ls:
                kept.append(n)
                notes.append("%s: kept; it matches the %s rule but the change adds it" % (shown(n), hit[2]))
            else:
                drop(n, hit[0], hit[1])
    return sorted(kept), sorted(excluded, key=lambda e: e["path"]), notes


def _diff_calls(plan: dict, kept: List[str], narrow: bool, top: str) -> List[List[str]]:
    """git argv lists whose outputs, in order, make the change diff, limited to kept files when some are excluded.

    shortcut: `git diff` has no --pathspec-from-file (git 2.56), so paths go in argv, in chunks under
    _ARGV_BUDGET; each chunk is its own diff, so sort order is per chunk. Fine until diffs span >60 kB of paths.
    """
    cmd = plan["diff_cmd"]
    if not cmd:
        return []
    if not narrow:
        return [cmd[1:]]
    skip, ren = set(plan["untracked"]), plan.get("renames", {})
    chunks, cur, size = [], [], 0
    for n in (n for n in kept if n not in skip):
        group = [n] + ([ren[n]] if n in ren else [])  # 6xl.67: both sides of a rename, or -M sees an add
        w = sum(len(g.encode("utf-8", "surrogateescape")) + 1 for g in group)
        if cur and size + w > _ARGV_BUDGET:
            chunks.append(cur)
            cur, size = [], 0
        cur += group
        size += w
    if cur:
        chunks.append(cur)
    return [["--literal-pathspecs"] + cmd[1:] + c for c in chunks]


_PATH_LINES = (b"diff --git ", b"--- ", b"+++ ", b"rename from ", b"rename to ", b"copy from ", b"copy to ")


def _diff_line(raw: bytes) -> str:
    """One diff line as text. A line that carries a path decodes with surrogateescape, like every listed name,
    so a non-UTF-8 name still matches its file (6xl.79); content keeps U+FFFD for undecodable bytes. LF is
    ASCII, so per-line decoding equals decoding the whole diff."""
    return raw.decode("utf-8", "surrogateescape" if raw.startswith(_PATH_LINES) else "replace")


def _diff_lines(calls: List[List[str]], top: str) -> Iterator[str]:
    """Output lines (no trailing LF) of each git call in turn, read as git writes them (6xl.66)."""
    for args in calls:
        p = subprocess.Popen(_GIT + args, cwd=top, env=_env(top), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            for raw in p.stdout:
                yield _diff_line(raw.rstrip(b"\n"))
        finally:
            p.stdout.close()
            rc = p.wait()
        if rc != 0:
            raise Refuse("bad-ref", "a git command failed")


def _diff_text(plan: dict, kept: List[str], narrow: bool, top: str) -> str:
    return "".join(ln + "\n" for ln in _diff_lines(_diff_calls(plan, kept, narrow, top), top))


def _sections(lines: Iterator[str]) -> Iterator[str]:
    """One file's diff text at a time: a section starts at each top-level `diff --git ` line (hunk lines
    always carry a ' ', '+', '-' or '\\' prefix, so no content line can start one)."""
    cur: List[str] = []
    for ln in lines:
        if ln.startswith("diff --git ") and cur:
            yield "".join(x + "\n" for x in cur)
            cur = []
        cur.append(ln)
    if cur:
        yield "".join(x + "\n" for x in cur)


def _write_index_tree(top: str, env: dict, records: bytes) -> str:
    """Load `records` (-z --index-info lines) into the index file `env` names, then write-tree it; no records, no load."""
    if records:
        git_bytes(["update-index", "-z", "--index-info"], top, env=env, stdin=records)
    return git(["write-tree"], top, env=env).strip()


def _write_diff(path: str, plan: dict, names: List[str], narrow: bool, kind: str, top: str,
                blobs: dict) -> Tuple[list, list]:
    """Strip and escape the change diff file by file into change.diff, so memory holds one file's diff and,
    for a tracked prose file, its whole-file-context diff (6xl.66). Returns (hidden chars, hidden text) with
    change.diff line numbers. blobs: pin_tree's {path: "mode oid"} of the files it hashed."""
    def untracked() -> Iterator[str]:
        # untracked files shown as additions: their pinned blobs, in one tree diffed against the empty tree, so the
        # cost is four git calls for any number of files and the diff shows the bytes the tree pins
        want = {u: blobs[u] for u in set(plan["untracked"]) & set(names) if u in blobs}
        if kind not in ("unstaged", "worktree") or not want:
            return
        env = {"GIT_INDEX_FILE": os.path.join(os.path.dirname(path), "untracked.index.tmp")}
        try:
            tree = _write_index_tree(top, env, b"".join(
                ("%s\t" % want[u]).encode() + u.encode("utf-8", "surrogateescape") + b"\0" for u in sorted(want)))
        finally:
            if os.path.exists(env["GIT_INDEX_FILE"]):
                os.remove(env["GIT_INDEX_FILE"])
        empty = git(["hash-object", "-t", "tree", "--stdin"], top, stdin=b"").strip()
        # --text (in _DIFF_FLAGS): an untracked `* -diff` must not hide text (6xl.65); NUL-byte files are excluded already
        yield from _sections(_diff_lines([["diff-tree", "-r"] + _DIFF_FLAGS + [empty, tree, "--"]], top))

    tracked = _sections(_diff_lines(_diff_calls(plan, names, narrow, top), top))
    wide = dict(plan, diff_cmd=[_WIDE if a == "--unified=3" else a for a in plan["diff_cmd"]]) if plan["diff_cmd"] else None
    hidden, stripped, offset = [], [], 0
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
        for is_tracked, sections in ((True, tracked), (False, untracked())):
            for sec in sections:
                full = None
                if is_tracked and wide:  # CTX-04: whole-file context tells strip_hidden_diff where a comment opens
                    paths = {p for _, p, tag, *_ in sanitize.walk(sec) if p and sanitize.is_prose_file(p)}
                    paths |= {o for *_, o in sanitize.walk(sec) if o and sanitize.is_prose_file(o)}
                    prose = [n for n in names if n in paths and n not in plan["untracked"]]
                    full = _diff_text(wide, prose, True, top) if prose else None
                text, st = sanitize.strip_hidden_diff(sec, full)
                text, hc = sanitize.escape_for_code(text)
                stripped += [dict(h, line=h["line"] + offset) for h in st]
                hidden += [dict(h, line=h["line"] + offset) for h in hc]
                offset += text.count("\n")
                fh.write(text)
    return hidden, stripped


WHOLE = ("path", "repo")  # targets that are a tree, not a change: no diff, no provenance, every line under review
INPLACE = ("staged", "unstaged", "worktree", "path", "repo")
REGULAR = ("100644", "100755")  # the modes of a regular file: never a link (120000) or a submodule (160000)
_RAW_DIFF = ["--raw", "--no-abbrev", "-z", "--no-renames", "--no-relative", "--no-ext-diff", "--no-color", "--ignore-submodules=none"]


def _c_quoted(path: str) -> bytes:
    """A path as one C-quoted line, which `hash-object --stdin-paths` unquotes: an LF anywhere in it, the repository's
    and the run directory's parts included, stays inside the line, and a CR before the closing quote is no line end."""
    b = path.encode("utf-8", "surrogateescape")
    return b'"' + b.replace(b"\\", b"\\\\").replace(b'"', b'\\"').replace(b"\n", b"\\n") + b'"'


def pin_tree(kind: str, plan: dict, run: str, top: str) -> dict:
    """The pinned tree and the checkout difference: {pinned_tree, pinned_head, checkout_head, paths: {path: tag},
    blobs: {path: "mode oid"} of each working file it hashed}.

    Ref and PR kinds pin the head commit's tree. In-place kinds pin a tree written from a copy of the index in `run`,
    with the kind's working files hashed raw (no filter runs, the stored bytes are the bytes on disk); the checkout, its
    index and its refs are only read. The difference tags each path where the checkout differs from the pinned tree:
    `modified`, `untracked` (in the checkout, not ignored, not in the tree) or `absent-in-checkout`; in place it holds
    only the worktree edits of `staged` (its tree is the index) and files the tree could not take. Its cost follows the
    change and the drift: one raw diff, one untracked listing, one config read in place, at most one hash-object and one
    update-index; with a filter driver configured, one ls-files and one check-attr more, and every filtered file read."""
    idx = os.path.join(run, "index.tmp")
    real = os.path.join(top, _rev_path("--git-path", top, "index"))
    if os.path.exists(real):
        shutil.copy2(real, idx)  # keeps the mtime git judges racy entries by; a refresh writes the copy, never the index
    env = {"GIT_INDEX_FILE": idx}
    inplace = kind in INPLACE
    tree = None if inplace else git(["rev-parse", "--verify", plan["head"] + "^{tree}"], top).strip()
    # in place: the working tree against the index, the edits the kind pins or leaves; else against the pinned tree
    f = _split(git_bytes(["diff"] + _RAW_DIFF + ([tree] if tree else []) + ["--"], top, env=env))
    edits = [(m.split()[4], p) for m, p in zip(f[0::2], f[1::2])]  # ":<index mode> <mode> <oid> <oid> <status>"
    imode = {p: m[1:7] for m, p in zip(f[0::2], f[1::2]) if m[1:7] in REGULAR}
    other = plan["untracked"] if kind in ("unstaged", "worktree") else plan["others"] if "others" in plan else \
        _untracked(top)  # path and repo listed theirs already
    tmp, blobs, idxmode = [], {}, {}
    try:
        if not inplace:
            paths = {p: "absent-in-checkout" if s == "D" else "untracked" if s == "A" else "modified" for s, p in edits}
            # shortcut: an untracked file at a pinned path is tagged modified unread; compare blobs if a tag must be exact
            paths.update((p, "modified" if p in paths else "untracked") for p in other)
        else:
            paths = {p: "absent-in-checkout" if s == "D" else "modified" for s, p in edits}
            paths.update((p, "untracked") for p in other)
            if kind != "staged" and _NO_FILTERS.get(top):  # a filter is configured: an unedited file's blob is filtered
                # "<mode> <oid> <stage>\t<path>" per entry (an unmerged path repeats); the raw diff lists only edits, so an
                # unedited file's index mode comes from here, which core.fileMode=false makes the only mode that counts
                ent = [(e[:6], e.partition("\t")[2]) for e in _split(git_bytes(["ls-files", "-s", "-z"], top, env=env))]
                for m, p in ent:
                    if m in REGULAR:
                        idxmode.setdefault(p, m)
                attrs = _split(git_bytes(["check-attr", "-z", "--stdin", "filter"], top, stdin="".join(
                    p + "\0" for p in dict.fromkeys(p for _, p in ent)).encode("utf-8", "surrogateescape")))
                paths.update((p, "modified") for p, v in zip(attrs[0::3], attrs[2::3])
                             if v not in ("unspecified", "unset") and p not in paths)  # so it is hashed as on disk
            # staged pins the index as it is; every other in-place kind pins the whole checkout, so its difference is empty
            take = [] if kind == "staged" else sorted(paths)
            zero, srcs = "0" * len(plan["head"]), []
            # core.fileMode=false: the exec bit on disk means nothing, so a tracked file keeps its index mode, a new one 100644
            trust = git(["config", "--type=bool", "--get", "core.fileMode"], top, check=False).strip() != "false"
            # an unmerged path has no stage-0 entry to pin: the staged tree leaves it out, and it stays in the difference
            unmerged = {p: "0 " + zero for s, p in edits if s == "U"} if kind == "staged" else {}
            recs = dict(unmerged)
            for p in take:
                try:
                    st = os.lstat(os.path.join(top, p))
                except OSError:
                    if paths[p] != "untracked":
                        recs[p] = "0 " + zero  # gone from the working tree: gone from the pinned tree
                    continue
                src, link = os.path.join(top, p), stat.S_ISLNK(st.st_mode)
                if not link and (not stat.S_ISREG(st.st_mode) or st.st_size > MAX_FILE):
                    continue  # a directory, FIFO, socket or oversized file stays as the index has it, and in the difference
                if link:  # --stdin-paths follows a link: its target string goes by copy
                    data = os.fsencode(os.readlink(src))
                    src = os.path.join(run, "hash.%d.tmp" % len(tmp))
                    write_atomic(src, data)
                    tmp.append(src)
                srcs.append((p, "120000" if link else ("100755" if st.st_mode & 0o100 else "100644") if trust
                             else imode.get(p) or idxmode.get(p, "100644"), src))
            if srcs:
                oids = git(["hash-object", "-w", "--no-filters", "--stdin-paths"], top,
                           stdin=b"".join(_c_quoted(s) + b"\n" for _, _, s in srcs)).split()
                if len(oids) != len(srcs):
                    raise Refuse("bad-ref", "a git command failed")
                blobs = {p: "%s %s" % (mode, oid) for (p, mode, _), oid in zip(srcs, oids)}
                recs.update(blobs)
            tree = _write_index_tree(top, env, b"".join(
                ("%s\t" % recs[p]).encode() + p.encode("utf-8", "surrogateescape") + b"\0" for p in sorted(recs)))
            paths = {p: t for p, t in paths.items() if p not in recs or p in unmerged}
    finally:
        for t in tmp + [idx]:
            if os.path.exists(t):
                os.remove(t)
    return {"pinned_tree": tree, "pinned_head": None if inplace else plan["head"],
            "checkout_head": _head_oid(top), "paths": paths, "blobs": blobs}


KNOWLEDGE = (".wolf", ".codegraph", ".serena")  # tool knowledge directories (gh#51); changes there are informational
CAUSE = "changed during the run; hooks, index daemons or other sessions may be the cause"


UNTRACKED_CAP = 5000  # untracked files itemised in the snapshot; above it only a count and a digest are kept
TRACKED_CAP = 5000  # the same for changed tracked files (status records 1, 2 and u), a mass rename for example


def _knowledge(p: str) -> bool:
    return any(p == d or p.startswith(d + "/") for d in KNOWLEDGE)


def _stat(path: str):
    try:
        st = os.lstat(path)
        return [st.st_size, st.st_mtime_ns]
    except OSError:
        return None


def integrity(top: str) -> dict:
    """The integrity snapshot (gh#51): a digest of one `git status --porcelain=v2 -z`, a digest of `git ls-files -s -v -z`,
    each status entry's fields with its (size, mtime_ns) so a second edit to a file already dirty shows, and (size,
    mtime_ns) of every file under the knowledge directories, walked by stat only. Clean tracked files cost nothing;
    no file content is read. Untracked files are listed one by one (--untracked-files=all), so an edit inside an untracked directory shows; the cost is one lstat
    per untracked, non-ignored file. Above UNTRACKED_CAP untracked files they are not itemised: the snapshot holds
    `untracked` {count, digest of the sorted (path, size, mtime_ns)}, so an edit still shows, but not which file.
    Above TRACKED_CAP changed tracked files (tool knowledge aside) `tracked` holds the same over their (path, row).
    A failed status or an unreadable index is recorded as it is, never raised."""
    out = git_bytes(["status", "--porcelain=v2", "-z", "--untracked-files=all", "--ignore-submodules=dirty"], top, check=False)
    dirty, unt, trk, recs = {}, [], [], out.decode("utf-8", "surrogateescape").split("\0")
    skip = False
    for r in recs:
        if skip or not r:
            skip = False
            continue
        n = {"1": 8, "2": 9, "u": 10}.get(r[0], 1)  # fields before the path; a rename record's next field is its source
        f, skip = r.split(" ", n), r[0] == "2"
        if len(f) > n and r[0] == "?" and not _knowledge(f[n]):  # tool knowledge is informational, not part of the digest
            unt.append(f[n])
        elif len(f) > n:
            row = [" ".join(f[:n]), _stat(os.path.join(top, f[n]))]
            if r[0] == "?" or _knowledge(f[n]):
                dirty[shown(f[n])] = row
            else:
                trk.append((f[n], row))
    if len(trk) <= TRACKED_CAP:
        dirty.update((shown(p), v) for p, v in trk)
    dig = hashlib.sha256()  # streamed, never itemised: the whole untracked set in one value
    for p in sorted(unt):
        st = _stat(os.path.join(top, p))
        dig.update(("%s\0%s\n" % (p, st)).encode("utf-8", "surrogateescape"))
        if len(unt) <= UNTRACKED_CAP:
            dirty[shown(p)] = ["?", st]
    # entries (mode, oid, stage, path) and the -v tag, which carries the assume-unchanged and skip-worktree flags that
    # hide an edit from status (87o1.10); no stat data, so a stat-only refresh is no change
    rc, ls, _ = _run(["ls-files", "-s", "-v", "-z"], top)
    index = hashlib.sha256(ls).hexdigest() if rc == 0 else None
    know = {}
    for d in KNOWLEDGE:
        for root, _, files in os.walk(os.path.join(top, d)):
            for f in files:
                p = os.path.join(root, f)
                know[shown(os.path.relpath(p, top).replace(os.sep, "/"))] = _stat(p)
    snap = {"version": 2, "status": hashlib.sha256(out).hexdigest(), "index": index, "dirty": dirty, "knowledge": know}
    if len(unt) > UNTRACKED_CAP:
        snap["untracked"] = {"count": len(unt), "digest": dig.hexdigest()}
    if len(trk) > TRACKED_CAP:
        snap["tracked"] = {"count": len(trk), "digest": hashlib.sha256("".join(
            "%s\0%s\n" % r for r in sorted(trk)).encode("utf-8", "surrogateescape")).hexdigest()}
    return snap


def integrity_changes(top: str, snap: dict) -> dict:
    """What changed since `snap` (gh#51): working-tree paths and the index are warnings, knowledge paths informational.
    Paths are stored and returned escaped (shown); a knowledge path the status lists is reported only as knowledge."""
    now = json.loads(json.dumps(integrity(top)))  # lists, as the stored snapshot holds them

    def kind(p, v):  # which capped set a non-knowledge row belongs to
        return None if _knowledge(p) else "untracked" if v[0] == "?" else "tracked"

    # a set over its cap on either side: its rows are dropped from both and the digests compared instead
    caps = {k: (now.get(k) or {}).get("count", sum(1 for p, v in now["dirty"].items() if kind(p, v) == k))
            for k in ("tracked", "untracked") if k in snap or k in now}
    if caps:
        snap, now = ({**d, "dirty": {p: v for p, v in d["dirty"].items() if kind(p, v) not in caps}} for d in (snap, now))

    def moved(key):
        a, b = snap[key], now[key]
        return sorted(p for p in set(a) | set(b) if a.get(p) != b.get(p))

    same = snap.get("version") == now["version"]  # 87o1.11: `index` digests another thing in another version
    res = {"working_tree": [p for p in moved("dirty") if not _knowledge(p)], "index": same and snap["index"] != now["index"],
           "knowledge": moved("knowledge"), "cause": CAUSE}
    if not same:
        res["version_note"] = "index not compared: snapshot from another version"
    for k, n in caps.items():  # a side under the cap holds no digest, so it differs from a side over it (the counts differ too)
        res[k] = {"count": n, "changed": snap.get(k) != now.get(k)}
    return res


def _checkout_root(top: str, head: str) -> str:
    """top when it has head checked out, else the first other worktree that does, else top (fix mode then refuses)."""
    held = []
    for blk in git_bytes(["worktree", "list", "--porcelain"], top, check=False).decode("utf-8", "surrogateescape").split("\n\n"):
        f = {k: v for k, _, v in (ln.partition(" ") for ln in blk.split("\n"))}
        if f.get("HEAD") == head and "bare" not in f and "prunable" not in f and os.path.isdir(f.get("worktree", "")):
            held.append(os.path.realpath(f["worktree"]))
    return os.path.realpath(top) if os.path.realpath(top) in held or not held else held[0]


def pin(candidate: dict, cwd: str, since_last: bool = False) -> dict:
    top = _toplevel(cwd)
    kind = candidate["kind"]
    if since_last and kind != "pr":
        raise Refuse("since-last-unsupported", "--since-last works on GitHub pull requests only")
    if kind in ("pr", "mr"):
        plan = _plan_forge(candidate, top, since_last)
    elif kind == "commit":
        plan = _plan_commit(candidate["rev"], top)
    elif kind in ("branch", "range"):
        base = _commit_id(candidate["base"], cwd)
        head = _commit_id(candidate["head"], cwd)
        mb = git(["merge-base", base, head], cwd, check=False).strip()
        if not mb:
            raise Refuse("bad-ref", "no common ancestor")
        plan = _plan_revs(base, head, mb, top)
    elif kind == "staged":
        head = _commit_id("HEAD", top)
        plan = _head_plan(top, "diff-index", ["--cached", head], False, ["--cached", head])
    elif kind == "unstaged":
        plan = _head_plan(top, "diff-files", [], True, [])
    elif kind == "worktree":
        head = _commit_id("HEAD", top)
        plan = _head_plan(top, "diff-index", [head], True, [head])
    elif kind in WHOLE:
        plan = _plan_files(candidate.get("paths", []), cwd, top)
    else:
        raise Refuse("bad-ref", "unknown target kind")
    if kind in ("commit", "branch", "range") and plan["head"] != _head_oid(top):  # forge kinds gate in _plan_forge
        if _symref(top, "refs/remotes/origin/HEAD") is None or \
                _run(["config", "--get", "remote.origin.url"], top)[0] != 0:  # no url: ls-remote reads a path `origin`
            raise Refuse("no-upstream-remote", "the upstream is the remote behind origin/HEAD, and there is none "
                                               "(`git remote set-head origin --auto` sets it)")  # gh#49
        _gate("origin", None, top)
    names = sorted(set(plan["names"]) | set(plan["untracked"]))
    if not names:
        raise Refuse("no-files" if kind in WHOLE else "empty-diff",
                     "the target has no files" if kind in WHOLE else "the change has no differences")
    names, excluded, fnotes = filter_files(kind, plan, names + plan.get("nested", []), top)  # a nested repo is no change
    if not names:
        counts = {}
        for e in excluded:
            counts[e["reason"]] = counts.get(e["reason"], 0) + 1
        sizes = [e["detail"].split(" bytes")[0] for e in excluded if e["reason"] == "too-large"]
        raise Refuse("only-excluded", "every changed file is excluded: " + ", ".join(  # 6xl.89: sizes, never names
            "%d %s" % (n, r) + (" (%s bytes, over %d)" % (", ".join(sizes), MAX_FILE) if r == "too-large" else "")
            for r, n in sorted(counts.items())))
    run = new_run_dir(cwd)
    integ = os.path.join(run, "integrity.json")  # gh#51: compared by report.py render, after the verifiers
    snap = integrity(top)
    write_private(integ, json.dumps(snap, sort_keys=True))
    pt = pin_tree(kind, plan, run, top)  # first: change.diff shows the untracked bytes the tree pins, read once
    hidden, stripped = _write_diff(os.path.join(run, "change.diff"), plan, names, bool(excluded), kind, top, pt["blobs"])
    # 6xl.51: names reach target.json escaped; a name that hides characters is itself a finding
    marks = [(sanitize.escape_for_code(n), n) for n in names + [e["path"] for e in excluded]]
    named = [{"file": esc, "name": True, "codepoints": [c for h in hits for c in h["codepoints"]]}
             for (esc, hits), _ in marks if hits]
    diff_path = os.path.join(run, "difference.json")
    write_private(diff_path, json.dumps({"version": 1, "pinned_tree": pt["pinned_tree"], "paths": [
        {"path": shown(p), "tag": pt["paths"][p]} for p in sorted(pt["paths"])]}, sort_keys=True, indent=2,
        ensure_ascii=False))  # lanes Grep it for a path as written (gh#52); shown() leaves no lone surrogate
    list_path = os.path.join(run, "difference.txt")  # what lanes Grep: one path per line, so ^path$ anchors and no JSON escape hides a name
    write_private(list_path, "".join(p + "\n" for p in sorted(
        shown(p).replace("\n", "<U+000A>").replace("\r", "<U+000D>") for p in pt["paths"])))
    rec = {
        "difference_list": list_path, "pinned_tree": pt["pinned_tree"], "pinned_head": pt["pinned_head"], "checkout_head": pt["checkout_head"],
        "difference": diff_path, "difference_count": len(pt["paths"]), "integrity": integ,
        "version": 1, "kind": kind, "repo": top, "root": _checkout_root(top, plan["head"]), "run": run, "arg": shown(candidate.get("arg", "")),
        "number": plan.get("number"), "since_last": plan.get("since_last"), "url": shown(plan["url"]) if plan.get("url") else plan.get("url"),  # 6xl.85
        "base_sha": plan["base"], "head_sha": plan["head"], "merge_base": plan["mb"],
        "diff_base": plan.get("diff_base"),
        # a tracked entry in the snapshot's status, itemised or capped (87o1.9), is dirty already; only a clean one needs --ignore-submodules=none
        "dirty": plan.get("dirty") or any(v[0] != "?" for v in snap["dirty"].values()) or "tracked" in snap or bool(git(["status", "--porcelain", "--untracked-files=no", "--ignore-submodules=none"], top).strip()),
        "diff_cmd": plan["diff_cmd"], "commits": plan["commits"],
        "included": [shown(n) for n in names], "excluded": [dict(e, path=shown(e["path"])) for e in excluded],
        "untracked": sorted(shown(n) for n in plan["untracked"]),
        "hidden_chars": [{"file": "change.diff", **h} for h in hidden] + named,
        "hidden_text": [{**h, "path": shown(h["file"]), "file": "change.diff"} for h in stripped], "notes": plan.get("notes", []) + fnotes,
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_private(os.path.join(run, "target.json"), json.dumps(rec, sort_keys=True, indent=2))
    return rec


def emit(obj: dict, keys=None) -> None:
    print(json.dumps({k: v for k, v in obj.items() if k in (keys or STDOUT_KEYS)}, sort_keys=True))


def _parse_cli(argv: List[str]) -> tuple:
    args, choice, it = [], None, iter(argv)
    for a in it:
        if a != "--choice" and not a.startswith("--choice="):
            args.append(a)
            continue
        v = a[len("--choice="):] if a.startswith("--choice=") else next(it, "")
        if not _DIGITS.fullmatch(v):
            raise Refuse("bad-choice", "--choice takes the number of a listed reading")
        choice = int(v)
    return args, choice


def main(argv: List[str], cwd: Optional[str] = None) -> int:
    cwd = cwd or os.getcwd()
    try:
        try:
            git(["rev-parse", "--show-toplevel"], cwd)
        except Refuse:
            raise Refuse("not-a-git-repo", "run inside a git repository")
        _NO_FILTERS.clear()  # read the filter config afresh each run, as an in-process caller may have changed it
        if "--stdin" in argv:  # WR-08: words as data, one per line, so no shell layer parses them
            argv = [a for a in argv if a != "--stdin"] + [w for w in sys.stdin.read().split("\n") if w]
        since_last = "--since-last" in argv
        args, choice = _parse_cli([a for a in argv if a != "--since-last"])
        cands = resolve(args, cwd)
        if choice is None and len(cands) > 1:
            emit({"status": "ambiguous", "candidates": [
                {"index": i, "kind": c["kind"], "reading": shown(c["reading"])} for i, c in enumerate(cands, 1)]})
            return 2
        if choice is not None and not 1 <= choice <= len(cands):
            raise Refuse("bad-choice", "the choice is not one of the listed readings")
        cand = cands[(choice or 1) - 1]
        cand["arg"] = " ".join(args)
        rec = pin(cand, cwd, since_last)
    except Refuse as r:
        emit({"status": "skip" if r.reason in SKIP_REASONS else "refuse", "reason": r.reason, "detail": r.detail})
        return 1
    except (OSError, KeyError, ValueError, TypeError) as e:  # the skill reads one JSON line; never a traceback
        emit({"status": "refuse", "reason": "internal-error", "detail": type(e).__name__})
        return 1
    emit({"status": "ok", "kind": rec["kind"], "base_sha": rec["base_sha"], "head_sha": rec["head_sha"],
          "merge_base": rec["merge_base"], "number": rec["number"], "run": rec["run"],
          "included_count": len(rec["included"]), "excluded_count": len(rec["excluded"]),
          "untracked_count": len(rec["untracked"]), **({"since_last": rec["since_last"]} if rec["since_last"] else {})})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
