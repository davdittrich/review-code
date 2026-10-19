"""Run one user-confirmed failing test for one finding in a throwaway worktree and record the outcome.

Stdlib only, Python 3.9 grammar. The only place in the plugin where target code executes, and only
after ledger.trust_gate passes. Prints one JSON line; never a traceback. No sandbox: the environment
allowlist keeps secrets out, nothing more.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import json
import os
import re
import shutil
import signal
import subprocess
from typing import List, Optional

import ledger
import target

ID_RE = r"F-[0-9a-f]{10}"
TIMEOUT = 120  # seconds; read at call time
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM")
SENTINEL = "REPRO-"
USAGE = "usage: repro.py run --run DIR --id ID --test RELPATH -- CMD [ARG ...]"


def _test_path_ok(rel: str) -> bool:
    return bool(rel) and "\0" not in rel and not os.path.isabs(rel) and ".." not in rel.split("/")


def _execute(cmd: List[str], cwd: str, env: dict, fid: str) -> dict:
    """Run cmd in its own session; on timeout kill the whole group, then collect what is left."""
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        return {"outcome": "error", "exit": None}
    try:
        out, _ = p.communicate(timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            p.kill()
        try:
            p.communicate(timeout=5)
        except subprocess.TimeoutExpired:  # shortcut: a grandchild that left the session keeps the pipe; abandon it
            pass
        return {"outcome": "timeout", "exit": None}
    text = out.decode("utf-8", "replace")
    if p.returncode != 0 and SENTINEL + fid in text:
        return {"outcome": "reproduced", "exit": p.returncode}
    return {"outcome": "not-reproduced", "exit": p.returncode}


def run(run_dir: str, fid: str, test: str, cmd: List[str], source: bytes) -> dict:
    if not re.fullmatch(ID_RE, fid):
        raise target.Refuse("bad-id")
    if not _test_path_ok(test):
        raise target.Refuse("bad-test-path")
    ledger.trust_gate(run_dir, cmd)  # first effectful call: external or unset trust ends here
    try:
        with open(os.path.join(run_dir, "target.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        repo, kind, head = rec["repo"], rec["kind"], rec["head_sha"]
    except (OSError, ValueError, KeyError, TypeError):
        raise target.Refuse("no-target")
    wt, home, tmp = (os.path.join(run_dir, p + fid) for p in ("wt-", "repro-home-", "repro-tmp-"))
    try:
        target.git_bytes(["worktree", "add", "--detach", wt, head], repo)
        wt_real = os.path.realpath(wt)
        path = os.path.realpath(os.path.join(wt, test))
        if not path.startswith(wt_real + os.sep):
            raise target.Refuse("bad-test-path")
        if kind in ledger.INPLACE:  # HEAD plus the tracked diff; untracked files are not carried
            args = ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color"]
            diff = target.git_bytes(args + (["--cached"] if kind == "staged" else []) + ["HEAD"], repo)
            if diff:
                target.git_bytes(["apply", "--binary"], wt, stdin=diff)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(source)
        os.mkdir(home, 0o700)
        os.mkdir(tmp, 0o700)
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env.update(HOME=home, TMPDIR=tmp)
        result = _execute(cmd, wt, env, fid)
    except (target.Refuse, OSError) as e:
        if isinstance(e, target.Refuse) and e.reason == "bad-test-path":
            raise
        result = {"outcome": "error", "exit": None}  # a git or OS failure after the gate
    finally:
        target.git_bytes(["worktree", "remove", "--force", wt], repo, check=False)
        shutil.rmtree(wt, ignore_errors=True)
        target.git_bytes(["worktree", "prune"], repo, check=False)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    out_dir = os.path.join(run_dir, "repro")
    os.makedirs(out_dir, exist_ok=True)
    os.chmod(out_dir, 0o700)
    target.write_private(os.path.join(out_dir, fid + ".json"), json.dumps(result, sort_keys=True))
    return dict(result, status="ok", id=fid)


def main(argv: List[str], stdin: Optional[object] = None) -> int:
    try:
        if argv[:1] != ["run"] or "--" not in argv:
            raise target.Refuse("usage", USAGE)
        i = argv.index("--")
        opts = argv[1:i]
        if len(opts) != 6 or opts[0::2] != ["--run", "--id", "--test"] or i == len(argv) - 1:
            raise target.Refuse("usage", USAGE)
        source = (stdin or sys.stdin.buffer).read()
        out = run(opts[1], opts[3], opts[5], argv[i + 1:], source)
        rc = 0
    except target.Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason}, 1
    except (OSError, KeyError, ValueError, TypeError) as e:
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    print(json.dumps({k: v for k, v in out.items() if k in ("status", "reason", "detail", "id", "outcome", "exit")},
                     sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
