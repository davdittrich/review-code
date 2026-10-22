"""ledger.py siblings: registry gaps for a new shipped file and co-change hints, from git and grep only (review-code-tvpq)."""
import itertools
import json
import os
import re
import types
import unittest
from unittest import mock

import paths  # noqa: F401

import gitfix  # noqa: E402
import ledger  # noqa: E402
import plan  # noqa: E402
import target  # noqa: E402
import test_ledger as tl  # noqa: E402
import test_lanes as lanes  # noqa: E402
import test_nit as tn  # noqa: E402
import test_plan as tp  # noqa: E402

OPENER = "Hints, not evidence. A finding needs a trigger citing the hit list. Severity suggestion at most."
MKT = ".claude-plugin/marketplace.json"


def mkt(*names):
    return '{\n  "plugins": [\n' + ",\n".join('    {"source": "./skills/%s"}' % n for n in names) + "\n  ]\n}\n"


SKILLS = {"skills/%s/SKILL.md" % n: "# %s\n" % n for n in "abc"}
BASE = dict(SKILLS, **{MKT: mkt("a", "b", "c")})


def gap_finding(hint):
    """The finding the Standards prompt asks for: anchored on the new file's first line, the hint verbatim as trigger."""
    return {"path": "skills/x/SKILL.md", "line": 1, "end_line": 1, "severity": "suggestion", "category": "smell",
            "title": "skills/x/SKILL.md is not registered where skills/a/SKILL.md is", "trigger": hint,
            "quote": "# x", "fix": "Add skills/x to %s" % MKT}


class Sib(tl.Base):
    def sib(self, base, feat, **kw):
        self.base(base)
        self.branch(feat)
        return self.build(**kw)

    def standards(self):
        return self.read("inputs/standards.md")


class Gaps(Sib):
    def test_a_new_skill_missing_from_the_registry_is_one_gap_with_its_line(self):
        out, led = self.sib(BASE, {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual([(e["file"], e["kind"]) for e in led["siblings"]], [(MKT, "gap")])
        how = led["siblings"][0]["how"]
        self.assertTrue(how.startswith("ADD skills/x/SKILL.md | sibling skills/a/SKILL.md | registered at %s:3 | " % MKT), how)
        self.assertTrue(how.endswith(" | missing: skills/x/SKILL.md"), how)
        self.assertIn(json.dumps('    {"source": "./skills/a"},'), how)
        self.assertEqual(out["counts"]["siblings"], 1)
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "tree", MKT)))  # the lane reads only tree/
        self.assertIn("- siblings: 1", self.read("inputs/defects.md"))
        sec = self.standards().split("## Siblings\n", 1)[1]
        self.assertIn(OPENER, sec)
        self.assertIn(how, sec)

    def test_a_registry_naming_one_of_three_siblings_is_dropped(self):
        _, led = self.sib(dict(SKILLS, **{MKT: mkt("a")}), {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual(led["siblings"], [])
        self.assertNotIn("## Siblings", self.standards())

    def test_one_sibling_is_enough_when_only_one_exists(self):
        _, led = self.sib({"skills/a/SKILL.md": "# a\n", MKT: mkt("a")}, {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual([e["file"] for e in led["siblings"]], [MKT])

    def test_a_registry_that_already_names_the_new_file_is_no_gap(self):
        _, led = self.sib(BASE, {"skills/x/SKILL.md": "# x\n", MKT: mkt("a", "b", "c", "x")})
        self.assertEqual(led["siblings"], [])

    def test_a_changed_registry_that_still_lacks_the_new_file_is_a_gap(self):
        _, led = self.sib(BASE, {"skills/x/SKILL.md": "# x\n", MKT: mkt("a", "b", "c") + "\n"})
        self.assertEqual([e["file"] for e in led["siblings"]], [MKT])

    def test_lockfile_and_changelog_hits_are_dropped(self):
        lock = "".join("skills/%s\n" % n for n in "abc")
        _, led = self.sib(dict(SKILLS, **{"package-lock.json": lock, "CHANGELOG.md": lock, "docs/release-notes.md": lock,
                                          "vendor/x/list.txt": lock, "dist/list.txt": lock}),
                          {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual(led["siblings"], [])

    def test_files_inside_a_sibling_directory_are_not_registries(self):
        _, led = self.sib(dict(SKILLS, **{"skills/a/notes.md": "skills/a skills/b\n"}), {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual(led["siblings"], [])

    def test_same_directory_same_extension_when_no_directory_siblings_exist(self):
        base = {"cmds/a.py": "x = 1\n", "cmds/b.py": "x = 2\n", "cmds/README": "", "cli.py": "cmds/a.py\ncmds/b.py\n"}
        _, led = self.sib(base, {"cmds/new.py": "x = 3\n"})
        self.assertEqual([e["file"] for e in led["siblings"]], ["cli.py"])
        self.assertIn("sibling cmds/a.py | registered at cli.py:1 |", led["siblings"][0]["how"])

    def test_test_and_doc_new_files_produce_nothing(self):
        reg = "".join("%s\n" % p for p in ("tests/test_a.py", "tests/test_b.py", "docs/a.md", "docs/b.md"))
        base = {"tests/test_a.py": "", "tests/test_b.py": "", "docs/a.md": "# a\n", "docs/b.md": "# b\n", "all.txt": reg}
        _, led = self.sib(base, {"tests/test_new.py": "", "docs/new.md": "# n\n"})
        self.assertEqual(led["siblings"], [])

    def test_an_existing_file_that_changes_is_not_new(self):
        _, led = self.sib(BASE, {"skills/a/SKILL.md": "# a2\n"})
        self.assertEqual(led["siblings"], [])

    def test_at_most_five_new_files_are_probed(self):
        _, led = self.sib(BASE, {"skills/n%d/SKILL.md" % i: "# n\n" for i in range(6)})
        self.assertEqual(len(led["siblings"]), 5)
        self.assertTrue(led["capped"]["siblings"])

    def test_at_most_eight_gaps_per_new_file(self):
        regs = {"reg%d.txt" % i: "skills/a skills/b\n" for i in range(10)}
        _, led = self.sib(dict(SKILLS, **regs), {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual(len(led["siblings"]), 8)
        self.assertTrue(led["capped"]["siblings"])

    def test_a_token_with_more_than_twelve_gaps_is_too_common(self):
        regs = {"reg%02d.txt" % i: "skills/a skills/b skills/c\n" for i in range(13)}
        _, led = self.sib(dict(SKILLS, **regs), {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual(led["siblings"], [])

    def test_the_wall_cap_stops_probing_and_records_it(self):
        tick = itertools.count(0, 100)
        with mock.patch.object(ledger, "time", types.SimpleNamespace(monotonic=lambda: next(tick))):
            _, led = self.sib(BASE, {"skills/x/SKILL.md": "# x\n"})
        self.assertEqual(led["siblings"], [])
        self.assertTrue(led["capped"]["siblings"])

    def test_a_hidden_character_in_the_new_name_is_escaped(self):
        name = "skills/x\u200b/SKILL.md"
        _, led = self.sib(BASE, {name: "# x\n"})
        self.assertEqual(len(led["siblings"]), 1)
        sec = self.standards()
        self.assertNotIn("\u200b", sec)
        self.assertNotIn("\u200b", json.dumps(led, ensure_ascii=False))
        self.assertIn(target.shown(name), led["siblings"][0]["how"])

    def test_registry_text_is_clipped_and_escaped(self):
        long = "skills/a skills/b " + "y" * 300 + "\u202e\n"
        _, led = self.sib(dict(SKILLS, **{"reg.txt": long}), {"skills/x/SKILL.md": "# x\n"})
        how = led["siblings"][0]["how"]
        self.assertNotIn("\u202e", how)
        self.assertNotIn("y" * 121, how)

    def test_no_section_and_no_entries_without_a_new_file(self):
        _, led = self.sib(BASE, {"skills/a/SKILL.md": "# a2\n"})
        self.assertEqual(led["siblings"], [])
        self.assertNotIn("Siblings", self.standards())
        self.assertIn("- siblings: 0", self.read("inputs/defects.md"))

    def test_section_is_at_most_twenty_lines(self):
        regs = {"reg%d.txt" % i: "skills/a skills/b\n" for i in range(8)}
        feat = {"skills/n%d/SKILL.md" % i: "# n\n" for i in range(5)}
        self.sib(dict(SKILLS, **regs), feat)
        sec = self.standards().split("## Siblings\n", 1)[1].strip("\n").split("\n")
        self.assertLessEqual(len(sec), 20)
        self.assertEqual(sec[-1].split(" | ")[0][:4], "ADD ")

    def test_a_staged_target_probes_the_index(self):
        self.base(BASE)
        self.g("checkout", "-q", "-b", "feat")
        gitfix.commit(self.repo, {"skills/x/SKILL.md": "# x\n"}, "stage")
        self.g("reset", "-q", "--soft", "HEAD~1")
        head = self.g("rev-parse", "HEAD")
        _, led = self.build(kind="staged", included=["skills/x/SKILL.md"], head=head, mb=head)
        self.assertEqual([e["file"] for e in led["siblings"]], [MKT])


def history(repo, n_with, n_without, extra=None):
    """Commit c.py n_with times together with o.py and n_without times alone (plus `extra` files each time)."""
    for i in range(n_with + n_without):
        files = {"c.py": "v = %d\n" % i}
        if i < n_with:
            files["o.py"] = "w = %d\n" % i
        files.update((extra or {}).get(i, {}))
        gitfix.commit(repo, files, "h%d" % i)


class CoChange(Sib):
    def setUp(self):
        super().setUp()
        p = mock.patch.object(ledger, "SIB_DAYS", 3650)  # fixture commits carry a fixed 2026 date
        p.start()
        self.addCleanup(p.stop)

    def edit(self, **kw):
        self.g("checkout", "-q", "-b", "feat")
        gitfix.commit(self.repo, {"c.py": "v = 'edit'\n"}, "edit")
        return self.build(**kw)

    def test_two_prior_commits_give_no_hint(self):
        history(self.repo, 2, 0)
        _, led = self.edit()
        self.assertEqual(led["siblings"], [])

    def test_three_of_six_is_below_the_ratio(self):
        history(self.repo, 3, 3)
        _, led = self.edit()
        self.assertEqual(led["siblings"], [])

    def test_three_of_five_is_a_hint_with_its_count(self):
        history(self.repo, 3, 2)
        _, led = self.edit()
        self.assertEqual([(e["file"], e["kind"], e["how"]) for e in led["siblings"]],
                         [("o.py", "cochange", "c.py usually changes with o.py (3 of 5 commits)")])
        self.assertIn(OPENER + "\n" + "c.py usually changes with o.py (3 of 5 commits)", self.standards())
        self.assertTrue(os.path.exists(os.path.join(self.run_dir, "tree", "o.py")))

    def test_a_commit_touching_more_than_thirty_files_is_skipped(self):
        big = {i: {"fill/f%d_%d.txt" % (i, j): "x\n" for j in range(30)} for i in range(3, 6)}  # c + 30 fillers = 31 files
        history(self.repo, 3, 3, big)
        _, led = self.edit()
        self.assertEqual([e["how"] for e in led["siblings"]], ["c.py usually changes with o.py (3 of 3 commits)"])

    def test_a_partner_removed_at_head_or_changed_in_the_change_is_not_a_hint(self):
        history(self.repo, 3, 0)
        self.g("checkout", "-q", "-b", "feat")
        gitfix.run(["git", "rm", "-q", "o.py"], self.repo)
        gitfix.commit(self.repo, {"c.py": "v = 'edit'\n"}, "edit")
        _, led = self.build(included=["c.py"])
        self.assertEqual(led["siblings"], [])

    def test_a_partner_in_the_change_is_not_a_hint(self):
        history(self.repo, 3, 0)
        self.g("checkout", "-q", "-b", "feat")
        gitfix.commit(self.repo, {"c.py": "v = 'edit'\n", "o.py": "w = 'edit'\n"}, "edit")
        _, led = self.build()
        self.assertEqual(led["siblings"], [])

    def test_five_per_changed_file_and_twelve_in_all(self):
        for i in range(3):
            gitfix.commit(self.repo, dict({"c%d.py" % k: "v = %d\n" % i for k in range(3)},
                                          **{"p%d.py" % k: "w = %d\n" % i for k in range(8)}), "h%d" % i)
        self.g("checkout", "-q", "-b", "feat")
        gitfix.commit(self.repo, {"c%d.py" % k: "e\n" for k in range(3)}, "edit")
        _, led = self.build()
        got = [e["how"] for e in led["siblings"] if e["kind"] == "cochange"]
        self.assertEqual(len(got), 12)  # 3 changed files x 5 partners would be 15
        for k in range(3):
            self.assertLessEqual(sum(1 for h in got if h.startswith("c%d.py " % k)), 5)

    def test_git_failure_is_never_fatal(self):
        history(self.repo, 3, 0)
        real = target.git

        def boom(args, cwd, *a, **kw):
            if args and args[0] == "log":
                return ""
            return real(args, cwd, *a, **kw)
        with mock.patch.object(target, "git", boom):
            _, led = self.edit()
        self.assertEqual(led["siblings"], [])


class Rerun(Sib):
    def test_the_plan_inputs_rewrite_keeps_the_siblings_section_once(self):
        self.sib(BASE, {"skills/x/SKILL.md": "# x\n"})
        for _ in range(2):
            plan._install_packs(self.run_dir, [])
        text = self.standards()
        self.assertEqual(text.count("## Siblings"), 1)
        self.assertIn("ADD skills/x/SKILL.md", text)


class Ingest(tp.Fixture):
    base_files = dict(BASE, **{"CLAUDE.md": "# Rules\n- Function names use snake_case.\n"})
    feat_files = {"skills/x/SKILL.md": "# x\n"}

    def test_a_gap_finding_shaped_like_the_prompt_ingests(self):
        out = self.ingest_lane("standards", gap_finding(self.hint()), {"inspected": ["skills/x/SKILL.md", MKT]})
        self.assertEqual((out["status"], out["dropped"]["invalid"], out["rejected"]), ("ok", 0, []), out)
        self.assertEqual([d["disposition"] for d in out["dispositions"]], ["accepted", "accepted"], out)

    def hint(self):
        led = json.load(open(os.path.join(self.run_dir, "ledger.json"), encoding="utf-8"))
        return next(e["how"] for e in led["siblings"] if e["kind"] == "gap")

    def test_the_prompt_text_cannot_turn_a_cochange_line_into_a_finding(self):
        _, body = lanes.parts("standards")
        text = "\n".join(body)
        sec = text.split("## Siblings", 1)[1]
        self.assertIn("never yields a finding alone", sec)
        self.assertIn("`ADD` line", sec)
        self.assertIn("first line", sec)
        self.assertIn("Never anchor on the registry line", sec)
        self.assertLessEqual(len([l for l in sec.split("\n\n", 1)[0].split("\n") if l.strip()]), 7)


class EndToEnd(tn.NitBase):
    """Ingest on the standards lane, confirm, render: the gap finding must survive report.py's origin rule."""
    base_files = dict(BASE, **{"CLAUDE.md": "# Rules\n- Function names use snake_case.\n"})

    @classmethod
    def setUpClass(cls):
        cls.feat_files = {"skills/x/SKILL.md": "# x\n"}

    def test_a_confirmed_registry_gap_is_in_the_json_report_as_introduced(self):
        led = json.load(open(os.path.join(self.run_dir, "ledger.json"), encoding="utf-8"))
        hint = next(e["how"] for e in led["siblings"] if e["kind"] == "gap")
        out = self.lane(gap_finding(hint), {"inspected": ["skills/x/SKILL.md", MKT]})
        self.assertEqual(len(out["ids"]), 1, out)
        self.confirm(out, [{"path": "skills/x/SKILL.md", "line": 1, "quote": "# x"}])
        d = self.doc()
        (f,) = d["findings"]
        self.assertEqual((f["origin"], f["path"], f["line"], f["category"], f["severity"]),
                         ("introduced", "skills/x/SKILL.md", 1, "smell", "suggestion"))
        self.assertEqual(d["dropped"]["do_not_flag"], 0)


if __name__ == "__main__":
    unittest.main()
