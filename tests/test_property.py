"""Seeded property test (6xl.77): nasty file names and line endings through the real CLI, every local kind.

Each seed builds a repository whose file names draw on space, TAB, LF, CR, quote, backslash, ESC, UTF-8,
a Tags character and ZWSP, and whose prose files hide a canary in a random construct at a random place with
LF, CRLF or CR line ends. For every target kind the test asserts that each changed path is either included
or excluded with a reason, and that no canary or raw hidden byte reaches any run-dir file name, file or stdout.
"""
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "skills", "review-code", "scripts")
import paths  # noqa: F401

import gitfix  # noqa: E402
import target  # noqa: E402

SEEDS = (11,)
# 6xl.85: a non-UTF-8 byte (as surrogateescape decodes it) joins the nasty characters
ALPHABET = ["a", "b", "c", " ", "\t", "\n", "\r", '"', "\\", "\x1b", "\u00fc", "\u200b", chr(0xE0041), "\udce9"]
CANARY = "PROPCANARY"
RAW = [b"\x1b", "\u200b".encode(), chr(0xE0041).encode(), CANARY.encode(), b"\\udb40", b"\\u200b", b"\\u001b",
       b"\\udc"]
PROSE_EXT = (".md", ".markdown", ".html")


def hidden(rng):
    """A hidden construct carrying the canary, as a list of lines (each renders as nothing on GitHub)."""
    return [["<!-- %s -->" % CANARY],
            ["<!--", "%s %d" % (CANARY, rng.randrange(99)), "more", "-->"],
            ["", "[//]: # (%s)" % CANARY, ""],
            ['<div hidden>%s</div>' % CANARY]][rng.randrange(4)]


def long_comment(n):
    """A comment opened on the first line that runs past `n` lines: a partial edit shows it without the opener."""
    return ["<!--"] + ["%s %d" % (CANARY, i) for i in range(n)] + ["-->"]


def prose(rng, n_lines):
    lines = ["* item %d" % i for i in range(n_lines)]
    at = rng.randrange(len(lines) + 1)
    lines[at:at] = hidden(rng)
    eol = rng.choice(["\n", "\r\n", "\r"])
    return eol.join(lines) + eol


def name(rng, used, ext):
    while True:
        n = "".join(rng.choice(ALPHABET) for _ in range(rng.randrange(2, 7))) + ext
        if rng.random() < 0.3:
            n = "d/" + n
        if n not in used and n.strip() and not n.startswith("-"):
            used.add(n)
            return n


class SeededNames(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = os.path.realpath(self._td.name)

    def write(self, repo, rel, text):
        p = os.path.join(repo, rel)
        os.makedirs(os.path.dirname(p) or repo, exist_ok=True)
        with open(p, "wb") as fh:
            fh.write(text.encode("utf-8", "surrogateescape"))

    def commit(self, repo, files, message):
        """gitfix.commit with literal pathspecs: a generated name may hold glob characters such as a backslash."""
        for rel, text in files.items():
            self.write(repo, rel, text)
        gitfix.run(["git", "--literal-pathspecs", "add", "--"] + sorted(files), repo)
        gitfix.run(["git", "commit", "-q", "-m", message], repo)

    def build(self, seed):
        rng = random.Random(seed)
        repo = gitfix.make_repo(os.path.join(self.tmp, "r%d" % seed))
        used, files = set(), {}
        partial = set()
        for i in range(8):
            ext = rng.choice(PROSE_EXT) if i % 2 == 0 else ".py"
            n = name(rng, used, ext)
            if i % 4 == 0:  # a long comment on line 1, edited deep inside later
                partial.add(n)
                files[n] = "\n".join(long_comment(30) + ["* tail"]) + "\n"
            else:
                files[n] = prose(rng, 30) if ext != ".py" else "v%d = %d\n" % (i, i)
        files["d/keep.py"] = "k = 1\n"
        self.commit(repo, files, "base")
        gitfix.run(["git", "checkout", "-q", "-b", "feat"], repo)
        names = sorted(files)
        changed = set()
        for n in rng.sample(names, 4):
            changed.add(n)
            if n in partial:
                files[n] = "\n".join(long_comment(30) + ["* tail"]).replace("%s 20" % CANARY, "%s 20 x" % CANARY) + "\n"
            else:
                files[n] = prose(rng, 31) if not n.endswith(".py") else files[n] + "w = 2\n"  # 31 lines: never the base
        added = name(rng, used, ".md")
        files[added] = prose(rng, 12)
        changed.add(added)
        self.commit(repo, {n: files[n] for n in changed}, "feat")
        old = rng.choice(sorted(set(names) - changed))
        new = name(rng, used, old[old.rindex("."):])  # keep the type: a prose file renamed to code is code
        gitfix.run(["git", "mv", "--", old, new], repo)
        gitfix.run(["git", "commit", "-q", "-m", "rename"], repo)
        committed = changed | {new}
        staged = name(rng, used, ".md")
        self.write(repo, staged, prose(rng, 10))
        gitfix.run(["git", "--literal-pathspecs", "add", "--", staged], repo)
        unstaged = rng.choice(sorted(committed - {new}))
        self.write(repo, unstaged, prose(rng, 25) if not unstaged.endswith(".py") else "u = 3\n")
        untracked = name(rng, used, ".md")
        self.write(repo, untracked, prose(rng, 8))
        tracked = sorted(set(files) - {old} | {new, staged})
        want = {("main",): committed, ("main..feat",): committed, ("HEAD^!",): {new},
                (): {staged, unstaged, untracked}, ("--staged",): {staged}, ("--unstaged",): {unstaged, untracked},
                ("--repo",): set(tracked) | {untracked}}
        under = {n for n in want[("--repo",)] if n.startswith("d/")}
        if under:  # the rename can move the last file out of d/
            want[("d",)] = under
        return repo, want

    def run_cli(self, repo, words):
        e = gitfix.env()
        e["TMPDIR"] = self.tmp
        p = subprocess.run([sys.executable, os.path.join(SCRIPTS, "target.py")] + list(words), cwd=repo, env=e,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        t = json.loads(p.stdout.decode("utf-8"))
        self.assertEqual(t["status"], "ok", (words, t))
        q = subprocess.run([sys.executable, os.path.join(SCRIPTS, "ledger.py"), "build", "--run", t["run"]], cwd=repo,
                           env=e, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(json.loads(q.stdout.decode("utf-8"))["status"], "ok", (words, q.stdout))
        return t["run"], p.stdout + q.stdout

    def test_every_kind_lists_every_path_and_leaks_nothing(self):
        for seed in SEEDS:
            repo, want = self.build(seed)
            for words, paths in want.items():
                with self.subTest(seed=seed, words=words):
                    run, out = self.run_cli(repo, words)
                    with open(os.path.join(run, "target.json"), encoding="utf-8") as fh:
                        tj = json.load(fh)
                    listed = set(tj["included"]) | {e["path"] for e in tj["excluded"] if e.get("reason")}
                    self.assertEqual({target.shown(n) for n in paths} - listed, set())
                    found = [b for b in RAW if b in out]
                    for d, dirs, fs in os.walk(run):
                        for n in dirs + fs:
                            found += [("name", n) for b in RAW[:3] if b in n.encode("utf-8", "surrogateescape")]
                        for f in fs:
                            with open(os.path.join(d, f), "rb") as fh:
                                data = fh.read()
                            found += [(os.path.relpath(os.path.join(d, f), run), b) for b in RAW if b in data]
                    self.assertEqual(found, [])


class ShuffledReport(unittest.TestCase):
    """The JSON document is a function of the finding set: line order and lane ingest order do not change a byte."""

    def test_json_is_byte_identical_for_shuffled_lines_and_lanes(self):
        import test_report as tr
        for seed in SEEDS:
            rng = random.Random(seed)
            by_lane = {"defects": [tr.mc(n, "bug", severity=rng.choice(("required", "suggestion"))) for n in (2, 4, 7)],
                       "standards": [tr.mc(n, c) for n, c in ((2, "lean"), (3, "lean"), (5, "smell"))]}
            outs = []
            for _ in range(4):
                case = tr.MultiLane("test_lane_without_an_inspected_line_is_incomplete")
                case.setUp()
                try:
                    for lane in rng.sample(sorted(by_lane), 2):
                        objs = rng.sample(by_lane[lane], len(by_lane[lane])) + [{"inspected": ["src/calc.py"]}]
                        case.lane(lane, *objs)
                    outs.append(case.rep("render", extra=["--format", "json"])[1])
                finally:
                    case.doCleanups()
            self.assertEqual(len(set(outs)), 1, seed)
            axes = [f["axis"] for f in json.loads(outs[0])["unverifiable"]]
            self.assertEqual(axes, sorted(axes, key=tr.report.AXES.index))


if __name__ == "__main__":
    unittest.main()
