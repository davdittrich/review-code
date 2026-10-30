"""Hook: keep a lane or verifier reply byte-exact for `report.py ingest|verdicts --agent <agentId>` (gh#8).

Runs on SubagentStop (the agent's last message) and on PostToolUse of SubagentHandback (the report an agent hands
back, where the harness runs it in auto mode). The hook input is JSON on stdin. A reply is kept only for this
plugin's lane and verifier agents and only under a strict agent_id. It writes captures_dir()/<agent_id>.<handback|stop>.json
= {agent_type, message}, mode 0600, through a temp file and os.replace, and always exits 0 and prints nothing, so it can
never block the agent. The directory is per user, not per repository (review-code-27u8): the hook input names no run,
and its cwd is the session's, which may be another worktree, another repository or none, so only a place that both
this hook and report.py derive from the same environment links an agent id to its reply; the agent id is unique, so
no repository key is needed. Stdlib only, no git; keep the syntax Python 3.9 safe. A custom agent_prefix plugin setting
is not captured (the hook cannot read it: an unset option fails a hook), so those runs use stdin.
"""
import json
import os
import re
import stat
import sys
import tempfile
import time

ID = re.compile(r"[A-Za-z0-9]{1,64}")
KINDS = ("handback", "stop")  # preference order: the handback is the whole report, the stop text only its closing words
AGENTS = frozenset("review-code:" + n for n in ("finder", "standards", "spec", "sota", "verifier", "spec-verifier"))
RETENTION_DAYS = 14  # run directories (target._prune) and capture files (prune) older than this are removed


def captures_dir():
    """<home>/.local/state/review-code/captures: the one place the hook writes and report.py and target.py read. The home
    comes from the account database, not $HOME, because a SessionStart hook can change the Bash tool's environment
    (CLAUDE_ENV_FILE) but not a hook's; expanduser only where there is no pwd module (Windows). REVIEW_CODE_STATE_HOME,
    when set, replaces <home>/.local/state: it is the only environment input, for tests."""
    state = os.environ.get("REVIEW_CODE_STATE_HOME")
    if not state:
        try:
            import pwd
            home = pwd.getpwuid(os.getuid()).pw_dir
        except (ImportError, KeyError):
            home = os.path.expanduser("~")
        state = os.path.join(home, ".local", "state")
    return os.path.join(state, "review-code", "captures")


def prune(caps=None):
    """Remove capture files not touched for RETENTION_DAYS from `caps` (default captures_dir()): a capture kept after a
    refusal, or one a sandboxed report.py could not delete. Called by the hook after each write, which runs outside the
    Bash sandbox, and by target._prune; links and other entries are left alone and a removal that fails is skipped."""
    caps = caps or captures_dir()
    cutoff = time.time() - RETENTION_DAYS * 86400
    try:
        names = os.listdir(caps)
    except OSError:
        return
    for f in names:
        try:
            st = os.lstat(os.path.join(caps, f))
            if stat.S_ISREG(st.st_mode) and st.st_mtime < cutoff:
                os.remove(os.path.join(caps, f))
        except OSError:
            pass


def path(agent_id, kind, caps=None):
    return os.path.join(caps or captures_dir(), "%s.%s.json" % (agent_id, kind))


def _message(ev):
    """(kind, text) of a hook input, else (None, None)."""
    if ev.get("hook_event_name") == "SubagentStop":
        return "stop", ev.get("last_assistant_message")
    if ev.get("hook_event_name") == "PostToolUse" and ev.get("tool_name") == "SubagentHandback":
        tin = ev.get("tool_input")
        return "handback", tin.get("message") if isinstance(tin, dict) else None
    return None, None


def keep(ev):
    """Store the reply of one hook event for this plugin's agents; a no-op for anything else."""
    kind, text = _message(ev)
    aid, typ = ev.get("agent_id"), ev.get("agent_type")
    if kind is None or not isinstance(text, str) or typ not in AGENTS or not (isinstance(aid, str) and ID.fullmatch(aid)):
        return
    os.makedirs(captures_dir(), 0o700, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=captures_dir(), prefix=".tmp-")  # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:  # ensure_ascii escapes everything, a lone surrogate included
            json.dump({"agent_type": typ, "message": text}, fh)
        os.replace(tmp, path(aid, kind))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    prune()


def main():
    try:
        ev = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        if isinstance(ev, dict):
            keep(ev)
    except Exception:  # a hook must never fail the agent it watches
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
