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


def resolve(argv: List[str], cwd: str) -> List[dict]:
    if not argv:
        raise Refuse("no-target", "name a branch or ref to review against")
    if len(argv) > 1:
        raise Refuse("mixed-target", "give one target")
    arg = argv[0]
    if arg.startswith("-"):
        raise Refuse("option-like-argument", "the target must not start with a dash")
    m = _PR_URL.fullmatch(arg)
    if m:
        n = _num(m.group(3))
        key = (m.group(1) + "/" + m.group(2)).lower()
        return [{"kind": "pr", "number": n, "repo_key": key, "reading": "pull request #%d" % n,
                 "url": "https://%s/%s/pull/%d" % (m.group(1), m.group(2), n)}]
    if arg.startswith("#") and _DIGITS.fullmatch(arg[1:]) or _DIGITS.fullmatch(arg):
        n = _num(arg.lstrip("#"))
        return [{"kind": "pr", "number": n, "url": None, "reading": "pull request #%d" % n}]
    _commit_id(arg, cwd)
    return [{"kind": "branch", "base": arg, "head": "HEAD", "reading": "changes on HEAD since " + arg}]


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


def pin(candidate: dict, cwd: str) -> dict:
    top = git(["rev-parse", "--show-toplevel"], cwd).strip()
    kind = candidate["kind"]
    if kind == "pr":
        plan = _plan_forge(candidate, top)
    else:
        base = _commit_id(candidate["base"], cwd)
        head = _commit_id(candidate["head"], cwd)
        mb = git(["merge-base", base, head], cwd, check=False).strip()
        if not mb:
            raise Refuse("bad-ref", "no common ancestor")
        plan = _plan_revs(base, head, mb, cwd)
    names = sorted(set(plan["names"]) | set(plan["untracked"]))
    if not names:
        raise Refuse("empty-diff", "the change has no differences")
    run = new_run_dir(cwd)
    text, hidden = sanitize.escape_for_code(git(plan["diff_cmd"][1:], top))
    with open(os.path.join(run, "change.diff"), "w", encoding="utf-8") as fh:
        fh.write(text)
    rec = {
        "version": 1, "kind": kind, "repo": top, "run": run, "arg": candidate.get("arg", ""),
        "number": plan.get("number"), "url": plan.get("url"),
        "base_sha": plan["base"], "head_sha": plan["head"], "merge_base": plan["mb"],
        "dirty": bool(git(["status", "--porcelain", "--untracked-files=no"], top).strip()),
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
