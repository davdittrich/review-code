"""Post one review per run to a pull request, only after a digest-bound confirmation.

Stdlib only, Python 3.9 grammar. `build` is read-only on the forge: it turns the schema v1 report document into one
payload, anchors each verified finding against the forge's own diff, writes `<run>/post/payload.json` and prints the
preview and the sha256 of the payload bytes. `submit` re-reads that file, refuses unless its hash equals the digest
the user confirmed, creates one pending review, checks what the forge stored and only then sends the one event in
the module constant EVENT. No other review event exists in this file. Prints one JSON line; never a traceback;
forge and tool text never reaches stdout except the sanitised preview.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import hashlib
import json
import os
import re
import subprocess
from typing import List, Optional

import report
import sanitize
import target

Refuse = target.Refuse
EVENT = "COMMENT"
ID_RE = r"F-[0-9a-f]{10}"
MARK = "<!-- review-code:finding %s v1 -->"
REVIEW_MARK = "<!-- review-code:review v1 head=%s -->"
MARK_RE = re.compile(r"^<!-- review-code:finding (" + ID_RE + r") v1 -->$", re.M)
MAX_INLINE = 32
MAX_BODY = 60000
PER_PAGE = 100
MAX_PAGES = 20
LABEL = {"blocking": "Blocking", "required": "Required", "suggestion": "Suggestion", "noted": "Noted"}
STDOUT_KEYS = ("status", "reason", "detail", "digest", "inline", "in_body", "skipped", "unverifiable", "redacted",
               "posted", "review_id")
USAGE = "usage: post.py build --run DIR | submit --run DIR --digest HEX"
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?")
_SEGMENT = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*")  # no leading dash: a segment can never read as an option


def _cli(tool: str, args: List[str], cwd: str, stdin: Optional[bytes] = None):
    """The one subprocess seam: argv list, no shell. Returns (exit code, stdout bytes, stderr bytes)."""
    try:
        p = subprocess.run([tool] + list(args), cwd=cwd, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError:
        raise Refuse(tool + "-missing")
    return p.returncode, p.stdout, p.stderr


def _api(tool: str, host: str, method: str, endpoint: str, body=None, cwd: str = "."):
    """One REST call; parsed JSON, or None for an empty 2xx body. A failure carries at most `HTTP nnn`, never forge text."""
    args = ["api", "--hostname", host, "-X", method, endpoint]
    data = None
    if body is not None:
        args += ["--input", "-"]
        data = json.dumps(body, sort_keys=True).encode("utf-8")
    rc, out, err = _cli(tool, args, cwd, data)
    if rc != 0:
        m = re.search(r"HTTP (\d{3})", err.decode("utf-8", "replace"))
        raise Refuse(tool + "-failed", "HTTP " + m.group(1) if m else "")
    if not out.strip():
        return None
    try:
        return json.loads(out.decode("utf-8"))
    except ValueError:
        raise Refuse(tool + "-failed")


def _pages(tool: str, host: str, endpoint: str, cwd: str) -> list:
    items = []
    for no in range(1, MAX_PAGES + 1):
        got = _api(tool, host, "GET", "%s%sper_page=%d&page=%d" % (endpoint, "&" if "?" in endpoint else "?", PER_PAGE, no),
                   cwd=cwd)
        if not isinstance(got, list):
            raise Refuse(tool + "-failed")
        items += got
        if len(got) < PER_PAGE:
            return items
    raise Refuse("too-many-comments")


def _target(run: str) -> dict:
    """Forge, host, repository, number and head from target.json alone: never gh's `{owner}/{repo}` placeholders or the cwd remote."""
    try:
        with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
            rec = json.load(fh)
        kind, url, head = rec["kind"], rec["url"], rec["head_sha"]
    except (OSError, ValueError, KeyError, TypeError):
        raise Refuse("no-target")
    if kind not in ("pr", "mr"):
        raise Refuse("not-a-forge-target")
    m = (target._PR_URL if kind == "pr" else target._MR_URL).fullmatch(url) if isinstance(url, str) else None
    segments = m.group(2).split("/") if m else []
    if (m is None or not _HOST.fullmatch(m.group(1)) or not all(_SEGMENT.fullmatch(x) for x in segments)
            or len(segments) < 2 or (kind == "pr" and len(segments) != 2) or rec.get("number") != int(m.group(3))
            or not isinstance(head, str) or not target._OID.fullmatch(head)):
        raise Refuse("bad-target-url")
    return {"forge": "github" if kind == "pr" else "gitlab", "host": m.group(1), "repo": m.group(2),
            "number": int(m.group(3)), "head_sha": head, "url": url, "run": run}


class GitHub:
    def __init__(self, info: dict):
        self.i = info
        self.pr = "repos/%s/pulls/%d" % (info["repo"], info["number"])

    def _call(self, method: str, endpoint: str, body=None):
        return _api("gh", self.i["host"], method, endpoint, body, self.i["run"])

    def state(self) -> str:
        """The live head commit of the pull request."""
        try:
            got = self._call("GET", self.pr)
            sha, merged, draft, state = got["head"]["sha"], got.get("merged"), got.get("draft"), got.get("state")
        except (KeyError, TypeError, AttributeError):
            raise Refuse("gh-failed")
        if merged is True:
            raise Refuse("merged")
        if draft is True:
            raise Refuse("draft")
        if state != "open":
            raise Refuse("closed")
        if not isinstance(sha, str) or not target._OID.fullmatch(sha):
            raise Refuse("gh-failed")
        return sha

    def diff(self) -> str:
        rc, out, _ = _cli("gh", ["pr", "diff", self.i["url"], "--color", "never"], self.i["run"])
        if rc != 0:
            raise Refuse("diff-unavailable")
        return out.decode("utf-8", "replace")

    def me(self) -> str:
        try:
            login = self._call("GET", "user")["login"]
        except Refuse as r:
            if r.reason != "gh-failed":
                raise
            raise Refuse("no-identity")
        except (KeyError, TypeError):
            raise Refuse("no-identity")
        if not isinstance(login, str) or not login:
            raise Refuse("no-identity")
        return login

    def existing(self, me: str) -> set:
        """Refuse when `me` already has a pending review (never deleted: it may hold their own work); else the ids already posted."""
        for r in _pages("gh", self.i["host"], self.pr + "/reviews", self.i["run"]):
            if isinstance(r, dict) and r.get("state") == "PENDING" and (r.get("user") or {}).get("login") == me:
                raise Refuse("pending-review-exists")
        return set()

    def create(self, payload: dict) -> int:
        body = {"commit_id": payload["head_sha"], "body": payload["body"],
                "comments": [{k: c[k] for k in ("path", "line", "side", "body")} for c in payload["comments"]]}
        try:
            got = self._call("POST", self.pr + "/reviews", body)
        except Refuse as r:
            raise Refuse("post-failed", r.detail) if r.reason == "gh-failed" else r
        rid = got.get("id") if isinstance(got, dict) else None
        if not isinstance(rid, int) or isinstance(rid, bool):
            raise Refuse("post-failed")
        return rid

    def verify(self, payload: dict, handle: int) -> None:
        stored = _pages("gh", self.i["host"], "%s/reviews/%d/comments" % (self.pr, handle), self.i["run"])
        want = sorted((c["path"], c["line"], c["side"], payload["head_sha"]) for c in payload["comments"])
        try:
            got = sorted((c["path"], c["line"], c["side"], c["commit_id"]) for c in stored)
        except (KeyError, TypeError):
            raise Refuse("verify-failed")
        if got != want:
            raise Refuse("verify-failed")

    def finish(self, handle: int) -> None:
        try:
            self._call("POST", "%s/reviews/%d/events" % (self.pr, handle), {"event": EVENT})
        except Refuse as r:
            raise Refuse("post-failed", r.detail) if r.reason == "gh-failed" else r

    def cleanup(self, handle: int) -> None:
        """Delete the pending review this run's own create returned; nothing else is ever listed or deleted."""
        self._call("DELETE", "%s/reviews/%d" % (self.pr, handle))


def _forge(info: dict):
    if info["forge"] != "github":
        raise Refuse("not-a-forge-target")  # merge request posting arrives with the GitLab adapter
    return GitHub(info)


def _anchors(diff_text: str) -> dict:
    """{(new path, new line): (tag, old line, old path)} for every context and added row of a unified diff."""
    rows = {}
    for _, path, tag, old_line, new_line, _, old_path in sanitize._walk(diff_text):
        if path and tag in (" ", "+"):
            rows[(path, new_line)] = (tag, old_line if tag == " " else None, old_path)
    return rows


def _prose(s) -> str:
    return sanitize.escape_for_code(" ".join(str(s).split()))[0]


def _render(f: dict, where: Optional[str] = None) -> str:
    quote = sanitize.escape_for_code(f["quote"])[0].split("\n")
    quote = ["%d: %s" % (f["line"] + n, ln) for n, ln in enumerate(quote)]
    fence = "`" * max(3, max(len(r) for r in re.findall(r"`+", "\n".join(quote)) or [""]) + 1)
    out = ["**%s** (%s, %s) - %s" % (LABEL[f["severity"]], _prose(f["axis"]), _prose(f["category"]), _prose(f["title"]))]
    out += ["**Location:** %s (outside the diff)" % where] if where else []
    out += ["**Trigger:** " + _prose(f["trigger"]),
            "**Evidence:** %s, score %d, %s" % (_prose(f["evidence_class"]), f["score"], _prose(f["origin"])),
            fence + "text"] + quote + [fence]
    out += ["**Fix:** " + _prose(f["fix"])] if f["fix"] else []
    return "\n".join(out + [MARK % f["id"]])


def _body(doc: dict, entries: List[str], n_unverifiable: int, head: str) -> str:
    out = ["**Verdict:** %s (advisory)" % _prose(doc["verdict"])]
    for ax in report.AXES:
        n = sum(1 for f in doc["findings"] if f["axis"] == ax)
        out += ["%s: %d finding%s" % (ax.capitalize(), n, "" if n == 1 else "s")] if n else []
    text = "\n".join(out)
    if entries:
        text += "\n\n" + "\n\n---\n\n".join(entries)
    if n_unverifiable:
        text += "\n\nNot posted: %d unverifiable items (see the terminal report)" % n_unverifiable
    return text + "\n\n" + REVIEW_MARK % head


def _preview(payload: dict) -> str:
    out = ["review-code post preview: %s %s head %s" % (payload["forge"], _prose(payload["url"]), payload["head_sha"]),
           "== review body ==", payload["body"]]
    for k, c in enumerate(payload["comments"], 1):
        out += ["== inline %d/%d: %s:%d (%s) ==" % (k, len(payload["comments"]), _prose(c["path"]), c["line"], c["side"]),
                c["body"]]
    return "\n".join(out + ["== not posted ==", "unverifiable: %d" % payload["unverifiable"]])


def build(run: str) -> dict:
    info = _target(run)
    doc = report.build(run)
    if not doc["findings"]:
        raise Refuse("nothing-to-post")
    gh = _forge(info)
    head = info["head_sha"]
    if gh.state() != head:  # check (a): the diff below belongs to the pinned head
        raise Refuse("head-moved")
    anchors = _anchors(gh.diff())
    gh.existing(gh.me())
    if gh.state() != head:  # check (b): nothing moved while the reads ran
        raise Refuse("head-moved")
    comments, entries, in_body = [], [], []
    for f in doc["findings"]:  # report order: axis, severity, path, line, id
        row = anchors.get((f["path"], f["line"]))
        if row is not None and len(comments) < MAX_INLINE:
            comments.append({"id": f["id"], "path": f["path"], "line": f["line"], "side": "RIGHT", "old_path": row[2],
                             "old_line": row[1], "body": _render(f)})
        else:
            entries.append(_render(f, "%s:%d" % (_prose(f["path"]), f["line"])))
            in_body.append(f["id"])
    n_unverifiable = sum(1 for i in doc["unverifiable"] if i["category"] != "sota")
    payload = {"version": 1, "forge": info["forge"], "host": info["host"], "repo": info["repo"],
               "number": info["number"], "url": info["url"], "head_sha": head,
               "body": _body(doc, entries, n_unverifiable, head), "comments": comments, "in_body": in_body,
               "unverifiable": n_unverifiable}
    if any(len(t) > MAX_BODY for t in [payload["body"]] + [c["body"] for c in comments]):
        raise Refuse("too-large")
    out_dir = os.path.join(run, "post")
    os.makedirs(out_dir, exist_ok=True)
    os.chmod(out_dir, 0o700)
    path = os.path.join(out_dir, "payload.json")
    target.write_private(path, json.dumps(payload, sort_keys=True, indent=2))
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    print(_preview(payload))
    return {"status": "ok", "digest": digest, "inline": len(comments), "in_body": len(in_body), "skipped": 0,
            "unverifiable": n_unverifiable, "redacted": 0}


def submit(run: str, digest: str) -> dict:
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise Refuse("usage", USAGE)
    try:
        with open(os.path.join(run, "post", "payload.json"), "rb") as fh:
            raw = fh.read()
    except OSError:
        raise Refuse("no-payload")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise Refuse("digest-mismatch")
    payload = json.loads(raw.decode("utf-8"))
    info = _target(run)
    if not isinstance(payload, dict) or any(payload.get(k) != info[k] for k in ("forge", "host", "repo", "number", "head_sha")):
        raise Refuse("payload-mismatch")
    gh = _forge(info)
    if gh.state() != info["head_sha"]:  # check (c): before the first write
        raise Refuse("head-moved")
    gh.existing(gh.me())  # fresh: a pending review made since build would be stacked on or confused with ours
    handle = gh.create(payload)
    try:
        gh.verify(payload, handle)
        if gh.state() != info["head_sha"]:  # check (d): before the event
            raise Refuse("head-moved")
        gh.finish(handle)
    except BaseException:  # any failure, an interrupt too, withdraws the review this call created
        try:
            gh.cleanup(handle)
        except Refuse:
            raise Refuse("cleanup-failed", "pending review %d" % handle)
        raise
    out = {"status": "ok", "posted": len(payload["comments"]), "in_body": len(payload["in_body"]), "review_id": handle}
    target.write_private(os.path.join(run, "post", "result.json"), json.dumps(out, sort_keys=True))
    return out


def main(argv: List[str]) -> int:
    try:
        if argv[:2] == ["build", "--run"] and len(argv) == 3:
            out = build(argv[2])
        elif argv[:2] == ["submit", "--run"] and len(argv) == 5 and argv[3] == "--digest":
            out = submit(argv[2], argv[4])
        else:
            raise Refuse("usage", USAGE)
        rc = 0
    except Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason}, 1
        if r.detail:
            out["detail"] = r.detail
    except (OSError, KeyError, ValueError, TypeError) as e:
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    print(json.dumps({k: v for k, v in out.items() if k in STDOUT_KEYS}, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
