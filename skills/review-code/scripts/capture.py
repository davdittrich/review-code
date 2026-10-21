"""Hook: keep a lane or verifier reply byte-exact for `report.py ingest|verdicts --agent <agentId>` (gh#8).

Runs on SubagentStop (the agent's last message) and on PostToolUse of SubagentHandback (the report an agent hands
back, where the harness runs it in auto mode). The hook input is JSON on stdin. A reply is kept only for this
plugin's lane and verifier agents, only under a strict agent_id, and only when the repository's review-code state
directory (`git rev-parse --git-path review-code`, as target.py names it) already exists: this script never makes it.
It stays standalone and does not import target: target.write_atomic touches the run directory and target's run-dir
creation makes directories, but the hook must never create the state directory, and must stay import-light and fail-silent.
It writes <state>/captures/<agent_id>.<handback|stop>.json = {agent_type, message}, mode 0600, through a temp file and
os.replace, and always exits 0 and prints nothing, so it can never block the agent. Stdlib only; keep the syntax
Python 3.9 safe. A custom agent_prefix plugin setting is not captured (the hook cannot read it: an unset option
fails a hook), so those runs use stdin.
"""
import json
import os
import re
import subprocess
import sys
import tempfile

ID = re.compile(r"[A-Za-z0-9]{1,64}")
KINDS = ("handback", "stop")  # preference order: the handback is the whole report, the stop text only its closing words
AGENTS = frozenset("review-code:" + n for n in ("finder", "standards", "spec", "sota", "verifier", "spec-verifier"))


def path(base, agent_id, kind):
    """The file that holds the capture of `agent_id` for one event kind (handback or stop)."""
    return os.path.join(base, "captures", "%s.%s.json" % (agent_id, kind))


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
    p = subprocess.run(["git", "rev-parse", "--git-path", "review-code"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    rel = p.stdout.decode("utf-8", "replace").strip()
    if p.returncode != 0 or not rel:
        return
    base = os.path.join(os.getcwd(), rel)
    try:
        os.mkdir(os.path.join(base, "captures"), 0o700)  # not makedirs: a missing state directory stays missing
    except FileExistsError:
        pass
    fd, tmp = tempfile.mkstemp(dir=os.path.join(base, "captures"), prefix=".tmp-")  # mode 0600
    try:
        with os.fdopen(fd, "w", encoding="ascii") as fh:  # ensure_ascii escapes everything, a lone surrogate included
            json.dump({"agent_type": typ, "message": text}, fh)
        os.replace(tmp, path(base, aid, kind))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main():
    """Hook entry: read the event JSON on stdin, keep it, and always return 0."""
    try:
        ev = json.loads(sys.stdin.buffer.read().decode("utf-8", "replace"))
        if isinstance(ev, dict):
            keep(ev)
    except Exception:  # a hook must never fail the agent it watches
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
