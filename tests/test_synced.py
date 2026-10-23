"""The synced-checkout gate (gh#49): a head that is not checked out is reviewed only from the main checkout on the
live upstream default-branch tip, through the run-directory contract."""
import json
import os
import subprocess
import sys
import unittest
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import test_pinned as tp  # noqa: E402
import target  # noqa: E402
import test_target as tt  # noqa: E402


class Gate(tt.PRWorld):
    """The clone sits on main at the live tip of its bare 'GitHub'; PR #7 is not checked out."""

    def refused(self, reason, args=("7",), gh=None, cwd=None):
        before = tp.snapshot(self.repo)
        runs = self.run_dirs()
        rc, out = tt.call(list(args), cwd or self.repo, self.tmp, gh=gh or self.gh())
        self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", reason), out)
        self.assertEqual(tp.snapshot(self.repo), before)  # HEAD, refs, index and working tree untouched
        self.assertEqual(self.run_dirs(), runs)
        self.assertFalse(self.has_pr_objects(), "the PR was fetched before the gate refused")
        return out["detail"]

    def passes(self, args=("7",), gh=None):
        rc, out = tt.call(list(args), self.repo, self.tmp, gh=gh or self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        return out

    def test_on_another_branch(self):
        self.git("checkout", "-q", "-b", "other")
        detail = self.refused("checkout-not-synced")
        self.assertIn("other", detail)
        self.assertIn("0 behind and 0 ahead", detail)

    def test_behind_the_remote_tip(self):
        self.git("reset", "-q", "--hard", "HEAD~1")  # the tip object stays in the repository
        self.assertIn("main, 1 behind and 0 ahead", self.refused("checkout-not-synced"))

    def test_behind_a_tip_the_repository_lacks(self):
        src = os.path.join(self.tmp, "src")
        gitfix.commit(src, {"src/later.py": "z = 1\n"}, "remote moves on again")
        gitfix.run(["git", "push", "-q", self.bare, "main"], src)
        self.assertIn("main, behind by an unknown count (this repository lacks the tip)",
                      self.refused("checkout-not-synced"))  # the review never fetches to count

    def test_an_unborn_head_never_claims_the_tip_is_missing(self):
        self.git("checkout", "-q", "--orphan", "fresh")
        detail = self.refused("checkout-not-synced")
        self.assertIn("fresh, behind by an unknown count", detail)
        self.assertNotIn("lacks", detail)

    def test_ahead_of_the_remote_tip(self):
        gitfix.commit(self.repo, {"local.py": "l = 1\n"}, "local only")
        self.assertIn("main, 0 behind and 1 ahead", self.refused("checkout-not-synced"))

    def test_in_a_linked_worktree(self):
        self.git("checkout", "-q", "-b", "parking")
        wt = os.path.join(self.tmp, "wt")
        self.git("worktree", "add", "-q", wt, "main")
        detail = self.refused("checkout-not-synced", cwd=wt)
        self.assertIn("linked worktree", detail)
        self.assertIn("main, 0 behind and 0 ahead", detail)

    def test_a_main_checkout_whose_path_holds_a_line_separator_is_not_linked(self):  # review-code-21my
        for i, ch in enumerate("\r\x0b\x0c\x1c\x1d\x1e\x85\u2028\u2029"):
            moved = os.path.join(self.tmp, "re%d%spo" % (i, ch))
            os.rename(self.repo, moved)
            rc, out = tt.call(["7"], moved, self.tmp, gh=self.gh())
            self.assertEqual((rc, out["status"]), (0, "ok"), (ch, out))
            os.rename(moved, self.repo)

    def test_a_main_checkout_whose_path_holds_a_non_utf8_byte_is_not_linked(self):  # review-code-6i9x
        moved = os.path.join(os.fsencode(self.tmp), b"re\xffpo")
        os.rename(self.repo, moved)
        rc, out = tt.call(["7"], os.fsdecode(moved), self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        os.rename(moved, self.repo)

    def test_a_main_checkout_whose_path_holds_a_newline_is_not_linked(self):  # review-code-kzvu
        moved = os.path.join(self.tmp, "re\npo")
        os.rename(self.repo, moved)
        rc, out = tt.call(["7"], moved, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        os.rename(moved, self.repo)

    def test_a_linked_worktree_whose_path_holds_a_newline_is_still_linked(self):  # review-code-kzvu
        self.git("checkout", "-q", "-b", "parking")
        wt = os.path.join(self.tmp, "w\nt")
        self.git("worktree", "add", "-q", wt, "main")
        self.assertIn("linked worktree", self.refused("checkout-not-synced", cwd=wt))

    def test_a_remote_url_holding_a_unicode_line_separator_stays_one_remote(self):  # review-code-vz3k
        self.git("remote", "add", "decoy", "https://example.com/x remote.up.url https://github.com/z/q")
        self.refused("no-upstream-remote", gh=self.gh(url="https://github.com/z/q/pull/7"))

    def test_a_default_branch_whose_name_holds_a_next_line_char_is_read_whole(self):  # review-code-vz3k
        name = "ma\u0085in"
        gitfix.run(["git", "branch", name, "main"], self.bare)
        gitfix.run(["git", "symbolic-ref", "HEAD", "refs/heads/" + name], self.bare)
        self.git("checkout", "-q", "-b", name)
        self.passes()

    def test_a_checked_out_default_branch_ending_in_a_next_line_char_is_synced(self):  # review-code-fuxn
        name = "main\u0085"
        gitfix.run(["git", "branch", name, "main"], self.bare)
        gitfix.run(["git", "symbolic-ref", "HEAD", "refs/heads/" + name], self.bare)
        self.git("checkout", "-q", "-b", name)
        self.passes()

    def test_synced_checkout_with_uncommitted_changes_passes(self):
        tp.write(self.repo, "src/cfg.py", "def load():\n    return 9\n")
        tp.write(self.repo, "untracked.py", "u = 1\n")
        self.passes()

    def test_unreachable_remote(self):
        self.git("config", "url." + os.path.join(self.tmp, "nope.git") + ".insteadOf", self.URL)
        self.git("config", "--unset", "url." + self.bare + ".insteadOf")
        self.refused("remote-unreachable")

    def test_base_repository_with_no_local_remote(self):
        self.refused("no-upstream-remote", gh=self.gh(url="https://github.com/other/repo/pull/7"))

    def test_a_checked_out_pr_head_is_not_gated(self):
        self.git("fetch", "-q", "--refmap=", "origin", "+refs/pull/7/head:refs/pr7")
        self.git("checkout", "-q", "--detach", "refs/pr7")
        self.git("update-ref", "-d", "refs/pr7")
        self.passes()


class Fork(tt.PRWorld):
    """The PR's base repository o/r is a fork of u/r; the clone has both as remotes."""

    UP = "https://github.com/u/r.git"

    def setUp(self):
        super().setUp()
        self.up = os.path.join(self.tmp, "up.git")
        gitfix.run(["git", "init", "-q", "--bare", "-b", "main", self.up], self.tmp)
        gitfix.run(["git", "push", "-q", self.up, self.main + ":refs/heads/main"], self.repo)  # the fork is one ahead
        self.git("remote", "add", "upstream", self.UP)
        self.git("config", "url." + self.up + ".insteadOf", self.UP)
        self.fork_gh = mock.Mock(side_effect=lambda args, cwd: (
            {"isFork": True, "parent": {"name": "r", "owner": {"login": "u"}}} if args[0] == "repo" else self.payload()))

    def test_fork_with_extra_commits_containing_the_upstream_tip_passes(self):
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.fork_gh)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)

    def test_fork_lacking_the_upstream_tip_refuses(self):
        src = os.path.join(self.tmp, "src")
        gitfix.run(["git", "fetch", "-q", self.up, "main"], src)
        gitfix.run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], src)
        gitfix.commit(src, {"up.py": "u = 1\n"}, "upstream moves on")
        gitfix.run(["git", "push", "-q", self.up, "HEAD:refs/heads/main"], src)
        before = tp.snapshot(self.repo)
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.fork_gh)
        self.assertEqual((rc, out["reason"]), (1, "checkout-not-synced"), out)
        self.assertEqual(tp.snapshot(self.repo), before)

    def test_fork_whose_parent_has_no_local_remote_refuses(self):
        self.git("remote", "remove", "upstream")
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.fork_gh)
        self.assertEqual((rc, out["reason"]), (1, "no-upstream-remote"), out)


class RefKinds(tt.Base):
    """A clone of a local bare repository: origin/HEAD names the upstream, feat is checked out."""

    def setUp(self):
        super().setUp()
        bare = os.path.join(self.tmp, "bare.git")
        gitfix.run(["git", "init", "-q", "--bare", "-b", "main", bare], self.tmp)
        src = gitfix.make_repo(os.path.join(self.tmp, "src"))
        self.base = gitfix.commit(src, {"a.py": "a = 0\n"}, "base")
        gitfix.run(["git", "push", "-q", bare, "main"], src)
        self.repo = os.path.join(self.tmp, "clone")
        gitfix.run(["git", "clone", "-q", bare, self.repo], self.tmp)
        self.git("checkout", "-q", "-b", "feat")
        self.feat = gitfix.commit(self.repo, {"a.py": "a = 1\n", "b.py": "b = 1\n"}, "feat")

    def test_a_feature_branch_no_argument_run_and_staged_target_are_not_gated(self):
        self.git("remote", "set-url", "origin", os.path.join(self.tmp, "nope.git"))  # any network read would fail
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out["status"], out["kind"]), (0, "ok", "branch"), out)
        tp.write(self.repo, "a.py", "a = 2\n")
        self.git("add", "a.py")
        rc, out = tt.call(["--staged"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"], out["kind"]), (0, "ok", "staged"), out)

    def test_a_head_branch_ending_in_a_next_line_char_is_not_the_default_branch(self):  # review-code-fuxn
        self.git("checkout", "-q", "-b", "main\u0085")  # .strip() turned it into "main", the default
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out.get("kind")), (0, "branch"), out)

    def test_a_branch_head_not_checked_out_is_gated(self):
        self.git("checkout", "-q", "main")
        rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)  # main is at the live tip
        self.git("checkout", "-q", "feat")
        self.git("branch", "-q", "side", self.base)
        rc, out = tt.call(["side..main"], self.repo, self.tmp)  # head main is not HEAD, and HEAD is on feat
        self.assertEqual((rc, out["reason"]), (1, "checkout-not-synced"), out)
        self.assertIn("feat", out["detail"])

    def test_unreachable_origin_refuses_a_gated_head(self):
        self.git("checkout", "-q", "main")
        self.git("remote", "set-url", "origin", os.path.join(self.tmp, "nope.git"))
        rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["reason"]), (1, "remote-unreachable"), out)

    def test_origin_without_origin_head_refuses_a_gated_head(self):
        self.git("checkout", "-q", "main")
        self.git("remote", "set-head", "origin", "-d")  # gh#49: the upstream is the remote behind origin/HEAD
        rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["reason"]), (1, "no-upstream-remote"), out)

    def test_origin_head_without_an_origin_url_never_reads_a_path_named_origin(self):
        self.git("checkout", "-q", "main")
        self.git("remote", "remove", "origin")
        gitfix.run(["git", "clone", "-q", "--bare", self.repo, os.path.join(self.repo, "origin")], self.tmp)
        self.git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")  # a path could forge the tip
        rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["reason"]), (1, "no-upstream-remote"), out)

    def test_no_origin_refuses_a_gated_head(self):
        self.git("checkout", "-q", "main")
        self.git("remote", "remove", "origin")
        rc, out = tt.call(["main..feat"], self.repo, self.tmp)
        self.assertEqual((rc, out["reason"]), (1, "no-upstream-remote"), out)


class GateProcessCount(tt.PRWorld):
    def count(self, n):
        src = os.path.join(self.tmp, "src")
        gitfix.run(["git", "fetch", "-q", self.bare, "refs/pull/7/head"], src)
        gitfix.run(["git", "checkout", "-q", "--detach", "FETCH_HEAD"], src)
        gitfix.commit(src, {"g%02d_%d.py" % (i, n): "v = 1\n" for i in range(n)}, "pr %d" % n)
        gitfix.run(["git", "push", "-q", "--force", self.bare, "HEAD:refs/pull/7/head"], src)
        head = gitfix.run(["git", "rev-parse", "HEAD"], src).strip()
        log = os.path.join(self.tmp, "gate%d.json" % n)
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh(headRefOid=head))
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        return tp.starts(log)

    def test_the_gate_adds_one_ls_remote_and_no_per_file_process(self):
        three = self.count(3)
        self.setUp()  # a fresh world: the 30-file run inherits no objects of the 3-file run
        thirty = self.count(30)
        self.assertEqual(len(three), len(thirty))
        for argv in (three, thirty):
            self.assertEqual(sum(1 for a in argv if "ls-remote" in a), 1)


class GateRemoteIsOperand(tt.PRWorld):
    def test_the_gates_ls_remote_ends_options_before_the_remote_name(self):  # review-code-j2os
        log = os.path.join(self.tmp, "ls.json")
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        (argv,) = [a for a in tp.starts(log) if "ls-remote" in a]
        self.assertEqual(argv[argv.index("ls-remote"):], ["ls-remote", "--symref", "--", "origin", "HEAD"])


class NetStdin(tt.Base):
    """review-code-rio1: no caller passes stdin on the network path, so the private seam is the only route to it; the
    argument is still honoured there, never dropped."""

    def test_the_net_path_hands_its_stdin_to_git(self):
        repo = gitfix.make_repo(os.path.join(self.tmp, "r"))
        for net in (False, True):
            rc, out, _ = tt.target._run(["hash-object", "--stdin"], repo, stdin=b"abc\n", net=net)
            self.assertEqual((rc, out.strip()), (0, b"8baef1b4abc478178b004d62031cf7fe6db6f903"), net)

    def test_a_timed_out_net_call_closes_the_stdin_pipe_it_made(self):  # review-code-5m2d
        repo = gitfix.make_repo(os.path.join(self.tmp, "r"))
        tt.target._env(repo)  # the filter read runs first, unspied
        made = []

        class Spy(subprocess.Popen):
            def __init__(self, *a, **kw):
                super().__init__(*a, **kw)
                made.append(self)

            def communicate(self, *a, **kw):
                raise subprocess.TimeoutExpired("git", 1)

        with mock.patch.object(tt.target.subprocess, "Popen", Spy):
            rc, out, _ = tt.target._run(["hash-object", "--stdin"], repo, stdin=b"abc\n", net=True)
        self.assertEqual((rc, out), (-9, b""))
        self.assertEqual([p.stdin.closed for p in made], [True])


class FetchMaintenance(tt.PRWorld):
    def test_the_objects_fetch_starts_no_auto_maintenance_in_the_users_repository(self):
        self.git("config", "--unset", "maintenance.auto")  # the user's default: fetch would run maintenance --auto
        log = os.path.join(self.tmp, "fetch.json")
        with mock.patch.dict(os.environ, {"GIT_TRACE2_EVENT": log}):
            rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        with open(log, encoding="utf-8") as fh:  # child_start: a detached maintenance logs its own start too late
            kids = [e["argv"] for e in map(json.loads, fh) if e.get("event") == "child_start"]
        self.assertTrue(any("fetch" in a for a in tp.starts(log)))
        self.assertEqual([a for a in kids if "maintenance" in a or "gc" in a], [])


HANG_SSH = """#!%s
import json, os, subprocess, sys, time
with open(%r, "a") as fh:  # what a prompt would need: the caller's session (its terminal) and askpass
    fh.write(json.dumps({"sid": os.getsid(0), "askpass": os.environ.get("SSH_ASKPASS_REQUIRE")}) + "\\n")
kid = subprocess.Popen(["sleep", "1000"], start_new_session=%s)  # a grandchild holding git's stdout and stderr
with open(%r, "a") as fh:
    fh.write("%%d\\n" %% kid.pid)
time.sleep(1000)  # a blackholed host: the connection never completes
"""


class Blackhole(tt.PRWorld):
    """origin's host swallows packets: the fake ssh first on PATH never answers, and no real network is used."""

    ESCAPE = False  # the grandchild stays in the process group

    def setUp(self):
        super().setUp()
        bin_dir = os.path.join(self.tmp, "bin")
        os.mkdir(bin_dir)
        self.log = os.path.join(self.tmp, "ssh.log")
        ssh = os.path.join(bin_dir, "ssh")
        with open(ssh, "w", encoding="utf-8") as fh:
            fh.write(HANG_SSH % (sys.executable, self.log, self.ESCAPE, self.log + ".kids"))
        os.chmod(ssh, 0o755)
        env = dict((k, v) for k, v in os.environ.items() if k not in ("GIT_SSH", "GIT_SSH_COMMAND"))
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        timeout = mock.patch.object(tt.target, "NET_TIMEOUT", 1)
        timeout.start()
        self.addCleanup(timeout.stop)
        self.addCleanup(self.reap)

    def reap(self):
        if os.path.exists(self.log + ".kids"):
            with open(self.log + ".kids", encoding="utf-8") as fh:
                for pid in fh.read().split():
                    try:
                        os.kill(int(pid), 9)
                    except ProcessLookupError:
                        pass

    def blackhole(self):
        self.git("config", "--unset", "url." + self.bare + ".insteadOf")
        self.git("config", "url.ssh://blackhole.invalid/o/r.git.insteadOf", self.URL)

    def calls(self):
        with open(self.log, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh]

    def test_the_gate_refuses_remote_unreachable_without_a_prompt(self):
        self.blackhole()
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["reason"]), (1, "remote-unreachable"), out)
        self.assertEqual([(c["sid"] == os.getsid(0), c["askpass"]) for c in self.calls()], [(False, "never")])

    def test_the_objects_fetch_refuses_when_it_is_the_first_contact(self):
        self.git("fetch", "-q", "--refmap=", "origin", "+refs/pull/7/head:refs/pr7")
        self.git("checkout", "-q", "--detach", "refs/pr7")  # a checked-out head: no gate, so no ls-remote
        self.git("update-ref", "-d", "refs/pr7")
        self.blackhole()
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["reason"]), (1, "fetch-failed"), out)
        self.assertEqual([(c["sid"] == os.getsid(0), c["askpass"]) for c in self.calls()], [(False, "never")])


FAKE_GLAB = """#!%s
import json, sys
with open(%r, "a") as fh:
    fh.write(json.dumps(sys.argv[1:]) + "\\n")
print(json.dumps(%r if sys.argv[1] == "mr" else {"path_with_namespace": "g/r", "forked_from_project": None}))
"""


class SelfHostedMR(tt.PRWorld):
    """A merge request on a self-hosted GitLab with a web port; a fake glab first on PATH answers and records argv.
    Not covered: a live glab's own host matching, auth and the real `forked_from_project` shape."""

    URL = "https://gitlab.example:8443/g/r.git"
    MR = "https://gitlab.example:8443/g/r/-/merge_requests/5"

    def test_fork_detection_asks_the_merge_requests_own_host(self):
        gitfix.run(["git", "update-ref", "refs/merge-requests/5/head", self.pr], self.bare)
        bin_dir, log = os.path.join(self.tmp, "bin"), os.path.join(self.tmp, "glab.log")
        os.mkdir(bin_dir)
        mr = {"iid": 5, "state": "opened", "draft": False, "target_branch": "main", "web_url": self.MR,
              "diff_refs": {"base_sha": self.main, "head_sha": self.pr, "start_sha": self.main}}
        with open(os.path.join(bin_dir, "glab"), "w", encoding="utf-8") as fh:
            fh.write(FAKE_GLAB % (sys.executable, log, mr))
        os.chmod(os.path.join(bin_dir, "glab"), 0o755)
        with mock.patch.dict(os.environ, {"PATH": bin_dir + os.pathsep + os.environ.get("PATH", "")}):
            rc, out = tt.call([self.MR], self.repo, self.tmp, glab=tt.target.glab_json)
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        with open(log, encoding="utf-8") as fh:
            calls = [json.loads(line) for line in fh]
        self.assertEqual([c for c in calls if c[0] == "api"],
                         [["api", "--hostname=gitlab.example:8443", "projects/g%2Fr"]])


class Docs(unittest.TestCase):
    def test_skill_names_each_gate_reason_as_the_users_fix(self):
        with open(os.path.join(os.path.dirname(paths.SCRIPTS), "SKILL.md"), encoding="utf-8") as fh:
            flat = " ".join(fh.read().split())
        self.assertIn("User fixes: `checkout-not-synced`", flat)  # never the model: the review never syncs the checkout
        for reason in ("`no-upstream-remote`", "`remote-unreachable`"):
            self.assertIn(reason, flat)

    def test_untrusted_input_does_not_claim_filter_drivers_run(self):  # review-code-2rkq: test_pinned.Filters shows none runs
        with open(os.path.join(os.path.dirname(paths.SCRIPTS), "references", "untrusted-input.md"), encoding="utf-8") as fh:
            flat = " ".join(fh.read().split())
        self.assertNotIn("filter drivers (git-lfs) still run", flat)
        self.assertIn("no configured filter driver (git-lfs included) runs", flat)


class BlackholeEscaped(Blackhole):
    """The grandchild leaves the group (setsid, an ssh ControlPersist master): killing the group cannot close the
    pipes it holds."""

    ESCAPE = True


SERVING_SSH = """#!%s
import os, subprocess, sys
kid = subprocess.Popen(["sleep", "1000"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, start_new_session=True)
with open(%r, "a") as fh:  # an ssh ControlPersist master: it outlives the call and keeps only stderr open
    fh.write("%%d\\n" %% kid.pid)
os.execvp("sh", ["sh", "-c", sys.argv[-1].replace("git-upload-pack", "git upload-pack", 1)])  # serves the bare repository
"""


class StderrHeldOpen(tt.PRWorld):
    """review-code-j8uh: ssh answers at once but a grandchild keeps git's stderr open; no real network is used."""

    def setUp(self):
        super().setUp()
        bin_dir = os.path.join(self.tmp, "bin")
        os.mkdir(bin_dir)
        self.kids = os.path.join(self.tmp, "kids")
        ssh = os.path.join(bin_dir, "ssh")
        with open(ssh, "w", encoding="utf-8") as fh:
            fh.write(SERVING_SSH % (sys.executable, self.kids))
        os.chmod(ssh, 0o755)
        env = dict((k, v) for k, v in os.environ.items() if k not in ("GIT_SSH", "GIT_SSH_COMMAND"))
        env["PATH"] = bin_dir + os.pathsep + env.get("PATH", "")
        patch = mock.patch.dict(os.environ, env, clear=True)
        patch.start()
        self.addCleanup(patch.stop)
        timeout = mock.patch.object(tt.target, "NET_TIMEOUT", 15)  # far above a local ls-remote, far below the 300 s default
        timeout.start()
        self.addCleanup(timeout.stop)
        self.addCleanup(self.reap)
        self.git("config", "--unset", "url." + self.bare + ".insteadOf")
        self.git("config", "url.ssh://served.invalid" + self.bare + ".insteadOf", self.URL)

    def reap(self):
        if os.path.exists(self.kids):
            with open(self.kids, encoding="utf-8") as fh:
                for pid in fh.read().split():
                    try:
                        os.kill(int(pid), 9)
                    except ProcessLookupError:
                        pass

    def test_a_served_gate_and_fetch_pass_whoever_holds_stderr_open(self):
        rc, out = tt.call(["7"], self.repo, self.tmp, gh=self.gh())
        self.assertEqual((rc, out["status"]), (0, "ok"), out)
        self.assertTrue(self.has_pr_objects())
        with open(self.kids, encoding="utf-8") as fh:
            self.assertEqual(len(fh.read().split()), 2)  # the gate's ls-remote and the objects fetch, each served


class NonUtf8GitDir(tt.Base):
    """A repository whose separate git directory name holds byte 0xff: `rev-parse --git-path` returns it absolute."""

    def setUp(self):
        super().setUp()
        self.gd = os.path.join(self.tmp, os.fsdecode(b"gd\xff"))
        self.repo = os.path.join(self.tmp, "work")
        os.makedirs(self.repo)
        gitfix.run(["git", "init", "-q", "-b", "main", "--separate-git-dir", self.gd, self.repo], self.tmp)
        gitfix.commit(self.repo, {"a.py": "a = 0\n", "b.py": "b = 0\n"}, "base")
        tp.write(self.repo, "a.py", "a = 1\n")

    def test_the_run_directory_is_made_inside_the_real_git_dir(self):  # review-code-v9fi, target.new_run_dir
        rc, out = tt.call([], self.repo, self.tmp)
        self.assertEqual((rc, out.get("kind")), (0, "worktree"), out)
        self.assertEqual(len(os.listdir(os.path.join(self.gd, "review-code"))), 1)
        self.assertEqual([n for n in os.listdir(self.tmp) if "�" in n], [])
        tree = gitfix.run(["git", "ls-tree", "--name-only", tt.load(out["run"])["pinned_tree"]], self.repo)
        self.assertEqual(tree.split(), ["a.py", "b.py"])  # without the copied index b.py, unchanged, drops out

    def test_the_index_is_read_from_the_real_git_dir(self):  # review-code-v9fi, target.integrity
        self.assertIsNotNone(target.integrity(self.repo)["index"])


if __name__ == "__main__":
    unittest.main()
