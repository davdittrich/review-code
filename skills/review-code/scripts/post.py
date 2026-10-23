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
import urllib.parse
from typing import List, Optional

import nit
import prior
import report
import sanitize
import target

Refuse = target.Refuse
EVENT = "COMMENT"
ID_RE = report.ID_RE
MARK = "<!-- review-code:finding %s v1 -->"
REVIEW_MARK = "<!-- review-code:review v1 head=%s -->"
MARK_RE = re.compile(r"^<!-- review-code:finding (" + ID_RE + r") v1 -->$", re.M)
REVIEW_RE = prior.REVIEW  # also the carried fixed= and outdated= lists, nothing else
NOT_RUN_LINE = re.compile(r"\*\*Not run:\*\* (?:%s)(?:, (?:%s))*" % ((report.NOT_RUN_ITEM,) * 2))
BASIS_LINE = re.compile(r"\*\*Pinned:\*\* (?:head|tree) [0-9a-f]{40}(?:[0-9a-f]{24})?, checkout difference [0-9]+ paths?"
                        r"(?:; lost capability: [a-z]+: [a-z ]+)*")  # report.basis_line plus plan.py's fixed capability text
_LINE_BREAK = re.compile(r"\r\n|\r|\n")  # the breaks a forge renders: a GitLab path holding one could forge a diff header
# Every reference GitHub or GitLab (docs.gitlab.com/user/markdown, "GitLab-specific references") links or pings. The
# forges match rendered text, so escaping cannot defuse one; a code span can. Commit SHAs and bare URLs are not covered.
_REF = re.compile(r"[@#!&~%$^](?=\w)[\w./-]*"  # @user #1 !1 &epic ~label %milestone $snippet ^alert
                  r'|[~%]"[^"`]*"|\*iteration:(?:"[^"`]*"|[\w./-]+)'  # ~"two words" %"two words" *iteration:x
                  r"|\[\[[^\]`]*\]\]|\[(?:issue|work_item|epic|cadence|vulnerability|feature_flag|contact|wiki_page):[^\]`]*\]"
                  # GL-1 GH-1 issue keys, namespace/project>. No boundary anchor: `_GH-1_` renders `GH-1` as its own text,
                  # and so does `GH-2GH-1` once `GH-2` is a code span; a span inside a word is only cosmetic
                  r"|(?:GL|GH)-\d+|[\w.-]+/[\w.-][\w./-]*>")
# Adapted from the gitleaks rule set, config/gitleaks.toml (MIT, see NOTICE). Regex only: no entropy check, no encoded or split secrets.
# A private key is detected by its BEGIN or END line only; its body has no reliable shape (PGP armor has a blank line,
# CRLF, string literals), so a hit blanks every free-text field of the finding instead of guessing where the key ends.
_PEM = r"-----(?:BEGIN|END)[ A-Z0-9_-]{0,100}PRIVATE KEY(?: BLOCK)?-----"
SECRET_RULES = tuple((rule, re.compile(rx)) for rule, rx in (
    ("aws-access-token", r"\b(?:A3T[A-Z0-9]|AKIA|ASIA|ABIA|ACCA)[A-Z2-7]{16}\b"),
    ("github-pat", r"ghp_[0-9a-zA-Z]{36}"),
    ("github-oauth", r"gho_[0-9a-zA-Z]{36}"),
    ("github-fine-grained-pat", r"github_pat_\w{82}"),
    ("gitlab-pat", r"glpat-[\w-]{20}"),
    ("gcp-api-key", r"\bAIza[\w-]{35}"),
    ("slack-bot-token", r"xoxb-[0-9]{10,13}-[0-9]{10,13}[a-zA-Z0-9-]*"),
    ("stripe-access-token", r"\b(?:sk|rk)_(?:test|live|prod)_[a-zA-Z0-9]{10,99}"),
    ("jwt", r"\bey[a-zA-Z0-9]{17,}\.ey[a-zA-Z0-9/\\_-]{17,}\.[a-zA-Z0-9/\\_-]{10,}"),
    ("private-key", _PEM),
    # a backtick counts as a quote too: _prose turns it into `'`, so the rendered text would match after redaction ran
    ("generic-secret", r"""(?i)(?:password|secret|token|api[_-]?key)\s*[:=]\s*["'`][^"'\s]{8,}["'`]"""),
))
MAX_INLINE = 32
MAX_BODY = 60000
PER_PAGE = 100
MAX_PAGES = 20
LABEL = {"blocking": "Blocking", "required": "Required", "suggestion": "Suggestion", "nit": "Nit", "noted": "Noted"}
STDOUT_KEYS = ("status", "reason", "detail", "digest", "inline", "in_body", "skipped", "unverifiable", "redacted",
               "posted", "review_id")
USAGE = "usage: post.py build --run DIR | submit --run DIR --digest HEX"
_HOST = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*(?::[0-9]+)?")
_SEGMENT = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9_.-]*")  # no leading dash: a segment can never read as an option


_cli = target.cli_run  # tests patch this name


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


def _changed(run: str, last: Optional[str], head: str) -> Optional[set]:
    """Paths that differ between the prior review head and `head`, else None (no prior head, not reachable, or git failed):
    only a changed file lets an absent finding count as fixed."""
    if not last:
        return None
    rc, out, _ = target._run(["diff", "--name-only", "--no-renames", "-z", last, head], target.load_target(run)["repo"])
    return set(out.decode("utf-8", "surrogateescape").split("\0")) - {""} if rc == 0 else None  # exact names, like every listing


def _target(run: str) -> dict:
    """Forge, host, repository, number and head from target.json alone: never gh's `{owner}/{repo}` placeholders or the cwd remote."""
    rec = target.load_target(run)
    kind, url, head = rec["kind"], rec["url"], rec["head_sha"]
    if kind not in ("pr", "mr"):
        raise Refuse("not-a-forge-target")
    m = (target.PR_URL if kind == "pr" else target.MR_URL).fullmatch(url) if isinstance(url, str) else None
    segments = m.group(2).split("/") if m else []
    if (m is None or not _HOST.fullmatch(m.group(1)) or not all(_SEGMENT.fullmatch(x) for x in segments)
            or len(segments) < 2 or (kind == "pr" and len(segments) != 2) or rec.get("number") != int(m.group(3))
            or not isinstance(head, str) or not target.OID.fullmatch(head)):
        raise Refuse("bad-target-url")
    return {"forge": "github" if kind == "pr" else "gitlab", "host": m.group(1), "repo": m.group(2),
            "number": int(m.group(3)), "head_sha": head, "url": url, "run": run}


class GitHub:
    refs = None  # GitLab only: the diff refs read by the last state()
    login = None  # the user existing() checked; a failed create deletes only a pending review of theirs
    ids = frozenset()  # the finding ids of the payload the last create sent
    prior_state = prior.read([], [], None)  # existing() replaces it with this PR's own earlier reviews
    unknown = False  # the event's outcome could not be read: cleanup must leave the review alone

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
        if not isinstance(sha, str) or not target.OID.fullmatch(sha):
            raise Refuse("gh-failed")
        return sha

    def diff(self) -> str:
        rc, out, _ = _cli("gh", ["pr", "diff", self.i["url"], "--color", "never"], self.i["run"])
        if rc != 0:
            raise Refuse("diff-unavailable")
        return "\n".join(target._diff_line(ln) for ln in out.split(b"\n"))  # header lines name files: exact; content: lossy

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
        """Refuse when `me` already has a pending review (never deleted: it may hold their own work); else the finding ids
        already posted: whole-line markers in review bodies and review comments written by `me` (exact login), never by anyone else."""
        self.login = me
        reviews = _pages("gh", self.i["host"], self.pr + "/reviews", self.i["run"])
        for r in reviews:
            if isinstance(r, dict) and r.get("state") == "PENDING" and (r.get("user") or {}).get("login") == me:
                raise Refuse("pending-review-exists")
        self.prior_state = prior.read(reviews, _pages("gh", self.i["host"], self.pr + "/comments", self.i["run"]), me)
        return set(self.prior_state["findings"])

    def create(self, payload: dict) -> int:
        self.ids = {c["id"] for c in payload["comments"]}  # cleanup deletes a review only when it holds nothing else
        body = {"commit_id": payload["head_sha"], "body": payload["body"],
                "comments": [{k: c[k] for k in ("path", "line", "side", "body")} for c in payload["comments"]]}
        try:
            got = self._call("POST", self.pr + "/reviews", body)
            rid = got.get("id") if isinstance(got, dict) else None
            if not isinstance(rid, int) or isinstance(rid, bool):
                raise Refuse("post-failed")
        except BaseException as e:  # the forge may have stored the review although its answer was lost
            self._orphan(payload["head_sha"])
            if isinstance(e, Refuse) and e.reason == "gh-failed":
                raise Refuse("post-failed", e.detail)
            raise
        return rid

    def _orphan(self, head: str) -> None:
        """Delete the pending review a failed create may still have made: PENDING, written by `me`, with this payload's
        review marker as a whole line. existing() showed `me` had no pending review just before, and one the user starts
        in the web UI meanwhile has no marker, so it is never touched."""
        try:
            found = [r["id"] for r in _pages("gh", self.i["host"], self.pr + "/reviews", self.i["run"])
                     if isinstance(r, dict) and r.get("state") == "PENDING" and (r.get("user") or {}).get("login") == self.login
                     and isinstance(r.get("id"), int) and isinstance(r.get("body"), str)
                     and _marked(r["body"], head)]
        except Refuse:
            raise Refuse("cleanup-failed", "pending review unknown")
        for rid in found:
            self.cleanup(rid)

    def verify(self, payload: dict, handle: int) -> None:
        """Every stored comment of the pending review must be one of ours: path and commit id, our marker line in its
        body, and its place. GitHub lists a PENDING review's comments with `line`, `side` and `original_line` null and a
        diff `position` instead (observed live on PR 1); position is read from the live diff, whose head check (d) confirms. A comment
        that does carry line and side is compared on those."""
        stored = _pages("gh", self.i["host"], "%s/reviews/%d/comments" % (self.pr, handle), self.i["run"])
        want = list(payload["comments"])
        if len(stored) != len(want):
            raise Refuse("verify-failed")
        anchors = _anchors(self.diff())
        for c in stored:
            for k, w in enumerate(want):
                if self._same(c, w, payload["head_sha"], anchors):
                    del want[k]
                    break
            else:
                raise Refuse("verify-failed")

    @staticmethod
    def _same(c, w: dict, head: str, anchors: dict) -> bool:
        if not isinstance(c, dict) or c.get("path") != w["path"] or c.get("commit_id") != head:
            return False
        if not isinstance(c.get("body"), str) or MARK % w["id"] not in c["body"].replace("\r\n", "\n").split("\n"):
            return False
        if c.get("line") is not None or c.get("side") is not None:
            return c.get("line") == w["line"] and c.get("side") == w["side"]
        row, pos = anchors.get((w["path"], w["line"])), c.get("position")
        return row is not None and isinstance(pos, int) and not isinstance(pos, bool) and pos == row[3]

    def recount(self, payload: dict, handle: int) -> None:
        """Right before the event: a comment the user starts in the web UI joins their one pending review, so a changed
        count refuses. This narrows the window after verify to one listing; it cannot close it."""
        if len(_pages("gh", self.i["host"], "%s/reviews/%d/comments" % (self.pr, handle), self.i["run"])) != len(payload["comments"]):
            raise Refuse("verify-failed")

    def finish(self, handle: int) -> None:
        try:
            self._call("POST", "%s/reviews/%d/events" % (self.pr, handle), {"event": EVENT})
        except Refuse as r:  # GitHub may have applied the event although its answer was lost (5xx, timeout)
            state = self._state(handle)
            if state is None:
                self.unknown = True  # never delete: the review may be public, and a DELETE would fail on it anyway
                raise Refuse("post-partial", "published unknown")
            if state != "PENDING":
                return  # it was submitted: the review is public
            raise Refuse("post-failed", r.detail) if r.reason == "gh-failed" else r

    def cleanup(self, handle: int) -> None:
        """Delete the pending review this run's own create made, only while every comment in it carries one of this
        payload's markers as a whole line. A comment the user starts in the web UI joins their one pending review, and
        the delete would take it too: then the review is kept (`review-kept`) for the user to submit or delete."""
        if self.unknown:
            return
        try:
            stored = _pages("gh", self.i["host"], "%s/reviews/%d/comments" % (self.pr, handle), self.i["run"])
        except Refuse:
            raise Refuse("cleanup-failed", "pending review %d" % handle)
        if not all(isinstance(c, dict) and isinstance(c.get("body"), str)
                   and set(MARK_RE.findall(c["body"].replace("\r\n", "\n"))) & self.ids for c in stored):
            raise Refuse("review-kept", "pending review %d" % handle)
        try:
            self._call("DELETE", "%s/reviews/%d" % (self.pr, handle))
        except Refuse:
            if self._state(handle) not in (None, "PENDING"):  # the event landed before the failure (an interrupt)
                raise Refuse("post-partial", "published")
            raise Refuse("cleanup-failed", "pending review %d" % handle)

    def _state(self, handle: int) -> Optional[str]:
        """The review's state, or None when it cannot be read."""
        try:
            got = self._call("GET", "%s/reviews/%d" % (self.pr, handle))
        except Refuse:
            return None
        state = got.get("state") if isinstance(got, dict) else None
        return state if isinstance(state, str) else None


class GitLab:
    """A merge request review as draft notes: a position-less summary draft, one positioned draft per inline finding, then
    one publish call that takes no body. The user's own drafts block posting, because that call publishes every draft."""

    _REFS = ("base_sha", "start_sha", "head_sha")
    refs = None  # {base_sha, start_sha, head_sha} read by the last state()
    prior_state = prior.read([], [], None)  # GitLab keeps no prior state: `--since-last` refuses there, re-reviews post as before

    def __init__(self, info: dict):
        self.i = info
        self.mr = "projects/%s/merge_requests/%d" % (urllib.parse.quote(info["repo"], safe=""), info["number"])
        self.created, self.marks = [], (set(), None)  # draft ids this call made; the marker lines that identify them
        self.uid, self.seen = None, set()  # the user existing() checked; the ids of their notes it saw

    def _call(self, method: str, endpoint: str, body=None):
        return _api("glab", self.i["host"], method, endpoint, body, self.i["run"])

    def _pages(self, suffix: str) -> list:
        return _pages("glab", self.i["host"], self.mr + suffix, self.i["run"])

    def _drafts(self) -> list:
        try:
            return self._pages("/draft_notes")
        except Refuse as r:
            raise Refuse("draft-notes-unavailable") if r.detail == "HTTP 404" else r

    def state(self) -> str:
        """The live head commit of the merge request; the three diff refs are kept in `refs`."""
        try:
            got = self._call("GET", self.mr)
            state, draft, refs = got["state"], got.get("draft"), got.get("diff_refs")
        except (KeyError, TypeError, AttributeError):
            raise Refuse("glab-failed")
        if state == "closed":
            raise Refuse("closed")
        if state == "merged":
            raise Refuse("merged")
        if state == "locked":
            raise Refuse("locked")
        if state != "opened":
            raise Refuse("glab-failed")
        if draft is True:
            raise Refuse("draft")
        if not (isinstance(refs, dict) and all(isinstance(refs.get(k), str) and target.OID.fullmatch(refs[k]) for k in self._REFS)):
            raise Refuse("diff-refs-pending")  # GitLab has not computed them yet
        self.refs = {k: refs[k] for k in self._REFS}
        return self.refs["head_sha"]

    def diff(self) -> str:
        """The merge request's own diff as one unified diff: git headers built from each file's paths, then its hunks."""
        try:
            entries = self._pages("/diffs")
        except Refuse as r:
            raise Refuse("diff-unavailable") if r.detail == "HTTP 404" else r
        out = []
        for e in entries:
            if not isinstance(e, dict) or e.get("collapsed") or e.get("too_large"):
                continue  # not anchorable
            old, new, hunks = e.get("old_path"), e.get("new_path"), e.get("diff")
            if not all(isinstance(x, str) and x and not _LINE_BREAK.search(x) for x in (old, new)) or not isinstance(hunks, str):
                continue  # a path with a line break could forge a header line
            out.append("diff --git a/%s b/%s\n--- a/%s\n+++ b/%s\n%s" % (old, new, old, new, hunks if hunks.endswith("\n") else hunks + "\n"))
        return "".join(out)

    def me(self) -> int:
        try:
            uid = self._call("GET", "user")["id"]
        except Refuse as r:
            if r.reason != "glab-failed":
                raise
            raise Refuse("no-identity")
        except (KeyError, TypeError):
            raise Refuse("no-identity")
        if not isinstance(uid, int) or isinstance(uid, bool):
            raise Refuse("no-identity")
        return uid

    def existing(self, me: int) -> set:
        """Refuse when the user already has any draft (never deleted: the publish call would send it too); else the finding
        ids already posted: whole-line markers in notes whose author id is `me`, never in anyone else's."""
        if self._drafts():
            raise Refuse("pending-drafts-exist")
        ids, self.uid, self.seen = set(), me, set()
        for n in self._pages("/notes"):
            a = n.get("author") if isinstance(n, dict) else None
            if isinstance(a, dict) and a.get("id") == me and isinstance(n.get("body"), str):
                self.seen.add(n.get("id"))
                ids.update(MARK_RE.findall(n["body"].replace("\r\n", "\n")))
        return ids

    def _draft(self, body: dict) -> int:
        try:
            got = self._call("POST", self.mr + "/draft_notes", body)
        except Refuse as r:
            if r.reason != "glab-failed":
                raise
            raise Refuse("draft-notes-unavailable") if r.detail == "HTTP 404" else Refuse("post-failed", r.detail)
        did = got.get("id") if isinstance(got, dict) else None
        if not isinstance(did, int) or isinstance(did, bool):
            raise Refuse("post-failed")
        self.created.append(did)
        return did

    def create(self, payload: dict) -> int:
        """Create the summary draft, then one draft per inline finding; a failure part way deletes the drafts made so far."""
        self.marks = ({c["id"] for c in payload["comments"]}, payload["head_sha"])  # finding ids, review head
        try:
            first = self._draft({"note": payload["body"]})
            for c in payload["comments"]:
                pos = dict(payload["diff_refs"], position_type="text", old_path=c["old_path"], new_path=c["path"], new_line=c["line"])
                if c["old_line"] is not None:  # an unchanged row needs both lines, or the publish call fails with HTTP 500
                    pos["old_line"] = c["old_line"]
                self._draft({"note": c["body"], "commit_id": payload["head_sha"], "position": pos})
        except BaseException:
            self.cleanup(None)
            raise
        return first

    def verify(self, payload: dict, handle: int) -> None:
        """Every draft the user now has must be one of ours, at the position we sent."""
        drafts = self._drafts()
        want = {c["id"]: c for c in payload["comments"]}
        if len(drafts) != 1 + len(want) or {d.get("id") if isinstance(d, dict) else None for d in drafts} != set(self.created):
            raise Refuse("verify-failed")
        review, seen = self.marks[1], set()
        for d in drafts:
            note, pos = d.get("note"), d.get("position")
            if not isinstance(note, str):
                raise Refuse("verify-failed")
            if _marked(note, review):
                ok = not pos and "summary" not in seen
                seen.add("summary")
            else:
                found = MARK_RE.findall(note.replace("\r\n", "\n"))
                c = want.get(found[0]) if len(found) == 1 else None
                ok = c is not None and found[0] not in seen and isinstance(pos, dict) and all(
                    pos.get(k) == v for k, v in dict(payload["diff_refs"], position_type="text", old_path=c["old_path"],
                                                     new_path=c["path"], new_line=c["line"], old_line=c["old_line"]).items())
                seen.add(found[0] if c else None)
            if not ok:
                raise Refuse("verify-failed")

    def recount(self, payload: dict, handle: int) -> None:
        """Right before publish: the publish call sends every draft, so one the user added after verify refuses."""
        if len(self._drafts()) != 1 + len(payload["comments"]):
            raise Refuse("verify-failed")

    def finish(self, handle: int) -> None:
        """Publish every draft in one call that carries no body: no review state, no note."""
        try:
            self._call("POST", self.mr + "/draft_notes/bulk_publish")
        except Refuse as r:
            raise self._published(r.detail) if r.reason == "glab-failed" else r

    def _published(self, detail: str) -> Refuse:
        """The refusal for a failed publish call, which may have published some drafts before it failed: the notes of
        `me` that were not there at existing() and carry a marker line of this payload are public."""
        try:
            n = sum(1 for x in self._pages("/notes") if isinstance(x, dict) and x.get("id") not in self.seen
                    and (x.get("author") or {}).get("id") == self.uid and self._ours({"id": x.get("id"), "note": x.get("body")}))
        except Refuse:
            return Refuse("post-partial", "published unknown")
        return Refuse("post-partial", "published %d of %d" % (n, 1 + len(self.marks[0]))) if n else Refuse("post-failed", detail)

    def _ours(self, d) -> bool:
        ids, review = self.marks
        if not (isinstance(d, dict) and isinstance(d.get("id"), int) and isinstance(d.get("note"), str)):
            return False
        note = d["note"].replace("\r\n", "\n")
        return _marked(note, review) or any(i in ids for i in MARK_RE.findall(note))

    def cleanup(self, handle) -> None:
        """Delete the drafts that hold a whole-line marker of this payload; the user's other drafts are never touched."""
        try:
            mine = [d["id"] for d in self._drafts() if self._ours(d)]
        except Refuse:
            mine = list(self.created)  # the listing failed: fall back to what this call made
        left = []
        for i in mine:
            try:
                self._call("DELETE", "%s/draft_notes/%d" % (self.mr, i))
            except Refuse:
                left.append(i)
        if left:
            raise Refuse("cleanup-failed", "drafts " + ",".join(str(i) for i in left))


def _marked(text: str, head: str) -> bool:
    """True when `text` holds a whole-line review marker for `head` (any carried lists)."""
    return any(prior.mark_head(ln) == head for ln in text.replace("\r\n", "\n").split("\n"))


def _forge(info: dict):
    return GitHub(info) if info["forge"] == "github" else GitLab(info)


def _live(adapter, head: str, refs) -> None:
    """The live head must be the pinned head and the diff refs (None on GitHub) must be those the diff was read at."""
    if adapter.state() != head or adapter.refs != refs:
        raise Refuse("head-moved")


def _anchors(diff_text: str) -> dict:
    """{(new path, new line): (tag, old line, old path, position)} for every context and added row of a unified diff.
    `position` is GitHub's: rows below the file's first @@ header, later @@ headers and `\\ No newline` lines counted."""
    rows, cur, pos = {}, None, 0
    for _, path, tag, old_line, new_line, _, old_path in sanitize.walk(diff_text):
        if path != cur:
            cur, pos = path, -1  # the file's first @@ is row 0, every later row (a second @@ too) adds one
        pos += 1
        if path and tag in (" ", "+"):
            rows[(path, new_line)] = (tag, old_line if tag == " " else None, old_path, pos)
    return rows


def _prose(s) -> str:
    """One line of repository-derived text that pings, links and renders nothing: references in code spans, the rest
    with `&`, `\\`, `<` and `[` escaped. `&` goes first, or `&commat;x` would render `@x`; `\\` is doubled, or it would
    escape the escape. A code span shows its text as written, so nothing inside one is escaped."""
    t = sanitize.escape_line(s).replace("`", "'")
    out, at = [], 0
    for m in _REF.finditer(t):
        out += [_escape(t[at:m.start()]), "`" + m.group() + "`"]
        at = m.end()
    return "".join(out + [_escape(t[at:])])


def _escape(t: str) -> str:
    return t.replace("&", "&amp;").replace("\\", "\\\\").replace("<", "&lt;").replace("[", "\\[")


def _scan(text: str) -> list:
    """[(rule, start, end)] for every rule match, in text order."""
    return sorted(((rule, m.start(), m.end()) for rule, rx in SECRET_RULES for m in rx.finditer(text)), key=lambda h: h[1:])


def _redact(text: str):
    """(text with each secret replaced by `[REDACTED:<rule>]`, the rule ids in text order). Overlaps keep the earliest, widest match."""
    hits, end = [], 0
    for rule, a, b in sorted(_scan(text), key=lambda h: (h[1], -h[2])):
        if a >= end:
            hits.append((rule, a, b))
            end = b
    for rule, a, b in reversed(hits):
        text = text[:a] + "[REDACTED:%s]" % rule + text[b:]
    return text, [h[0] for h in hits]


_SPAN = re.compile(r"(?<!\\)((?:\\\\)*)`(?:" + _REF.pattern + ")`")  # the only code span _prose writes, behind an even backslash run
_LOOSE = re.compile(r"[<`]|&(?!amp;|lt;)|(?<!\\)(?:\\\\)*\[")  # what _prose never leaves outside a fence


# The only lines build writes outside the quote fence, besides an empty line, `---` and the marker lines.
_SHAPE = re.compile(r"\*\*(?:Verdict|Location|Lines|Trigger|Evidence|Basis|Fix):\*\* |(?:nit: \*\*Nit|\*\*(?:%s))\*\* \(|(?:%s): \d+ findings?$"
                    r"|Not posted: \d+ unverifiable items |Areas inspected: " % ("|".join(LABEL.values()), "|".join(a.capitalize() for a in report.AXES)))


def _shown(ln: str) -> str:
    """The text a forge renders for a guarded line outside the fence (spans already blanked): escapes and entities undone.
    CommonMark drops a backslash only before ASCII punctuation; before anything else it stays as text."""
    return re.sub(r"\\([!-/:-@\[-`{-~])", r"\1", ln).replace("&lt;", "<").replace("&amp;", "&")


def _guard(text: str) -> None:
    """Refuse a line a forge or bot could run as a command or hide as markup, unless it is exactly one of our marker lines.
    A CR refuses outright: a forge that turns it into a line break would start a line no `N: ` prefix covers. Only the
    line shapes build writes pass (an allowlist: a `~~~` or indented line would open a block the checks below cannot
    see into). Outside the quote fence every line must also hold _prose's invariants (no raw `<`, `&`, backtick,
    reference or unescaped `[`); inside it every line carries a `N: ` or `| ` prefix. So an edited payload is held to the
    rules build applies; any fence CommonMark could read differently refuses."""
    if "\r" in text:
        raise Refuse("unsafe-text")
    fence = None
    for ln in text.split("\n"):
        marker = MARK_RE.fullmatch(ln) or REVIEW_RE.fullmatch(ln)
        if re.match(r"\s*(?:/|<!--)", ln) and not marker:
            raise Refuse("unsafe-text")
        if fence:
            if ln == fence:
                fence = None
            elif not re.match(r"\d+: |\| ", ln):  # nothing else, so no line can close the fence early
                raise Refuse("unsafe-text")
        elif re.fullmatch(r"`{3,}text", ln):
            fence = ln[:-4]
        elif marker or ln in ("", "---") or prior.PRIOR_LINE.fullmatch(ln) or prior.STATE_LINE.fullmatch(ln) or NOT_RUN_LINE.fullmatch(ln) or BASIS_LINE.fullmatch(ln):
            continue  # the last four are exact shapes of ids, counts and closed words: no repository text can ride in them
        elif not _SHAPE.match(ln):
            raise Refuse("unsafe-text")
        else:
            bare = _SPAN.sub(r"\1 ", ln)  # a space: a span is a code element, so text around it never joins into a reference
            if _LOOSE.search(bare) or _REF.search(_shown(bare)):
                raise Refuse("unsafe-text")
    if fence:
        raise Refuse("unsafe-text")


def _checked(texts: List[str]) -> None:
    """The guard and the secret scan over every outgoing body; a scanner failure refuses rather than posts unscanned."""
    for t in texts:
        _guard(t)
    try:
        found = any(_scan(t) for t in texts)
    except Exception:
        raise Refuse("scan-failed")
    if found:
        raise Refuse("secret-found")


def _first_line(quote: List[str], rows) -> Optional[int]:
    """The index into `rows` (the finding's line..end_line of the reviewed file) where `quote` sits, when exactly one
    place fits after whitespace folding: inner lines equal to their row, the first a row's tail, the last a row's head
    (a one-line quote anywhere in its row). None when no place or several fit: the line numbers would be a guess."""
    k, ws = len(quote), report._ws
    fits = [o for o in range(len(rows or ()) - k + 1)
            if all(ws(q) == ws(r) or (k == 1 and ws(q) in ws(r)) or (k > 1 and i == 0 and ws(r).endswith(ws(q)))
                   or (k > 1 and i == k - 1 and ws(r).startswith(ws(q))) for i, (q, r) in enumerate(zip(quote, rows[o:])))]
    return fits[0] if len(fits) == 1 else None


def _render(f: dict, rows=None, where: Optional[str] = None, keyed: bool = False, state: Optional[str] = None):
    """(the finding's markdown, the secret rules redacted in text order). Each raw field is redacted before it is
    escaped: a match run over escaped text could swallow an escape character and revive the markup it stopped."""
    raw, rules = {}, []
    basis = nit.basis_text(f)
    for k in ("title", "trigger", "quote", "fix", "basis"):
        raw[k], got = _redact(basis if k == "basis" else f[k])
        rules += got
    if keyed and "private-key" not in rules:  # another finding saw a key marker in this file: its body may be quoted here
        rules.append("private-key")
    if "private-key" in rules:  # key material may sit in any field, with or without the marker line next to it
        raw = {k: "[REDACTED:private-key]" for k in raw}
    quote = sanitize.escape_for_code(raw["quote"].replace("\r\n", "\n").replace("\r", "<U+000D>"))[0].split("\n")
    src = f["quote"].replace("\r\n", "\n").split("\n")  # numbered from the lane's own lines, if redaction kept their count
    at = _first_line(src, rows) if len(src) == len(quote) else None
    quote = ["%d: %s" % (f["line"] + at + n, ln) if at is not None else "| " + ln for n, ln in enumerate(quote)]
    fence = "`" * max(3, max(len(r) for r in re.findall(r"`+", "\n".join(quote)) or [""]) + 1)
    out = ["%s**%s** (%s, %s) - %s" % ("nit: " if f["severity"] == "nit" else "", LABEL[f["severity"]], _prose(f["axis"]),
                                      _prose(f["category"]), _prose(raw["title"]))]
    out += ["**State:** " + state] if state else []
    out += ["**Basis:** " + _prose(report._ws(raw["basis"]))] if basis else []
    out += ["**Location:** %s (outside the diff)" % where] if where else []
    span = "%d" % f["line"] if f["end_line"] == f["line"] else "%d-%d" % (f["line"], f["end_line"])
    out += ["**Lines:** %s (quoted without line numbers)" % span] if at is None else []
    out += ["**Trigger:** " + _prose(raw["trigger"]),
            "**Evidence:** %s, score %d, %s" % (_prose(f["evidence_class"]), f["score"], _prose(f["origin"])),
            fence + "text"] + quote + [fence]
    out += ["**Fix:** " + _prose(raw["fix"])] if raw["fix"] else []
    return "\n".join(out + [MARK % f["id"]]), rules


def _body(verdict: str, findings: List[dict], entries: List[str], n_unverifiable: int, head: str, areas: List[str],
          mark: Optional[str] = None, extra: tuple = ()) -> str:
    """`mark` is the review marker line (default: the head alone); `extra` the exact `**Prior:**` and `**Not run:**` lines."""
    out = ["**Verdict:** %s (advisory)" % _prose(verdict)]
    for ax in report.AXES:
        n = sum(1 for f in findings if f["axis"] == ax)
        out += ["%s: %d finding%s" % (ax.capitalize(), n, "" if n == 1 else "s")] if n else []
    more = len(areas) - report.MAX_AREAS  # CTX-02
    out += list(extra)
    out += ["Areas inspected: " + ", ".join(_prose(a) for a in areas[:report.MAX_AREAS]) + (" (+%d more)" % more if more > 0 else "")] if areas else []
    text = "\n".join(out)
    if entries:
        text += "\n\n" + "\n\n---\n\n".join(entries)
    if n_unverifiable:
        text += "\n\nNot posted: %d unverifiable items (see the terminal report)" % n_unverifiable
    return text + "\n\n" + (mark or REVIEW_MARK % head)


def _basis(b: dict) -> Optional[str]:
    """The `**Pinned:**` line (gh#48): pinned head or tree, difference size, each lost capability; None for an older run."""
    if not b["pinned_tree"]:
        return None
    return "; ".join([report.basis_line(b, "**Pinned:** ")] + ["lost capability: " + c for c in b["lost_capabilities"]])


def _preview(payload: dict) -> str:
    out = ["review-code post preview: %s %s head %s" % (payload["forge"], _prose(payload["url"]), payload["head_sha"]),
           "== review body ==", payload["body"]]
    for k, c in enumerate(payload["comments"], 1):
        out += ["== inline %d/%d: %s:%d (%s) ==" % (k, len(payload["comments"]), _prose(c["path"]), c["line"], c["side"]),
                c["body"]]
    out += ["== secret scan =="] + (["%s %s" % (h["rule"], _prose(h["where"])) for h in payload["scan"]] or ["no matches"])
    return "\n".join(out + ["== not posted ==", "unverifiable: %d" % payload["unverifiable"],
                            "already posted: %d" % len(payload["skipped"])])


def build(run: str) -> dict:
    info = _target(run)
    doc = report.build(run)
    gh = _forge(info)
    head = info["head_sha"]
    if gh.state() != head:  # check (a): the diff below belongs to the pinned head; the refs are read here, then held
        raise Refuse("head-moved")
    refs = gh.refs
    anchors = _anchors(gh.diff())
    posted = gh.existing(gh.me())
    _live(gh, head, refs)  # check (b): nothing moved while the reads ran
    old = gh.prior_state
    skip = posted - old["fixed"]  # an id found fixed earlier that is back in this run is new again
    complete = (doc["mode"] == "parallel" and not any(doc.get(k) for k in ("rejected", "rejected_batches", "no_verdict", "lost"))
                and all(v == "complete" for n, v in doc["lanes"].items() if n.split("@")[0] in ("defects", "standards", "spec"))
                and not any(s.get("what") in ("defects", "standards") or (s.get("what") == "spec" and s.get("why") != "no spec")
                            for s in doc["skipped"]))  # else absence proves nothing; "no spec" is the usual case and blocks nothing
    d = prior.derive(old, {f["id"]: f["path"] for f in doc["findings"]}, set(doc["inspected"]["files"]), complete,
                     {i["id"] for i in doc["unverifiable"]}, _changed(run, old["last_head"], head), "nit" in doc)
    fresh = [f for f in doc["findings"] if f["id"] not in skip]
    if not fresh and not (d["fixed"] or d["outdated"]):  # a body-only status review is allowed when a prior state changed
        raise Refuse("already-posted" if doc["findings"] or old["fixed"] or old["outdated"] else "nothing-to-post")
    comments, entries, in_body, scan = [], [], [], []
    try:  # a file with a key marker in any finding: every finding in it is blanked, since a key body has no shape
        keyed = {f["path"] for f in fresh if any("private-key" in _redact(f[k])[1] for k in ("title", "trigger", "quote", "fix"))}
    except Exception:
        raise Refuse("scan-failed")
    pin = report._Pinned(run, [f["path"] for f in fresh], [f["path"] for f in fresh])  # the cited rows, read as ingest judged them
    for f in fresh:  # report order: axis, severity, path, line, id
        row = anchors.get((f["path"], f["line"]))
        inline = row is not None and len(comments) < MAX_INLINE
        rows = report._cited(pin, f["path"], f["line"], f["end_line"], f["quote"])
        try:  # each finding on its own, so every match is reported at the finding's path:line
            where = None if inline else "%s:%d" % (_prose(f["path"]), f["line"])
            text, rules = _render(f, rows, where, f["path"] in keyed, "new" if old["last_head"] or old["findings"] else None)
        except Exception:
            raise Refuse("scan-failed")
        scan += [{"rule": r, "where": "%s:%d" % (f["path"], f["line"])} for r in rules]
        if inline:
            comments.append({"id": f["id"], "path": f["path"], "line": f["line"], "side": "RIGHT", "old_path": row[2],
                             "old_line": row[1], "body": text})
        else:
            entries.append(text)
            in_body.append(f["id"])
    n_unverifiable = sum(1 for i in doc["unverifiable"] if i["category"] != "sota")
    payload = {"version": 1, "forge": info["forge"], "host": info["host"], "repo": info["repo"],
               "number": info["number"], "url": info["url"], "head_sha": head,
               "body": _body(doc["verdict"], fresh, entries, n_unverifiable, head, doc["inspected"]["areas"],
                             prior.review_mark(head, d["fixed_out"], d["outdated_out"]),
                             tuple(x for x in (prior.render(d), "**Not run:** " + ", ".join(doc["not_run"]) if doc["not_run"] else None,
                                               _basis(doc["basis"])) if x)),
               "comments": comments,
               "in_body": in_body, "unverifiable": n_unverifiable, "scan": scan,
               "skipped": [f["id"] for f in doc["findings"] if f["id"] in skip]}
    if refs:
        payload["diff_refs"] = refs
    texts = [payload["body"]] + [c["body"] for c in comments]
    if any(len(t) > MAX_BODY for t in texts):
        raise Refuse("too-large")
    _checked(texts)
    preview = _preview(payload)
    if len(preview) > target.MAX_PREVIEW:  # the user must see every byte they confirm
        raise Refuse("preview-too-large")
    out_dir = os.path.join(run, "post")
    os.makedirs(out_dir, exist_ok=True)
    os.chmod(out_dir, 0o700)
    path = os.path.join(out_dir, "payload.json")
    target.write_private(path, json.dumps(payload, sort_keys=True, indent=2))
    with open(path, "rb") as fh:
        digest = hashlib.sha256(fh.read()).hexdigest()
    print(preview)
    return {"status": "ok", "digest": digest, "inline": len(comments), "in_body": len(in_body),
            "skipped": len(payload["skipped"]), "unverifiable": n_unverifiable, "redacted": len(scan)}


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
    _checked([payload["body"]] + [c["body"] for c in payload["comments"]])  # the file may have been edited and re-hashed
    gh = _forge(info)
    _live(gh, info["head_sha"], payload.get("diff_refs"))  # check (c): before the first write
    posted = gh.existing(gh.me()) - gh.prior_state["fixed"]  # fresh: a pending review made since build would be stacked on or confused with ours
    if posted & ({c["id"] for c in payload["comments"]} | set(payload["in_body"])):
        raise Refuse("already-posted")  # a retry after a timeout, or a first submit that died after its event call
    last = gh.prior_state
    marks = [m for m in map(prior.parse_mark, reversed(payload["body"].replace("\r\n", "\n").split("\n"))) if m]  # the last marker line, wherever it sits
    if not payload["comments"] and not payload["in_body"] and \
            (marks[:1] or [None])[0] == (last["last_head"], last["fixed"], last["outdated"]):
        raise Refuse("already-posted")  # a body-only status review has no id to find: its marker is the record
    handle = gh.create(payload)
    try:
        gh.verify(payload, handle)
        _live(gh, info["head_sha"], payload.get("diff_refs"))  # check (d): before the event
        gh.recount(payload, handle)  # the last read: nothing joined the review since verify
        gh.finish(handle)
    except BaseException:  # any failure, an interrupt too, withdraws the review this call created
        gh.cleanup(handle)  # names what it could not remove
        raise
    out = {"status": "ok", "posted": len(payload["comments"]), "in_body": len(payload["in_body"]), "review_id": handle}
    try:  # the review is public now: a lost local record must not read as "nothing was posted"
        target.write_private(os.path.join(run, "post", "result.json"), json.dumps(out, sort_keys=True))
    except OSError:
        pass
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
    target.emit(out, STDOUT_KEYS)
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
