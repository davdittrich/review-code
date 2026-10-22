"""Run one user-confirmed failing test for one finding in a throwaway worktree and record the outcome.

Stdlib only, Python 3.9 grammar. Runs target code (fix.py is the other place), and only after
ledger.trust_gate passes. Prints one JSON line; never a traceback. No sandbox: the environment
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
import report
import target

ID_RE = report.ID_RE
TIMEOUT = 120  # seconds; read at call time
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM")
SENTINEL = "REPRO-"
USAGE = ("usage: repro.py run --run DIR --id ID --test RELPATH -- CMD [ARG ...]"
         " | repro.py base --run DIR --test RELPATH -- CMD [ARG ...]")


def _test_path_ok(rel: str) -> bool:
    return bool(rel) and "\0" not in rel and not os.path.isabs(rel) and ".." not in rel.split("/")


def _kill(p) -> None:
    """SIGKILL the child's whole group, then reap the child so the group is really gone."""
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except OSError:
        p.kill()
    try:
        p.wait(timeout=5)
    except subprocess.TimeoutExpired:  # shortcut: a grandchild that left the session keeps the pipe; abandon it
        pass
    p.stdout.close()


def _execute(cmd: List[str], cwd: str, env: dict, fid: str, timeout: Optional[float] = None) -> dict:
    """Run cmd in its own session; on timeout or any BaseException kill the whole group. tail: last 2000 characters."""
    try:
        p = subprocess.Popen(cmd, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, start_new_session=True)
    except OSError:
        return {"outcome": "error", "exit": None, "tail": ""}
    try:
        out, _ = p.communicate(timeout=TIMEOUT if timeout is None else timeout)
    except subprocess.TimeoutExpired:
        _kill(p)
        return {"outcome": "timeout", "exit": None, "tail": ""}
    except BaseException:
        _kill(p)
        raise
    text = out.decode("utf-8", "replace")
    outcome = "reproduced" if p.returncode != 0 and SENTINEL + fid in text else "not-reproduced"
    return {"outcome": outcome, "exit": p.returncode, "tail": text[-2000:]}


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
        if kind in ledger.INPLACE:  # HEAD plus the tracked diff; untracked files are not carried
            args = ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color"]
            diff = target.git_bytes(args + (["--cached"] if kind == "staged" else []) + ["HEAD"], repo)
            if diff:
                target.git_bytes(["apply", "--binary"], wt, stdin=diff)
        # after the apply: a symlink the diff creates (directory or last part) is resolved too
        wt_real = os.path.realpath(wt)
        path = os.path.realpath(os.path.join(wt, test))
        if not path.startswith(wt_real + os.sep):
            raise target.Refuse("bad-test-path")
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
    record = {k: result[k] for k in ("outcome", "exit")}  # the tail is target output: it is not kept
    target.write_private(os.path.join(out_dir, fid + ".json"), json.dumps(record, sort_keys=True))
    return dict(result, status="ok", id=fid)


def base(run_dir: str, test: str, cmd: List[str]) -> dict:
    """Base check: the change's own test must pass at head and fail at the merge base (the fix reverted)."""
    if not _test_path_ok(test):
        raise target.Refuse("bad-test-path")
    ledger.trust_gate(run_dir, cmd)
    try:
        with open(os.path.join(run_dir, "target.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        repo, kind, head, mb = rec["repo"], rec["kind"], rec["head_sha"], rec["merge_base"]
    except (OSError, ValueError, KeyError, TypeError):
        raise target.Refuse("no-target")
    wts = [os.path.join(run_dir, "wt-base-" + n) for n in ("head", "base")]
    home, tmp = (os.path.join(run_dir, p + "base") for p in ("repro-home-", "repro-tmp-"))
    outcome, codes = "error", {}
    try:
        for wt, rev in zip(wts, (head, mb)):
            target.git_bytes(["worktree", "add", "--detach", wt, rev], repo)
        if kind in ledger.INPLACE:  # head = HEAD plus the tracked diff, as in run; the base stays HEAD
            args = ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color"]
            diff = target.git_bytes(args + (["--cached"] if kind == "staged" else []) + ["HEAD"], repo)
            if diff:
                target.git_bytes(["apply", "--binary"], wts[0], stdin=diff)
        src, dst = (os.path.realpath(os.path.join(w, test)) for w in wts)
        if not (src.startswith(os.path.realpath(wts[0]) + os.sep) and dst.startswith(os.path.realpath(wts[1]) + os.sep)):
            raise target.Refuse("bad-test-path")
        if not os.path.isfile(src):
            raise target.Refuse("no-test-file")  # untracked files are not carried
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copyfile(src, dst)
        os.mkdir(home, 0o700)
        os.mkdir(tmp, 0o700)
        env = {k: os.environ[k] for k in ENV_KEEP if k in os.environ}
        env.update(HOME=home, TMPDIR=tmp)
        for name, wt in zip(("head", "base"), wts):
            res = _execute(cmd, wt, env, "")
            codes[name] = res["exit"]
            if res["outcome"] in ("timeout", "error"):
                outcome = res["outcome"]
                break
            if (res["exit"] == 0) != (name == "head"):  # head must pass; base must fail
                outcome = "fails-on-head" if name == "head" else "passes-on-base"
                break
        else:
            outcome = "verified"
    except (target.Refuse, OSError) as e:
        if isinstance(e, target.Refuse) and e.reason in ("bad-test-path", "no-test-file"):
            raise
    finally:
        for wt in wts:
            target.git_bytes(["worktree", "remove", "--force", wt], repo, check=False)
            shutil.rmtree(wt, ignore_errors=True)
        target.git_bytes(["worktree", "prune"], repo, check=False)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    out_dir = os.path.join(run_dir, "repro")
    os.makedirs(out_dir, exist_ok=True)
    os.chmod(out_dir, 0o700)
    record = {"outcome": outcome, "head_exit": codes.get("head"), "base_exit": codes.get("base"), "test": test}
    # a name no finding id can have, so report.py _repro never reads it
    target.write_private(os.path.join(out_dir, "base-" + re.sub(r"\W", "_", test) + ".json"),
                         json.dumps(record, sort_keys=True))
    return dict(record, status="ok")


def main(argv: List[str], stdin: Optional[object] = None) -> int:
    try:
        if argv[:1] not in (["run"], ["base"]) or "--" not in argv:
            raise target.Refuse("usage", USAGE)
        i = argv.index("--")
        opts = argv[1:i]
        keys = ["--run", "--id", "--test"] if argv[0] == "run" else ["--run", "--test"]
        if len(opts) != 2 * len(keys) or opts[0::2] != keys or i == len(argv) - 1:
            raise target.Refuse("usage", USAGE)
        if argv[0] == "base":
            out = base(opts[1], opts[3], argv[i + 1:])
        else:
            out = run(opts[1], opts[3], opts[5], argv[i + 1:], (stdin or sys.stdin.buffer).read())
        rc = 0
    except target.Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason}, 1
    except (OSError, KeyError, ValueError, TypeError) as e:
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    print(json.dumps({k: v for k, v in out.items() if k in ("status", "reason", "detail", "id", "outcome", "exit", "head_exit", "base_exit")},
                     sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
