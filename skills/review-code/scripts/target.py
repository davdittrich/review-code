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


class Refuse(Exception):
    def __init__(self, reason: str, detail: str = ""):
        Exception.__init__(self, reason)
        self.reason = reason
        self.detail = detail


def _env() -> dict:
    e = {k: v for k, v in os.environ.items() if k != "GIT_EXTERNAL_DIFF"}
    e.update(GIT_TERMINAL_PROMPT="0", GIT_OPTIONAL_LOCKS="0")
    return e


def git_bytes(args: List[str], cwd: str, stdin: Optional[bytes] = None, check: bool = True) -> bytes:
    p = subprocess.run(_GIT + list(args), cwd=cwd, env=_env(), input=stdin,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if check and p.returncode != 0:
        raise Refuse("bad-ref", "a git command failed")
    return p.stdout if p.returncode == 0 else b""


def git(args: List[str], cwd: str, stdin: Optional[bytes] = None, check: bool = True) -> str:
    return git_bytes(args, cwd, stdin, check).decode("utf-8", "replace")


def gh_json(args: List[str], cwd: str):
    try:
        p = subprocess.run(["gh"] + list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise Refuse("gh-missing", "gh is not installed")
    if p.returncode != 0:
        raise Refuse("gh-failed", "gh exited with an error")
    try:
        return json.loads(p.stdout.decode("utf-8", "replace"))
    except ValueError:
        raise Refuse("gh-failed", "gh printed invalid JSON")


def new_run_dir(cwd: str) -> str:
    """Mode 0700 directory under the git dir: a read-only lane cannot read $TMPDIR (probe A3)."""
    base = git(["rev-parse", "--git-path", "review-code"], cwd).strip()
    base = os.path.join(cwd, base)
    os.makedirs(base, mode=0o700, exist_ok=True)
    return tempfile.mkdtemp(prefix="review-code.", dir=base)


def resolve(argv: List[str], cwd: str) -> List[dict]:
    if not argv:
        raise Refuse("no-target", "name a branch or ref to review against")
    if len(argv) > 1:
        raise Refuse("mixed-target", "give one target")
    arg = argv[0]
    if arg.startswith("-"):
        raise Refuse("option-like-argument", "the target must not start with a dash")
    if git(["rev-parse", "--verify", "--quiet", arg + "^{commit}"], cwd, check=False).strip():
        return [{"kind": "branch", "base": arg, "head": "HEAD", "reading": "changes on HEAD since " + arg}]
    raise Refuse("bad-ref", "the target is not a commit in this repository")


def pin(candidate: dict, cwd: str) -> dict:
    base = git(["rev-parse", "--verify", candidate["base"] + "^{commit}"], cwd).strip()
    head = git(["rev-parse", "--verify", candidate["head"] + "^{commit}"], cwd).strip()
    mb = git(["merge-base", base, head], cwd, check=False).strip()
    if not mb:
        raise Refuse("bad-ref", "no common ancestor")
    names = [n for n in git(["diff", "--name-only", "-z", "--no-ext-diff", "--no-textconv", mb, head, "--"],
                            cwd).split("\0") if n]
    if not names:
        raise Refuse("empty-diff", "the change has no differences")
    diff_cmd = ["git", "diff"] + _DIFF_FLAGS + [mb, head, "--"]
    run = new_run_dir(cwd)
    text, hidden = sanitize.escape_for_code(git(diff_cmd[1:], cwd))
    with open(os.path.join(run, "change.diff"), "w", encoding="utf-8") as fh:
        fh.write(text)
    rec = {
        "version": 1, "kind": candidate["kind"], "repo": git(["rev-parse", "--show-toplevel"], cwd).strip(),
        "run": run, "arg": candidate["base"], "number": None, "url": None,
        "base_sha": base, "head_sha": head, "merge_base": mb,
        "dirty": bool(git(["status", "--porcelain", "--untracked-files=no"], cwd).strip()),
        "diff_cmd": diff_cmd,
        "commits": git(["rev-list", "--reverse", mb + ".." + head], cwd).split(),
        "included": sorted(names), "excluded": [], "untracked": [],
        "hidden_chars": [{"file": "change.diff", **h} for h in hidden], "notes": [],
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
        rec = pin(resolve(argv, cwd)[0], cwd)
    except Refuse as r:
        status = "skip" if r.reason == "empty-diff" else "refuse"
        emit({"status": status, "reason": r.reason, "detail": r.detail})
        return 1
    emit({"status": "ok", "kind": rec["kind"], "base_sha": rec["base_sha"], "head_sha": rec["head_sha"],
          "merge_base": rec["merge_base"], "run": rec["run"], "included_count": len(rec["included"]),
          "excluded_count": len(rec["excluded"]), "untracked_count": len(rec["untracked"])})
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
