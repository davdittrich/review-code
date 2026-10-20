"""Pin the review target to full SHAs and create the run directory.

Stdlib only, Python 3.9 grammar. Prints one JSON object with fixed keys; repository-sourced text
(file names, notes, messages) goes to run-dir files only, never to stdout.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import errno
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from typing import Iterator, List, Optional, Tuple

import sanitize

MAX_PREVIEW = 25000  # Claude Code saves Bash output over 30000 characters (its default) to a file, unseen by the user
STDOUT_KEYS = {"status", "reason", "detail", "kind", "base_sha", "head_sha", "merge_base", "number", "run",
               "included_count", "excluded_count", "untracked_count", "candidates"}
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
_MAX_FILE = 4_000_000
_ARGV_BUDGET = 60000  # bytes of path arguments per git call; far below the kernel limit
_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # SHA-1 or SHA-256, lowercase only
_PR_URL = re.compile(r"https?://([^/\s]+)/(.+?)/pull/([0-9]+)(?:[/?#].*)?", re.S)
_MR_URL = re.compile(r"https?://([^/\s]+)/(.+?)/-/merge_requests/([0-9]+)(?:[/?#].*)?", re.S)
_DIGITS = re.compile(r"[0-9]+")
_REMOTE_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([^/:]+)(?::[0-9]+)?[:/](.+?)/?", re.I | re.S)
_PR_FIELDS = "state,isDraft,baseRefOid,headRefOid,baseRefName,url,number"


class Refuse(Exception):
    def __init__(self, reason: str, detail: str = ""):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


def _env() -> dict:
    return dict(os.environ, GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")  # no prompt, no index write


def _run(args: List[str], cwd: str, stdin: Optional[bytes] = None):
    p = subprocess.run(_GIT + list(args), cwd=cwd, env=_env(), input=stdin,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return p.returncode, p.stdout, p.stderr


def git_bytes(args: List[str], cwd: str, stdin: Optional[bytes] = None, check: bool = True) -> bytes:
    rc, out, _ = _run(args, cwd, stdin)
    if check and rc != 0:
        raise Refuse("bad-ref", "a git command failed")
    return out if rc == 0 else b""


def git(args: List[str], cwd: str, stdin: Optional[bytes] = None, check: bool = True) -> str:
    return git_bytes(args, cwd, stdin, check).decode("utf-8", "replace")


CLI_TIMEOUT = 120  # seconds per gh or glab call


def _cli_json(tool: str, args: List[str], cwd: str):
    try:
        p = subprocess.run([tool] + list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           timeout=CLI_TIMEOUT)
    except FileNotFoundError:
        raise Refuse(tool + "-missing", tool + " is not installed")
    except subprocess.TimeoutExpired:  # a hung gh or glab would block the run for ever
        raise Refuse(tool + "-failed", "timeout")
    if p.returncode != 0:
        raise Refuse(tool + "-failed", tool + " exited with an error")
    try:
        return json.loads(p.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise Refuse(tool + "-failed", tool + " printed invalid JSON")


def gh_json(args: List[str], cwd: str):
    return _cli_json("gh", args, cwd)


def glab_json(args: List[str], cwd: str):
    return _cli_json("glab", args, cwd)


def load_target(run: str) -> dict:
    """<run>/target.json, or Refuse("no-target") when it is missing or lacks what every consumer reads."""
    try:
        with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
            tj = json.load(fh)
        tj["repo"], tj["head_sha"], tj["url"]
        if isinstance(tj["kind"], str) and isinstance(tj["included"], list):
            return tj
    except (OSError, ValueError, KeyError, TypeError):
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


def write_private(path: str, text: str) -> None:
    """Create or truncate `path` with mode 0600 (the run-directory layout promises owner-only files)."""
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
        fh.write(text)


RETENTION_DAYS = 14


def _prune(base: str) -> None:
    """IN-01: remove run directories not touched for RETENTION_DAYS. Runs before the new one is made, so the
    current run is never a candidate; links and other names are left alone; a removal that fails is skipped."""
    cutoff = time.time() - RETENTION_DAYS * 86400
    for name in os.listdir(base):
        path = os.path.join(base, name)
        try:
            st = os.lstat(path)
            if name.startswith("review-code.") and stat.S_ISDIR(st.st_mode) and st.st_mtime < cutoff:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def new_run_dir(cwd: str) -> str:
    """Mode 0700 directory under the git dir: a read-only lane cannot read $TMPDIR (probe A3)."""
    base = git(["rev-parse", "--git-path", "review-code"], cwd).strip()
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
    rc, out, err = _run(["rev-parse", "--verify", rev + "^{commit}"], cwd)
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
    for line in git(["config", "--get-regexp", r"^remote\..*\.url$"], cwd, check=False).splitlines():
        parts = line.split(None, 1)
        if len(parts) == 2:
            name = parts[0][len("remote."):-len(".url")]
            key = _repo_key(parts[1])
            if key and name and not name.startswith("-"):
                rows.append((name, key))
    return rows


def _shown(name: str) -> str:
    """A file name as run-dir files show it: hidden characters escaped (escape_for_code)."""
    return sanitize.escape_for_code(name)[0]


def _q(name: str) -> str:
    """A ref name from the repository, quoted unless plainly safe: `reading` text goes to stdout."""
    return name if re.fullmatch(r"[A-Za-z0-9._/+@-]+", name) else json.dumps(_shown(name))


def default_branch(cwd: str) -> Optional[tuple]:
    """(ref usable in git, local branch name) of the default branch, or None."""
    rc, out, _ = _run(["symbolic-ref", "--quiet", "refs/remotes/origin/HEAD"], cwd)
    if rc == 0:
        ref = out.decode("utf-8", "replace").strip()[len("refs/remotes/"):]
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
    rc, out, _ = _run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd)
    if default and rc == 0 and out.decode("utf-8", "replace").strip() != default[1]:
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
    m = _PR_URL.fullmatch(arg)
    if m:
        return [_forge_candidate("pr", m)]
    m = _MR_URL.fullmatch(arg)
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
    if arg == "." and os.path.realpath(cwd) == os.path.realpath(git(["rev-parse", "--show-toplevel"], cwd).strip()):
        return [{"kind": "repo", "reading": "the whole repository"}]  # elsewhere '.' is a cwd path (6xl.78)
    cands, err = [], None
    if _DIGITS.fullmatch(arg):
        try:
            n = _num(arg)
            cands.append({"kind": "pr", "number": n, "url": None, "reading": _reading("pr", n)})
        except Refuse as e:
            err = e
    try:
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
    return isinstance(value, str) and _OID.fullmatch(value) is not None


def _view_pr(cand: dict, cwd: str) -> dict:
    j = gh_json(["pr", "view", cand["url"] or str(cand["number"]), "--json", _PR_FIELDS], cwd)
    try:
        state, m = j["state"], _PR_URL.fullmatch(j["url"])
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
        state, refs, m = j["state"], j["diff_refs"], _MR_URL.fullmatch(j["web_url"])
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
                     "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", "--refmap=",
                     "--", remote] + refspecs, cwd)
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


def _plan_revs(base: str, head: str, mb: str, top: str) -> dict:
    names, renames = _changed("diff-tree", [mb, head], top)
    return {"base": base, "head": head, "mb": mb, "names": names, "renames": renames, "untracked": [],
            "diff_cmd": ["git", "diff-tree", "-r"] + _DIFF_FLAGS + [mb, head, "--"],
            "commits": git(["rev-list", "--reverse", mb + ".." + head], top).split()}


def _plan_forge(cand: dict, cwd: str) -> dict:
    if cand.get("repo_key") and not any(k == cand["repo_key"] for _, k in _remotes(cwd)):
        raise Refuse("cross-repo", "the pull request belongs to a repository with no configured remote")
    info = (_view_pr if cand["kind"] == "pr" else _view_mr)(cand, cwd)
    remote = next((n for n, k in _remotes(cwd) if k == info["key"]), None)
    if remote is None:
        raise Refuse("cross-repo" if cand.get("repo_key") else "no-remote-for-pr-repo",
                     "no configured remote matches the pull request repository")
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
    plan = _plan_revs(info["base"], info["head"], mb, cwd)
    plan.update(number=cand["number"], url=info["url"], notes=info["notes"])
    return plan


def _head_plan(top: str, sub: str, extra: List[str], untracked: bool, diff_extra: List[str]) -> dict:
    """staged, unstaged and worktree: base = merge_base = head = HEAD, the change is the tree state."""
    head = _commit_id("HEAD", top)
    names, renames = _changed(sub, extra, top)
    other = _split(git_bytes(["ls-files", "--others", "--exclude-standard", "-z"], top)) if untracked else []
    return {"base": head, "head": head, "mb": head, "names": names, "renames": renames, "untracked": other,
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
    other = _split(git_bytes(["--literal-pathspecs", "ls-files", "--others", "--exclude-standard", "-z", "--"] + specs, top))
    names = sorted(n for n in set(tracked) | set(other) if os.path.lexists(os.path.join(top, n)))
    present = set(names)
    explicit = {sp for sp in specs if not os.path.isdir(os.path.join(top, sp))}  # named files bypass every rule
    return {"base": head, "head": head, "mb": head, "names": names, "untracked": [n for n in other if n in present],
            "diff_cmd": None, "commits": [], "explicit": explicit}


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
        p = subprocess.Popen(_GIT + ["cat-file", "--batch"], cwd=cwd, env=_env(), stdin=fh,
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
    for oid, (f, data) in zip(uniq, cat_batch([o.encode() for o in uniq], top, False, _MAX_FILE)):
        if len(f) >= 3 and f[-2] == b"blob":
            got[oid] = _scan(data) if data is not None else (False, 0, [], int(f[-1]))
    return {n: got[o] for n, o in want.items() if o in got}


def _files(names: List[str], top: str) -> dict:
    """_scan of regular working-tree files, read through read_working; a symlink, special or missing file is
    left out, and one over the cap gets only its size."""
    out = {}
    for n in names:
        status, got = read_working(os.path.join(top, n), _MAX_FILE)
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
    mb, explicit = plan["mb"], plan.get("explicit", ())

    def drop(n, reason, detail):
        excluded.append({"path": n, "reason": reason, "detail": detail})

    for n in names:
        base = n.rsplit("/", 1)[-1]
        if n in explicit:
            todo.append(n)  # only the size cap applies to a file the user named
        elif base in _LOCKFILES:
            drop(n, "lockfile", "basename")
        else:
            todo.append(n)  # a minified name is judged below, with the "the change adds it" keep
    if todo:
        if kind in ("unstaged", "worktree", "path", "repo"):
            head = _files(todo, top)
        else:
            head = _blobs(index_listing(top) if kind == "staged" else tree_listing(plan["head"], top), todo, top)
        # 6xl.88: the base side is what the diff compares against: the index for --unstaged, the merge base else
        base_ls = _blobs(index_listing(top) if kind == "unstaged" else tree_listing(mb, top), todo, top)
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
            if size > _MAX_FILE:  # 6xl.75: a resource limit, so it applies before every heuristic
                drop(n, "too-large", "%d bytes, over %d" % (size, _MAX_FILE))
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
                notes.append("%s: kept; it matches the %s rule but the change adds it" % (_shown(n), hit[2]))
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
        p = subprocess.Popen(_GIT + args, cwd=top, env=_env(), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
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


def _write_diff(path: str, plan: dict, names: List[str], narrow: bool, kind: str, top: str) -> Tuple[list, list]:
    """Strip and escape the change diff file by file into change.diff, so memory holds one file's diff and,
    for a tracked prose file, its whole-file-context diff (6xl.66). Returns (hidden chars, hidden text) with
    change.diff line numbers."""
    def untracked() -> Iterator[str]:
        if kind in ("unstaged", "worktree"):  # untracked files shown as additions; exit status 1 means "differs"
            for u in sorted(set(plan["untracked"]) & set(names)):
                if not u.endswith("/"):
                    # --text: an untracked `* -diff` must not hide text (6xl.65); NUL-byte files are excluded already
                    rc, out, _ = _run(["diff", "--no-index", "--text"] + _DIFF_FLAGS + ["--", "/dev/null", u], top)
                    if rc in (0, 1):
                        yield "".join(_diff_line(ln) + "\n" for ln in out.split(b"\n")[:-1])

    tracked = _sections(_diff_lines(_diff_calls(plan, names, narrow, top), top))
    wide = dict(plan, diff_cmd=[_WIDE if a == "--unified=3" else a for a in plan["diff_cmd"]]) if plan["diff_cmd"] else None
    hidden, stripped, offset = [], [], 0
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w", encoding="utf-8") as fh:
        for is_tracked, sections in ((True, tracked), (False, untracked())):
            for sec in sections:
                full = None
                if is_tracked and wide:  # CTX-04: whole-file context tells strip_hidden_diff where a comment opens
                    paths = {p for _, p, tag, *_ in sanitize._walk(sec) if p and sanitize.is_prose_file(p)}
                    paths |= {o for *_, o in sanitize._walk(sec) if o and sanitize.is_prose_file(o)}
                    prose = [n for n in names if n in paths and n not in plan["untracked"]]
                    full = _diff_text(wide, prose, True, top) if prose else None
                text, st = sanitize.strip_hidden_diff(sec, full)
                text, hc = sanitize.escape_for_code(text)
                stripped += [dict(h, line=h["line"] + offset) for h in st]
                hidden += [dict(h, line=h["line"] + offset) for h in hc]
                offset += text.count("\n")
                fh.write(text)
    return hidden, stripped


def pin(candidate: dict, cwd: str) -> dict:
    top = git(["rev-parse", "--show-toplevel"], cwd).strip()
    kind = candidate["kind"]
    if kind in ("pr", "mr"):
        plan = _plan_forge(candidate, top)
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
    elif kind in ("path", "repo"):
        plan = _plan_files(candidate.get("paths", []), cwd, top)
    else:
        raise Refuse("bad-ref", "unknown target kind")
    names = sorted(set(plan["names"]) | set(plan["untracked"]))
    if not names:
        raise Refuse("no-files" if kind in ("path", "repo") else "empty-diff",
                     "the target has no files" if kind in ("path", "repo") else "the change has no differences")
    names, excluded, fnotes = filter_files(kind, plan, names, top)
    if not names:
        counts = {}
        for e in excluded:
            counts[e["reason"]] = counts.get(e["reason"], 0) + 1
        sizes = [e["detail"].split(" bytes")[0] for e in excluded if e["reason"] == "too-large"]
        raise Refuse("only-excluded", "every changed file is excluded: " + ", ".join(  # 6xl.89: sizes, never names
            "%d %s" % (n, r) + (" (%s bytes, over %d)" % (", ".join(sizes), _MAX_FILE) if r == "too-large" else "")
            for r, n in sorted(counts.items())))
    run = new_run_dir(cwd)
    hidden, stripped = _write_diff(os.path.join(run, "change.diff"), plan, names, bool(excluded), kind, top)
    # 6xl.51: names reach target.json escaped; a name that hides characters is itself a finding
    marks = [(sanitize.escape_for_code(n), n) for n in names + [e["path"] for e in excluded]]
    named = [{"file": esc, "name": True, "codepoints": [c for h in hits for c in h["codepoints"]]}
             for (esc, hits), _ in marks if hits]
    rec = {
        "version": 1, "kind": kind, "repo": top, "run": run, "arg": _shown(candidate.get("arg", "")),
        "number": plan.get("number"), "url": _shown(plan["url"]) if plan.get("url") else plan.get("url"),  # 6xl.85
        "base_sha": plan["base"], "head_sha": plan["head"], "merge_base": plan["mb"],
        "dirty": plan.get("dirty") or bool(git(["status", "--porcelain", "--untracked-files=no", "--ignore-submodules=none"], top).strip()),
        "diff_cmd": plan["diff_cmd"], "commits": plan["commits"],
        "included": [_shown(n) for n in names], "excluded": [dict(e, path=_shown(e["path"])) for e in excluded],
        "untracked": sorted(_shown(n) for n in plan["untracked"]),
        "hidden_chars": [{"file": "change.diff", **h} for h in hidden] + named,
        "hidden_text": [{**h, "path": _shown(h["file"]), "file": "change.diff"} for h in stripped], "notes": plan.get("notes", []) + fnotes,
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
        if "--stdin" in argv:  # WR-08: words as data, one per line, so no shell layer parses them
            argv = [a for a in argv if a != "--stdin"] + [w for w in sys.stdin.read().split("\n") if w]
        args, choice = _parse_cli(argv)
        cands = resolve(args, cwd)
        if choice is None and len(cands) > 1:
            emit({"status": "ambiguous", "candidates": [
                {"index": i, "kind": c["kind"], "reading": _shown(c["reading"])} for i, c in enumerate(cands, 1)]})
            return 2
        if choice is not None and not 1 <= choice <= len(cands):
            raise Refuse("bad-choice", "the choice is not one of the listed readings")
        cand = cands[(choice or 1) - 1]
        cand["arg"] = " ".join(args)
        rec = pin(cand, cwd)
    except Refuse as r:
        emit({"status": "skip" if r.reason in SKIP_REASONS else "refuse", "reason": r.reason, "detail": r.detail})
        return 1
    except (OSError, KeyError, ValueError, TypeError) as e:  # the skill reads one JSON line; never a traceback
        emit({"status": "refuse", "reason": "internal-error", "detail": type(e).__name__})
        return 1
    emit({"status": "ok", "kind": rec["kind"], "base_sha": rec["base_sha"], "head_sha": rec["head_sha"],
          "merge_base": rec["merge_base"], "number": rec["number"], "run": rec["run"],
          "included_count": len(rec["included"]), "excluded_count": len(rec["excluded"]),
          "untracked_count": len(rec["untracked"])})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
