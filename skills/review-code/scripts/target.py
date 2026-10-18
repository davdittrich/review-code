"""Pin the review target to full SHAs and create the run directory.

Stdlib only, Python 3.9 grammar. Prints one JSON object with fixed keys; repository-sourced text
(file names, notes, messages) goes to run-dir files only, never to stdout.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import json
import os
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import List, Optional

import sanitize

STDOUT_KEYS = {"status", "reason", "detail", "kind", "base_sha", "head_sha", "merge_base", "number", "run",
               "included_count", "excluded_count", "untracked_count", "candidates"}
# No hooks, no fsmonitor program; check-out family subcommands are never run.
_GIT = ["git", "-c", "core.hooksPath=/dev/null", "-c", "core.fsmonitor=false"]
_DIFF_FLAGS = ["--unified=3", "--no-ext-diff", "--no-textconv", "--no-color"]
SKIP_REASONS = {"empty-diff", "no-files", "closed", "merged", "draft", "locked"}
_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")  # SHA-1 or SHA-256, lowercase only
_PR_URL = re.compile(r"https?://([^/\s]+)/(.+?)/pull/([0-9]+)(?:[/?#].*)?", re.S)
_DIGITS = re.compile(r"[0-9]+")
_REMOTE_URL = re.compile(r"(?:[a-z][a-z0-9+.-]*://)?(?:[^@/]+@)?([^/:]+)(?::[0-9]+)?[:/](.+?)/?", re.I | re.S)
_PR_FIELDS = "state,isDraft,baseRefOid,headRefOid,baseRefName,isCrossRepository,url,number"


class Refuse(Exception):
    def __init__(self, reason: str, detail: str = ""):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


def _env() -> dict:
    e = {k: v for k, v in os.environ.items() if k != "GIT_EXTERNAL_DIFF"}
    e.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return e


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


def _cli_json(tool: str, args: List[str], cwd: str):
    try:
        p = subprocess.run([tool] + list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise Refuse(tool + "-missing", tool + " is not installed")
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


def new_run_dir(cwd: str) -> str:
    """Mode 0700 directory under the git dir: a read-only lane cannot read $TMPDIR (probe A3)."""
    base = git(["rev-parse", "--git-path", "review-code"], cwd).strip()
    base = os.path.join(cwd, base)
    os.makedirs(base, mode=0o700, exist_ok=True)
    return tempfile.mkdtemp(prefix="review-code.", dir=base)


def _split(text: str) -> List[str]:
    return [n for n in text.split("\0") if n]


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
    if git(["status", "--porcelain"], cwd).strip():
        return [{"kind": "worktree", "reading": "uncommitted changes against HEAD, untracked files included"}]
    default = default_branch(cwd)
    rc, out, _ = _run(["symbolic-ref", "--quiet", "--short", "HEAD"], cwd)
    if default and rc == 0 and out.decode("utf-8", "replace").strip() != default[1]:
        return [{"kind": "branch", "base": default[0], "head": "HEAD",
                 "reading": "changes on HEAD since " + default[0]}]
    raise Refuse("no-target", "name a target to review")


def resolve(argv: List[str], cwd: str) -> List[dict]:
    if not argv:
        return _no_argument(cwd)
    if argv[0] in ("--staged", "--unstaged", "--repo"):
        if len(argv) > 1:
            raise Refuse("mixed-target", "give one target")
        kind = argv[0][2:]
        return [{"kind": kind, "reading": {"staged": "staged changes", "unstaged": "unstaged changes",
                                           "repo": "the whole repository"}[kind]}]
    for arg in argv:
        if arg.startswith("-"):
            raise Refuse("option-like-argument", "the target must not start with a dash")
    if len(argv) > 1:
        return [_path_candidate(argv, cwd)]
    arg = argv[0]
    m = _PR_URL.fullmatch(arg)
    if m:
        n = _num(m.group(3))
        key = (m.group(1) + "/" + m.group(2)).lower()
        return [{"kind": "pr", "number": n, "repo_key": key, "reading": "pull request #%d" % n,
                 "url": "https://%s/%s/pull/%d" % (m.group(1), m.group(2), n)}]
    if arg.startswith("#") and _DIGITS.fullmatch(arg[1:]) or _DIGITS.fullmatch(arg):
        n = _num(arg.lstrip("#"))
        return [{"kind": "pr", "number": n, "url": None, "reading": "pull request #%d" % n}]
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
    if arg == ".":
        return [{"kind": "repo", "reading": "the whole repository"}]
    try:
        _commit_id(arg, cwd)
        return [{"kind": "branch", "base": arg, "head": "HEAD", "reading": "changes on HEAD since " + arg}]
    except Refuse:
        if os.path.lexists(os.path.join(cwd, arg)):
            return [_path_candidate(argv, cwd)]
        raise


def _path_candidate(paths: List[str], cwd: str) -> dict:
    if not all(os.path.lexists(os.path.join(cwd, p)) for p in paths):
        raise Refuse("mixed-target", "give one target, or only paths")
    return {"kind": "path", "paths": list(paths), "reading": "files under " + ", ".join(paths)}


def _branch_ok(name: object, cwd: str) -> bool:
    return (isinstance(name, str) and bool(name) and not name.startswith("-") and "@{" not in name
            and _run(["check-ref-format", "--branch", name], cwd)[0] == 0)


def _oid_ok(value: object) -> bool:
    return isinstance(value, str) and _OID.fullmatch(value) is not None


def _view_pr(cand: dict, cwd: str) -> dict:
    j = gh_json(["pr", "view", cand["url"] or str(cand["number"]), "--json", _PR_FIELDS], cwd)
    if not isinstance(j, dict):
        raise Refuse("gh-failed", "gh printed an unexpected document")
    state = j.get("state")
    if state not in ("OPEN", "CLOSED", "MERGED"):
        raise Refuse("gh-failed", "gh reported an unknown pull request state")
    if state != "OPEN":
        raise Refuse(state.lower(), "the pull request is " + state.lower())
    if j.get("isDraft"):
        raise Refuse("draft", "the author must mark it ready for review")
    m = _PR_URL.fullmatch(j.get("url") if isinstance(j.get("url"), str) else "")
    if (not m or j.get("number") != cand["number"] or not _oid_ok(j.get("baseRefOid"))
            or not _oid_ok(j.get("headRefOid")) or not _branch_ok(j.get("baseRefName"), cwd)):
        raise Refuse("gh-failed", "gh reported invalid pull request fields")
    return {"base": j["baseRefOid"], "head": j["headRefOid"], "branch": j["baseRefName"], "url": j["url"],
            "key": (m.group(1) + "/" + m.group(2)).lower(), "ref": "refs/pull/%d/head" % cand["number"],
            "notes": []}


def _fetch(remote: str, refspecs: List[str], cwd: str) -> None:
    """Objects only: no destination, no FETCH_HEAD, no opportunistic ref update (empty --refmap=)."""
    rc, _, _ = _run(["-c", "transfer.fsckObjects=true", "-c", "gc.auto=0", "-c", "maintenance.auto=false",
                     "fetch", "--no-tags", "--no-write-fetch-head", "--no-recurse-submodules", "--refmap=",
                     "--", remote] + refspecs, cwd)
    if rc != 0:
        raise Refuse("fetch-failed", "git could not fetch the pull request objects")


def _have(oid: str, cwd: str) -> bool:
    return _run(["cat-file", "-e", oid + "^{commit}"], cwd)[0] == 0


def _plan_revs(base: str, head: str, mb: str, cwd: str) -> dict:
    names = _split(git(["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv", mb, head, "--"], cwd))
    return {"base": base, "head": head, "mb": mb, "names": names, "untracked": [],
            "diff_cmd": ["git", "diff"] + _DIFF_FLAGS + [mb, head, "--"],
            "commits": git(["rev-list", "--reverse", mb + ".." + head], cwd).split()}


def _plan_forge(cand: dict, cwd: str) -> dict:
    if cand.get("repo_key") and not any(k == cand["repo_key"] for _, k in _remotes(cwd)):
        raise Refuse("cross-repo", "the pull request belongs to a repository with no configured remote")
    info = _view_pr(cand, cwd)
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


_EMPTY_NAMES = ("--name-only", "-z", "--no-ext-diff", "--no-textconv")


def _head_plan(top: str, extra: List[str], untracked: bool, diff_extra: List[str]) -> dict:
    """staged, unstaged and worktree: base = merge_base = head = HEAD, the change is the tree state."""
    head = _commit_id("HEAD", top)
    names = _split(git(["diff"] + list(_EMPTY_NAMES) + extra + ["--"], top))
    other = _split(git(["ls-files", "--others", "--exclude-standard", "-z"], top)) if untracked else []
    return {"base": head, "head": head, "mb": head, "names": names, "untracked": other, "dirty": True,
            "diff_cmd": ["git", "diff"] + _DIFF_FLAGS + diff_extra + ["--"], "commits": []}


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
    tracked = _split(git(["--literal-pathspecs", "ls-files", "-z", "--"] + specs, top))
    other = _split(git(["--literal-pathspecs", "ls-files", "--others", "--exclude-standard", "-z", "--"] + specs, top))
    names = sorted(n for n in set(tracked) | set(other) if os.path.lexists(os.path.join(top, n)))
    present = set(names)
    return {"base": head, "head": head, "mb": head, "names": names, "untracked": [n for n in other if n in present],
            "diff_cmd": None, "commits": []}


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


def pin(candidate: dict, cwd: str) -> dict:
    top = git(["rev-parse", "--show-toplevel"], cwd).strip()
    kind = candidate["kind"]
    if kind == "pr":
        plan = _plan_forge(candidate, top)
    elif kind == "commit":
        plan = _plan_commit(candidate["rev"], top)
    elif kind in ("branch", "range"):
        base = _commit_id(candidate["base"], cwd)
        head = _commit_id(candidate["head"], cwd)
        mb = git(["merge-base", base, head], cwd, check=False).strip()
        if not mb:
            raise Refuse("bad-ref", "no common ancestor")
        plan = _plan_revs(base, head, mb, cwd)
    elif kind == "staged":
        head = _commit_id("HEAD", top)
        plan = _head_plan(top, ["--cached", head], False, ["--cached", head])
    elif kind == "unstaged":
        plan = _head_plan(top, [], True, [])
    elif kind == "worktree":
        head = _commit_id("HEAD", top)
        plan = _head_plan(top, [head], True, [head])
    elif kind in ("path", "repo"):
        plan = _plan_files(candidate.get("paths", []), cwd, top)
    else:
        raise Refuse("bad-ref", "unknown target kind")
    names = sorted(set(plan["names"]) | set(plan["untracked"]))
    if not names:
        raise Refuse("no-files" if kind in ("path", "repo") else "empty-diff",
                     "the target has no files" if kind in ("path", "repo") else "the change has no differences")
    run = new_run_dir(cwd)
    text = git(plan["diff_cmd"][1:], top) if plan["diff_cmd"] else ""
    if kind in ("unstaged", "worktree"):  # untracked files shown as additions; exit status 1 means "differs"
        for u in sorted(plan["untracked"]):
            if not u.endswith("/"):
                rc, out, _ = _run(["diff", "--no-index", "--no-ext-diff", "--no-textconv", "--no-color", "--",
                                   "/dev/null", u], top)
                if rc in (0, 1):
                    text += out.decode("utf-8", "replace")
    text, hidden = sanitize.escape_for_code(text)
    with open(os.path.join(run, "change.diff"), "w", encoding="utf-8") as fh:
        fh.write(text)
    rec = {
        "version": 1, "kind": kind, "repo": top, "run": run, "arg": candidate.get("arg", ""),
        "number": plan.get("number"), "url": plan.get("url"),
        "base_sha": plan["base"], "head_sha": plan["head"], "merge_base": plan["mb"],
        "dirty": plan.get("dirty") or bool(git(["status", "--porcelain", "--untracked-files=no"], top).strip()),
        "diff_cmd": plan["diff_cmd"], "commits": plan["commits"],
        "included": names, "excluded": [], "untracked": sorted(plan["untracked"]),
        "hidden_chars": [{"file": "change.diff", **h} for h in hidden], "notes": plan.get("notes", []),
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(run, "target.json"), "w", encoding="utf-8") as fh:
        json.dump(rec, fh, sort_keys=True, indent=2)
    return rec


def emit(obj: dict) -> None:
    print(json.dumps({k: v for k, v in obj.items() if k in STDOUT_KEYS}, sort_keys=True))


def main(argv: List[str], cwd: Optional[str] = None) -> int:
    cwd = cwd or os.getcwd()
    try:
        try:
            git(["rev-parse", "--show-toplevel"], cwd)
        except Refuse:
            raise Refuse("not-a-git-repo", "run inside a git repository")
        cand = resolve(argv, cwd)[0]
        cand["arg"] = " ".join(argv)
        rec = pin(cand, cwd)
    except Refuse as r:
        emit({"status": "skip" if r.reason in SKIP_REASONS else "refuse", "reason": r.reason, "detail": r.detail})
        return 1
    emit({"status": "ok", "kind": rec["kind"], "base_sha": rec["base_sha"], "head_sha": rec["head_sha"],
          "merge_base": rec["merge_base"], "number": rec["number"], "run": rec["run"],
          "included_count": len(rec["included"]), "excluded_count": len(rec["excluded"]),
          "untracked_count": len(rec["untracked"])})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
