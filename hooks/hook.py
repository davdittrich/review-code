#!/usr/bin/env python3
"""PostToolUse hook for the Agent tool: ingest a review-code lane or verifier reply (review-code-1ej).

Reads the hook JSON on stdin. Stays silent unless the call is a review-code lane or verifier call whose
(subagent_type, prompt) equals one call of a parallel run's plan.json or candidates.json verify_calls. Lane, shard and
batch come from that plan entry, never from the reply. Calls report.ingest / report.verdicts in-process (the heredoc
path's parser). Prints one JSON object whose additionalContext is one derived line (counts, never reply text), and
always exits 0. Python 3.9 stdlib only.
"""
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "skills", "review-code", "scripts"))
import report  # noqa: E402
import target  # noqa: E402

MAX_REPLY = 256 * 1024
HEADER = "Verify. Handoffs:\n"
HANDOFF = re.compile(r"F-[0-9a-f]{10}\.md")


class Skip(Exception):
    pass


class Malformed(Exception):
    pass


def emit(line):
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": line}}))


def prompt_paths(prompt):
    """The absolute paths a plan prompt names: the lane input, or the verifier handoffs. [] for any other prompt."""
    if prompt.startswith(HEADER):
        return prompt[len(HEADER):].split("\n")
    m = re.search(r"^\S[^\n]* lane(?:, shard [0-9]+)?\. Input: (/[^\n]+)$", prompt)
    return [m.group(1)] if m else []


def run_of(path, base):
    """The run dir (a child of the git-path review-code dir) that realpath(path) lies in; Skip otherwise."""
    cur = os.path.realpath(path)
    while cur != os.path.dirname(cur):
        if os.path.dirname(cur) == base:
            if cur.startswith(os.path.join(base, "review-code.")):
                return cur
            break
        cur = os.path.dirname(cur)
    raise Skip("path is not in a run dir")


def reply_text(resp):
    if not isinstance(resp, dict) or resp.get("status") != "completed":
        raise Malformed("status %s" % (resp.get("status") if isinstance(resp, dict) and isinstance(resp.get("status"), str)
                                       and re.fullmatch(r"[a-z_]{1,32}", resp["status"]) else "unknown"))
    parts = resp.get("content")
    text = "\n".join(p["text"] for p in parts if isinstance(p, dict) and isinstance(p.get("text"), str)) if isinstance(parts, list) else ""
    if len(text.encode("utf-8")) > MAX_REPLY:
        raise Malformed("reply over 256 KiB")
    if not any(True for _ in report._objects(text)):
        raise Malformed("no finding lines")
    return text


def handle(doc):
    ti = doc.get("tool_input")
    agent = ti.get("subagent_type") if isinstance(ti, dict) else None
    prompt = ti.get("prompt") if isinstance(ti, dict) else None
    if not (isinstance(agent, str) and agent.startswith("review-code:") and agent != "review-code:fixer"):
        return None
    if not isinstance(prompt, str):
        raise Skip("no prompt")
    paths = prompt_paths(prompt)
    if not paths:
        raise Skip("no run path in the prompt")
    cwd = doc.get("cwd")
    if not (isinstance(cwd, str) and os.path.isabs(cwd)):
        raise Skip("no cwd")
    base = os.path.realpath(os.path.join(cwd, subprocess.run(
        ["git", "rev-parse", "--git-path", "review-code"], cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=True).stdout.decode("utf-8", "replace").strip()))
    run = run_of(paths[0], base)
    verify = prompt.startswith(HEADER)
    if verify:
        hand = os.path.join(run, "handoff")
        for p in paths:
            if not HANDOFF.fullmatch(os.path.basename(p)) or os.path.realpath(p) != os.path.join(hand, os.path.basename(p)):
                raise Skip("handoff path is not in the run")
    plan = report._plan(run)
    if plan.get("mode") != "parallel":
        raise Skip("plan mode is not parallel")
    if verify:
        calls = target.load_json(run, "candidates.json").get("verify_calls")
    else:
        calls = plan.get("calls")
    hit = [c for c in (calls if isinstance(calls, list) else []) if isinstance(c, dict) and c.get("agent") == agent and c.get("prompt") == prompt]
    if len(hit) != 1:
        raise Skip("prompt is not a plan call")
    c = hit[0]
    if verify:
        key = "batch %s" % c["batch"]
    else:
        key = c["lane"] if c.get("shard") is None else "%s@%s" % (c["lane"], c["shard"])
    try:
        text = reply_text(doc.get("tool_response"))
        if not verify and (os.path.exists(os.path.join(run, "replies", "verify")) or os.path.exists(os.path.join(run, "verdicts.json"))):
            raise Malformed("verification already started")
    except Malformed as m:
        return "review-code hook: NOT ingested %s: %s; ingest by heredoc" % (key, m)
    if verify:
        out = report.verdicts(run, text, c["batch"])
        return "review-code verdicts: ok batch=%d confirmed=%d refuted=%d foreign=%d" % (
            c["batch"], len(out["confirmed"]), len(out["refuted"]), out["foreign"])
    out = report.ingest(run, text, c["lane"], c.get("shard"))
    return "review-code ingest: ok lane=%s shard=%s ids=%d invalid=%d" % (
        c["lane"], "none" if c.get("shard") is None else c["shard"], len(out["ids"]) + len(out["spec_ids"]), out["dropped"]["invalid"])


def main():
    try:
        line = handle(json.loads(sys.stdin.buffer.read().decode("utf-8", "replace")))
    except Skip as s:
        line = "review-code hook: skipped (%s)" % s
    except target.Refuse as r:
        line = "review-code hook: error Refuse:%s; ingest by heredoc" % re.sub(r"[^a-z0-9-]", "", str(r.reason).lower())[:40]
    except BaseException as e:  # noqa: B902 a hook must never fail the tool call
        line = "review-code hook: error %s; ingest by heredoc" % type(e).__name__
    if line:
        emit(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
