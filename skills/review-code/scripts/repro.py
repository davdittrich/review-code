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
import threading
from typing import List, Optional

import ledger
import report
import target

ID_RE = report.ID_RE
TIMEOUT = 120  # seconds; read at call time. SKILL.md step 9 gives the Bash call 300000 ms: the rest is setup and cleanup
ENV_KEEP = ("PATH", "LANG", "LC_ALL", "TERM")
SENTINEL = "REPRO-"
USAGE = "usage: repro.py run --run DIR --id ID --test RELPATH [--base] -- CMD [ARG ...]"


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


def _execute(cmd: List[str], cwd: str, env: dict, fid: Optional[str], timeout: Optional[float] = None) -> dict:
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
    finally:
        target._NO_FILTERS.clear()  # the command may have defined a filter: the next git call reads the config afresh
    text = out.decode("utf-8", "replace")
    hit = p.returncode == 0 if fid is None else p.returncode != 0 and SENTINEL + fid in text  # fid None: base mode, exit 0 is the claim
    outcome = "reproduced" if hit else "not-reproduced"
    return {"outcome": outcome, "exit": p.returncode, "tail": text[-2000:]}


def run(run_dir: str, fid: str, test: str, cmd: List[str], source: bytes, base: bool = False) -> dict:
    """base: the finding claims `test` passes on the merge base; run the head's copy of it there (exit 0 = reproduced)."""
    if not re.fullmatch(ID_RE, fid):
        raise target.Refuse("bad-id")
    if not _test_path_ok(test):
        raise target.Refuse("bad-test-path")
    if base and not any(test in a for a in cmd):
        raise target.Refuse("cmd-misses-test")
    ledger.trust_gate(run_dir, cmd)  # first effectful call: external or unset trust ends here
    try:
        with open(os.path.join(run_dir, "target.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        repo, kind, head, mb = rec["repo"], rec["kind"], rec["head_sha"], rec.get("merge_base")
    except (OSError, ValueError, KeyError, TypeError, RecursionError):
        raise target.Refuse("no-target")
    if base and not mb:
        raise target.Refuse("no-merge-base")
    wt, home, tmp = (os.path.join(run_dir, p + fid) for p in ("wt-", "repro-home-", "repro-tmp-"))
    wth = os.path.join(run_dir, "wt-head-" + fid)  # base mode only: where the head's copy of the test is read
    try:
        target.git_bytes(["worktree", "add", "--detach", wth if base else wt, head], repo)
        if kind in target.INPLACE:  # HEAD plus the tracked diff; untracked files are not carried
            args = ["diff", "--binary", "--no-ext-diff", "--no-textconv", "--no-color"]
            diff = target.git_bytes(args + (["--cached"] if kind == "staged" else []) + ["HEAD"], repo)
            if diff:
                target.git_bytes(["apply", "--binary"], wth if base else wt, stdin=diff)
        if base:
            hp = os.path.realpath(os.path.join(wth, test))
            if not hp.startswith(os.path.realpath(wth) + os.sep):
                raise target.Refuse("bad-test-path")
            if not os.path.isfile(hp):
                raise target.Refuse("no-test-at-head")
            with open(hp, "rb") as fh:
                source = fh.read()
            target.git_bytes(["worktree", "add", "--detach", wt, mb], repo)
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
        result = _execute(cmd, wt, env, None if base else fid)
    except (target.Refuse, OSError) as e:
        if isinstance(e, target.Refuse) and e.reason in ("bad-test-path", "no-test-at-head"):
            raise
        result = {"outcome": "error", "exit": None}  # a git or OS failure after the gate
    finally:
        for w in (wt, wth):
            target.git_bytes(["worktree", "remove", "--force", w], repo, check=False)
            shutil.rmtree(w, ignore_errors=True)
        target.git_bytes(["worktree", "prune"], repo, check=False)
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(tmp, ignore_errors=True)
    out_dir = os.path.join(run_dir, "repro")
    os.makedirs(out_dir, exist_ok=True)
    os.chmod(out_dir, 0o700)
    record = dict({k: result[k] for k in ("outcome", "exit")}, mode="base" if base else "run")  # the tail is target output: it is not kept
    target.write_private(os.path.join(out_dir, fid + ".json"), json.dumps(record, sort_keys=True))
    return dict(result, status="ok", id=fid)


def _term(signum, frame):
    for name in ("SIGTERM", "SIGHUP"):  # one-shot: a second signal must not abort the cleanup the first one starts
        if getattr(signal, name, None):
            signal.signal(getattr(signal, name), signal.SIG_IGN)
    raise SystemExit(143)  # unwinds through run()'s finally and _execute's group kill; the default action skips both


def main(argv: List[str], stdin: Optional[object] = None) -> int:
    names = [n for n in ("SIGTERM", "SIGHUP") if getattr(signal, n, None)]  # Windows has no SIGHUP
    old = {}
    try:
        if argv[:1] != ["run"] or "--" not in argv:
            raise target.Refuse("usage", USAGE)
        i = argv.index("--")
        flag = argv[1:i].count("--base")
        opts = [a for a in argv[1:i] if a != "--base"]
        if flag > 1 or len(opts) != 6 or opts[0::2] != ["--run", "--id", "--test"] or i == len(argv) - 1:
            raise target.Refuse("usage", USAGE)
        if threading.current_thread() is threading.main_thread():  # signal.signal raises elsewhere
            for n in names:
                old[n] = signal.signal(getattr(signal, n), _term)
        out = run(opts[1], opts[3], opts[5], argv[i + 1:], b"" if flag else (stdin or sys.stdin.buffer).read(), bool(flag))
        rc = 0
    except target.Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason}, 1
    except (OSError, KeyError, ValueError, TypeError) as e:
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    finally:
        for n, h in old.items():
            signal.signal(getattr(signal, n), h)
    print(json.dumps({k: v for k, v in out.items() if k in ("status", "reason", "detail", "id", "outcome", "exit")},
                     sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
