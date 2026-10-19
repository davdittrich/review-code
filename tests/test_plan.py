"""plan.py and the multi-lane report.py on a real fixture: target.py -> ledger.py -> plan.py -> report.py."""
import ast
import json
import os
import re
import stat
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import test_packs as tp  # noqa: E402
import test_report as tr  # noqa: E402  (module import: its test classes are not collected twice)
from test_report import CAND, CITE, cand, cli, fid, verdict  # noqa: E402

SCRIPTS = tr.SCRIPTS
RULE = "Use snake_case names"
ENV_VARS = ("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "CLAUDE_CODE_EFFORT_LEVEL", "CLAUDE_CODE_SUBAGENT_MODEL")
CONV = {"rule": RULE, "rule_source": "conventions/CLAUDE.md"}


def std(category="convention", **kw):
    o = {"path": "src/calc.py", "line": 5, "severity": "required", "category": category, "title": "name style",
         "trigger": "the function name breaks the rule", "quote": "return a - b"}
    if category == "convention":
        o.update(CONV)
    o.update(kw)
    return o


class Fixture(tr.Base):
    base_files = {"src/calc.py": tr.BASE["src/calc.py"], "CLAUDE.md": "# Rules\n- %s\n" % RULE}
    feat_files = {"src/calc.py": tr.FEAT}

    def plan(self, *args, stdin="", env=None):
        with mock.patch.dict(os.environ):  # host forcing variables must not leak into a plan
            for k in ENV_VARS:
                os.environ.pop(k, None)
            os.environ.update(env or {})
            rc, out, err = cli("plan.py", list(args), self.repo, self.tmp, stdin)
        self.assertEqual(err, "")
        return rc, json.loads(out)

    def plan_json(self):
        with open(os.path.join(self.run_dir, "plan.json"), encoding="utf-8") as fh:
            return json.load(fh)

    def ingest_lane(self, lane, *objs):
        rc, out = self.rep("ingest", tr.lines(*objs), extra=["--lane", lane])
        self.assertEqual(rc, 0, out)
        return json.loads(out)

    def handoff(self, i):
        with open(os.path.join(self.run_dir, "handoff", i + ".md"), encoding="utf-8") as fh:
            return fh.read()


class Overrides(Fixture):
    def run_plan(self, cfg, *args):
        rc, out = self.plan("plan", "--run", self.run_dir, *args, stdin="".join(k + "=" + v + "\n" for k, v in cfg))
        return rc, out

    def test_finder_override_reaches_every_finder_call(self):
        rc, out = self.run_plan([("finder_model", "sonnet"), ("finder_effort", "high")])
        self.assertEqual(rc, 0)
        p = self.plan_json()
        self.assertEqual({(c["model"], c["effort"]) for c in p["calls"]}, {("sonnet", "high")})
        self.assertEqual((p["verifier"]["model"], p["verifier"]["effort"]), ("sonnet", "high"))
        self.assertEqual(p["effective_overrides"], {"finder_effort": "high", "finder_model": "sonnet"})

    def test_verifier_override_reaches_both_verifiers(self):
        self.run_plan([("verifier_model", "opus"), ("verifier_effort", "low")])
        p = self.plan_json()
        for v in (p["verifier"], p["spec_verifier"]):
            self.assertEqual((v["model"], v["effort"]), ("opus", "low"))
        self.assertEqual({c["model"] for c in p["calls"]}, {"opus"})  # finders untouched

    def test_unset_references_are_ignored(self):
        rc, out = self.run_plan([("verifier_model", "${user_config.verifier_model}"), ("max_shards", ""),
                                 ("default_tier", "${user_config.default_tier}")])
        p = self.plan_json()
        self.assertEqual((rc, out["tier"], p["effective_overrides"], p["caps"]["max_shards"]), (0, "standard", {}, 6))

    def test_flag_beats_default_tier(self):
        self.assertEqual(self.run_plan([("default_tier", "deep")])[1]["tier"], "deep")
        rc, out = self.run_plan([("default_tier", "deep")], "--tier", "quick")
        self.assertEqual(out["tier"], "quick")
        self.assertNotIn("default_tier", self.plan_json()["effective_overrides"])

    def test_caps(self):
        self.run_plan([("max_verifier_calls", "5"), ("max_packs", "9")])
        caps = self.plan_json()["caps"]
        self.assertEqual((caps["max_verifier_calls"], caps["max_packs"], caps["max_shards"]), (5, 9, 6))

    def test_bad_config_refuses_and_never_echoes(self):
        bad = [("bogus", "1"), ("finder_effort", "turbo"), ("finder_model", "a b"), ("finder_model", "$(id)"),
               ("verifier_model", "-x"), ("max_packs", "10"), ("max_shards", "0"), ("max_verifier_calls", "2.5"),
               ("max_verifier_calls", "201"), ("default_tier", "huge"), ("max_packs", "\u00b2")]
        for kv in bad:
            rc, out = self.run_plan([kv])
            self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "bad-config"), kv)
            self.assertNotIn(kv[1], json.dumps(out), kv)
            if kv[0] != "bogus":
                self.assertIn(kv[0], out["detail"])
        rc, out = self.plan("plan", "--run", self.run_dir, stdin="no equals sign\n")
        self.assertEqual((rc, out["reason"]), (1, "bad-config"))

    def test_oversized_stdin_refuses(self):
        rc, out = self.plan("plan", "--run", self.run_dir, stdin="finder_model=x\n" * 5000)
        self.assertEqual((rc, out["reason"]), (1, "bad-config"))

    def test_plugin_json_keys(self):
        with open(os.path.join(HERE, "..", ".claude-plugin", "plugin.json"), encoding="utf-8") as fh:
            cfg = json.load(fh)["userConfig"]
        self.assertEqual(set(cfg["default_tier"]), {"type", "title", "description", "default", "options"})
        self.assertEqual((cfg["default_tier"]["default"], cfg["default_tier"]["options"]),
                         ("standard", ["quick", "standard", "deep"]))
        for k in ("finder_effort", "verifier_effort"):
            self.assertEqual(cfg[k]["type"], "string")
            for lvl in ("low", "medium", "high", "xhigh", "max"):
                self.assertIn(lvl, cfg[k]["description"])
        import plan as mod
        for k, (lo, hi) in mod.CAP_BOUNDS.items():
            self.assertEqual((cfg[k]["type"], cfg[k]["min"], cfg[k]["max"]), ("number", lo, hi))
        self.assertEqual(set(cfg), mod.CONFIG_KEYS)


DIFFERENT_TIER = "verifier not on a different tier: finders and verifier both run on "
UNCHECKED = "verifier tier not checked: finders run on %s, the verifier on %s; only distinct aliases are compared"


class Tiers(Fixture):
    def lanes(self, *args, **kw):
        rc, out = self.plan("plan", "--run", self.run_dir, *args, **kw)
        self.assertEqual(rc, 0, out)
        return out, self.plan_json()

    def test_standards_lane_in_every_tier_and_serial(self):
        for args in (["--tier", "quick"], ["--tier", "standard"], ["--tier", "deep"], ["--serial"]):
            _, p = self.lanes(*args)
            self.assertIn("standards", [c["lane"] for c in p["calls"]], args)

    def test_tier_table(self):
        for tier, fin, ver, cap in (("quick", ("sonnet", "low"), ("opus", "medium"), 10),
                                    ("standard", ("opus", "medium"), ("sonnet", "high"), 25),
                                    ("deep", ("opus", "xhigh"), ("sonnet", "high"), 60)):
            _, p = self.lanes("--tier", tier)
            self.assertEqual({(c["model"], c["effort"]) for c in p["calls"]}, {fin}, tier)
            for v in (p["verifier"], p["spec_verifier"]):
                self.assertEqual((v["model"], v["effort"]), ver, tier)
            self.assertEqual((p["caps"]["max_verifier_calls"], p["lost_guarantees"]), (cap, []), tier)

    def test_equal_models_lose_the_guarantee(self):
        out, p = self.lanes(stdin="verifier_model=opus\n")
        self.assertEqual(p["lost_guarantees"], [DIFFERENT_TIER + "opus"])
        self.assertEqual(out["lost_guarantees"], p["lost_guarantees"])
        _, p = self.lanes("--tier", "quick", stdin="verifier_model=sonnet\n")
        self.assertEqual(p["lost_guarantees"], [DIFFERENT_TIER + "sonnet"])
        _, p = self.lanes(stdin="verifier_model=haiku\nfinder_model=haiku\n")  # case-insensitive equality below
        self.assertEqual(len(p["lost_guarantees"]), 1)
        _, p = self.lanes(stdin="verifier_model=OPUS\n")
        self.assertEqual(len(p["lost_guarantees"]), 1)
        _, p = self.lanes(stdin="verifier_model=haiku\n")
        self.assertEqual(p["lost_guarantees"], [])

    def test_models_that_cannot_be_compared_lose_the_guarantee(self):  # CR-06
        for cfg, fm, vm in (("verifier_model=claude-opus-4-5\n", "opus", "claude-opus-4-5"),
                            ("finder_model=inherit\nverifier_model=sonnet\n", "inherit", "sonnet"),
                            ("finder_model=claude-sonnet-4-5\n", "claude-sonnet-4-5", "sonnet")):
            _, p = self.lanes(stdin=cfg)
            self.assertEqual(p["lost_guarantees"], [UNCHECKED % (fm, vm)], cfg)
        _, p = self.lanes(stdin="finder_model=claude-opus-4-5\nverifier_model=claude-opus-4-5\n")
        self.assertEqual(p["lost_guarantees"], [DIFFERENT_TIER + "claude-opus-4-5"])
        _, p = self.lanes(stdin="finder_model=fable\n")
        self.assertEqual(p["lost_guarantees"], [])

    def test_environment_forcing_is_recorded_as_heuristic(self):
        for v in ("1", "true", "YES"):
            _, p = self.lanes(env={"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": v})
            self.assertEqual(len(p["lost_guarantees"]), 1, v)
            self.assertIn("per-lane models ignored: CLAUDE_CODE_SUBAGENT_MODEL_FORCE is set", p["lost_guarantees"][0])
            self.assertIn("heuristic", p["lost_guarantees"][0])
        _, p = self.lanes(env={"CLAUDE_CODE_EFFORT_LEVEL": "high"})
        self.assertIn("per-lane effort ignored: CLAUDE_CODE_EFFORT_LEVEL is set", p["lost_guarantees"][0])
        self.assertIn("heuristic", p["lost_guarantees"][0])
        for env in ({"CLAUDE_CODE_SUBAGENT_MODEL": "haiku"}, {"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "0"},
                    {"CLAUDE_CODE_EFFORT_LEVEL": ""}):
            self.assertEqual(self.lanes(env=env)[1]["lost_guarantees"], [], env)

    def test_sota_flags(self):
        out, p = self.lanes("--sota")
        self.assertEqual(out["skipped"][-1], {"what": "sota", "why": "no dependency or import names in the change"})
        _, p = self.lanes("--tier", "deep", "--no-sota")
        self.assertEqual(p["skipped"], [{"what": "spec", "why": "no spec"}])
        self.assertNotIn("sota", [c["lane"] for c in p["calls"]])
        self.assertEqual(self.plan("plan", "--run", self.run_dir, "--sota", "--no-sota")[1]["reason"], "usage")
        self.assertEqual(self.plan("plan", "--run", self.run_dir, "--sota", "--sota")[1]["reason"], "usage")

    def test_ai_flag(self):
        self.assertIs(self.lanes()[1]["ai"], False)
        self.assertIs(self.lanes("--ai")[1]["ai"], True)


class Serial(Fixture):
    def test_serial_plan(self):
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "deep", "--serial", "--sota")
        self.assertEqual(rc, 0)
        p = self.plan_json()
        self.assertEqual(p["mode"], "serial")
        self.assertEqual([(c["lane"], c["agent"]) for c in p["calls"]],
                         [("defects", "main-thread"), ("standards", "main-thread")])
        self.assertEqual(p["calls"][0]["input"], os.path.join(self.run_dir, "inputs", "defects.md"))
        self.assertEqual(p["skipped"], [{"what": "spec", "why": "serial: the main thread never opens inputs/spec.md"},
                                        {"what": "sota", "why": "serial: no isolated web lane"}])
        self.assertEqual([g.split(":")[0] for g in p["lost_guarantees"]],
                         ["no independent verifier", "no lane isolation", "Spec axis skipped", "SOTA skipped"])
        self.assertEqual(out["lost_guarantees"], p["lost_guarantees"])
        self.assertEqual(out["mode"], "serial")

    def test_serial_ignores_environment_and_models(self):
        _, out = self.plan("plan", "--run", self.run_dir, "--serial", stdin="verifier_model=opus\n",
                           env={"CLAUDE_CODE_SUBAGENT_MODEL_FORCE": "1"})
        self.assertEqual(len(out["lost_guarantees"]), 4)


class Tracer(Fixture):
    KEYS = {"version", "tier", "mode", "calls", "verifier", "spec_verifier", "caps", "max_turns", "packs", "shards",
            "skipped", "lost_guarantees", "sota_inventory", "preview", "confirm", "effective_overrides", "ai"}

    def test_standard_tier_without_spec(self):
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "standard")
        self.assertEqual((rc, out["status"], out["tier"], out["calls"]), (0, "ok", "standard", 2))
        self.assertEqual(out["skipped"], [{"what": "spec", "why": "no spec"}])
        p = self.plan_json()
        self.assertEqual(set(p), self.KEYS)
        self.assertEqual([(c["lane"], c["axis"], c["agent"], c["model"], c["effort"], c["shard"]) for c in p["calls"]],
                         [("defects", "defects", "review-code:finder", "opus", "medium", None),
                          ("standards", "standards", "review-code:standards", "opus", "medium", None)])
        self.assertEqual(p["calls"][0]["input"], os.path.join(self.run_dir, "inputs", "defects.md"))
        self.assertEqual(p["calls"][1]["prompt"], "Standards lane. Input: " + p["calls"][1]["input"])
        self.assertEqual((p["verifier"]["model"], p["verifier"]["effort"]), ("sonnet", "high"))
        self.assertEqual(p["caps"]["max_verifier_calls"], 25)
        self.assertEqual((p["mode"], p["confirm"], [x["name"] for x in p["packs"]], p["shards"]), ("parallel", False, ["python"], []))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.run_dir, "plan.json")).st_mode), 0o600)

    def test_default_tier_is_standard(self):
        rc, out = self.plan("plan", "--run", self.run_dir)
        self.assertEqual((rc, out["tier"]), (0, "standard"))

    def test_deep_tier_with_spec_skips_sota(self):
        os.makedirs(os.path.join(self.repo, "docs"), exist_ok=True)
        with open(os.path.join(self.repo, "docs", "spec.md"), "w") as fh:
            fh.write("# Spec\nadd must add.\n")
        rc, out, _ = cli("ledger.py", ["build", "--run", self.run_dir, "--spec", "docs/spec.md"], self.repo, self.tmp)
        self.assertEqual(json.loads(out)["spec_status"], "found")
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "deep")
        self.assertEqual((rc, out["calls"]), (0, 3))
        self.assertEqual(out["skipped"], [{"what": "sota", "why": "no dependency or import names in the change"}])
        self.assertEqual([c["lane"] for c in self.plan_json()["calls"]], ["defects", "standards", "spec"])

    def test_usage_refusals(self):
        for args in ([], ["plan"], ["plan", "--run", self.run_dir, "--tier", "huge"],
                     ["plan", "--run", self.run_dir, "extra"], ["plan", "--run", self.run_dir, "--tier"],
                     ["bogus", "--run", self.run_dir]):
            rc, out = self.plan(*args)
            self.assertEqual((rc, out["status"], out["reason"]), (1, "refuse", "usage"), args)

    def test_missing_ledger_refuses(self):
        os.remove(os.path.join(self.run_dir, "ledger.json"))
        rc, out = self.plan("plan", "--run", self.run_dir)
        self.assertEqual((rc, out["reason"]), (1, "no-ledger"))

    def test_two_lanes_keep_axes_apart(self):
        d = self.ingest_lane("defects", CAND, {"inspected": ["src/calc.py"]})
        s = self.ingest_lane("standards", std(), {"inspected": ["src/calc.py"]})
        self.assertEqual((len(d["ids"]), len(s["ids"])), (1, 2))  # additive: the defects candidate survives
        self.verdicts(*[{"id": i, "verdict": "confirmed", "evidence": [CITE], "reason": "r"} for i in s["ids"]])
        fs = self.doc()["findings"]
        self.assertEqual([(f["axis"], f["source_lane"]) for f in fs], [("defects", "defects"), ("standards", "standards")])

    def test_ids_defects_unchanged_standards_salted(self):
        d = self.ingest_lane("defects", CAND)["ids"][0]
        self.assertEqual(d, fid("src/calc.py", "bug", "return a - b"))
        (s,) = [i for i in self.ingest_lane("standards", std("lean"))["ids"] if i != d]
        self.assertNotEqual(s, fid("src/calc.py", "lean", "return a - b"))
        self.assertRegex(s, "^" + tr.report.ID_RE + "$")

    def test_standards_validation(self):
        out = self.ingest_lane(
            "standards", std("bug"), std(rule="Never use tabs"), std(rule_source="conventions/NOPE.md"),
            std(rule_source="CLAUDE.md"), std(rule_source="conventions/../ledger.json"), std(rule=""),
            std(rule="x" * 501), std(rule_source="/etc/hostname"), std(), {"inspected": []})
        self.assertEqual((len(out["ids"]), out["dropped"]["invalid"]), (1, 8))

    def test_foreign_keys_cannot_retag_a_lane(self):
        out = self.ingest_lane("standards", dict(std("lean"), source_lane="defects", axis="defects"))
        self.verdicts({"id": out["ids"][0], "verdict": "confirmed", "evidence": [CITE], "reason": "r"})
        (f,) = self.doc()["findings"]
        self.assertEqual((f["axis"], f["source_lane"]), ("standards", "standards"))

    def test_convention_handoff_fences_the_rule(self):
        (i,) = self.ingest_lane("standards", std())["ids"]
        body = self.handoff(i)
        self.assertRegex(body, r"(?s)check: rule\n.*<<<UNTRUSTED-([0-9a-f]+)\n" + RULE + r"\n\1>>>")
        for banned in ("severity", "category", "fix", "return a - b", "required"):
            self.assertNotIn(banned, body)

    def test_lean_handoff_and_traced_suggestion(self):
        (i,) = self.ingest_lane("standards", std("lean", severity="blocking"))["ids"]
        body = self.handoff(i)
        self.assertIn("check: property\n", body)
        for banned in ("severity", "category", "fix", "return a - b", "required", "rule:"):
            self.assertNotIn(banned, body)
        self.verdicts({"id": i, "verdict": "confirmed", "evidence": [CITE], "reason": "r"})
        (f,) = self.doc()["findings"]
        self.assertEqual((f["evidence_class"], f["severity"]), ("traced", "suggestion"))

    def test_ceiling_clamps_per_category(self):
        out = self.ingest_lane("standards", std("comment"), std("smell", title="smell"))
        self.verdicts(*[{"id": i, "verdict": "confirmed", "evidence": [CITE], "reason": "r"} for i in out["ids"]])
        got = {f["category"]: f["severity"] for f in self.doc()["findings"]}
        self.assertEqual(got, {"comment": "required", "smell": "suggestion"})

    def test_text_shows_axes_in_fixed_order_and_spec_skipped(self):
        self.plan("plan", "--run", self.run_dir)
        self.ingest_lane("standards", std())
        self.ingest_lane("defects", CAND)
        rc, out = self.rep("render")
        heads = [m.group(0) for m in re.finditer(r"(?m)^(Defects|Standards|Spec)$", out)]
        self.assertEqual(heads, ["Defects", "Standards", "Spec"])
        self.assertIn("Spec: skipped (no spec)", out)

    def test_unknown_lane_refuses(self):
        rc, out = self.rep("ingest", "", extra=["--lane", "bogus"])
        self.assertEqual((rc, json.loads(out)["reason"]), (1, "usage"))

    def test_plan_lanes_ingest_under_their_own_axis(self):
        self.plan("plan", "--run", self.run_dir, "--tier", "quick")
        cats = {"defects": "bug", "standards": "lean"}
        for c in self.plan_json()["calls"]:
            self.ingest_lane(c["lane"], cand("return a - b", 5, category=cats[c["lane"]]) if c["lane"] == "defects"
                             else std(cats[c["lane"]]))
        out = json.loads(self.rep("render", extra=["--format", "json"])[1])
        self.assertEqual({f["axis"] for f in out["unverifiable"]}, {"defects", "standards"})

    def test_python_39_grammar(self):
        with open(os.path.join(SCRIPTS, "plan.py"), encoding="utf-8") as fh:
            ast.parse(fh.read(), feature_version=(3, 9))


SQL_TEXT = "ALTER TABLE t DROP COLUMN c;\n"
SQL_CAND = cand("ALTER TABLE t DROP COLUMN c;", 1, path="migrations/001_add.sql")
OTHER_PACKS = ("r", "js-ts", "concurrency", "api-compat", "security-owasp", "llm-generated-code")


class Packs(Fixture):
    feat_files = {"src/calc.py": tr.FEAT, "migrations/001_add.sql": SQL_TEXT}

    def read(self, rel):
        with open(os.path.join(self.run_dir, rel), encoding="utf-8") as fh:
            return fh.read()

    def run_plan(self, *args, stdin=""):
        rc, out = self.plan("plan", "--run", self.run_dir, *args, stdin=stdin)
        self.assertEqual(rc, 0, out)
        return self.plan_json()

    @staticmethod
    def names(p):
        return [x["name"] for x in p["packs"]]

    def test_py_and_sql_select_exactly_their_packs(self):
        p = self.run_plan()
        self.assertEqual(self.names(p), ["python", "sql", "data-migrations"])
        self.assertEqual({x["name"]: x["files"] for x in p["packs"]},
                         {"python": ["src/calc.py"], "sql": ["migrations/001_add.sql"],
                          "data-migrations": ["migrations/001_add.sql"]})
        self.assertEqual(sorted(os.listdir(os.path.join(self.run_dir, "inputs", "packs"))),
                         ["data-migrations.md", "python.md", "sql.md"])
        for lane in ("defects", "standards"):
            text = self.read("inputs/%s.md" % lane)
            self.assertIn("## Domain packs", text)
            self.assertIn("packs/python.md", text)
            for gone in OTHER_PACKS:
                self.assertNotIn("packs/%s.md" % gone, text)
        self.assertIn("## Smells", self.read("inputs/standards.md"))
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "inputs", "smells.md")))

    def test_pack_cap_keeps_the_ranked_first_and_lists_the_rest(self):
        p = self.run_plan("--tier", "quick")
        self.assertEqual(self.names(p), ["python", "sql"])
        self.assertIn({"what": "pack data-migrations", "why": "pack cap"}, p["skipped"])
        self.assertNotIn("data-migrations", self.read("inputs/defects.md"))

    def test_llm_pack_loads_only_on_ai_flag_or_deep(self):
        for args, want in ((["--tier", "standard"], False), (["--tier", "quick"], False),
                           (["--tier", "standard", "--ai"], True), (["--tier", "deep"], True)):
            self.assertEqual("llm-generated-code" in self.names(self.run_plan(*args)), want, args)

    def test_rerun_leaves_one_section(self):
        self.run_plan()
        self.run_plan()
        self.assertEqual(self.read("inputs/defects.md").count("## Domain packs"), 1)
        std_text = self.read("inputs/standards.md")
        self.assertEqual((std_text.count("## Domain packs"), std_text.count("## Smells")), (1, 1))

    def test_change_target_is_never_sharded(self):
        self.assertEqual(self.run_plan("--tier", "deep")["shards"], [])

    def test_no_match_writes_none(self):
        with open(os.path.join(self.run_dir, "target.json"), encoding="utf-8") as fh:
            tj = json.load(fh)
        tj["included"] = ["README.txt"]
        with open(os.path.join(self.run_dir, "target.json"), "w", encoding="utf-8") as fh:
            json.dump(tj, fh)
        self.assertEqual(self.run_plan()["packs"], [])
        self.assertTrue(self.read("inputs/defects.md").rstrip().endswith("## Domain packs\n\nnone"))

    def test_every_pack_key_has_a_file(self):
        import plan as mod
        self.assertEqual({row[0] for row in mod.PACKS}, set(tp.PACK_NAMES))
        for row in mod.PACKS:
            self.assertTrue(os.path.isfile(tp.pack_path(row[0])), row[0])

    def test_pack_files_are_private(self):
        self.run_plan()
        mode = os.stat(os.path.join(self.run_dir, "inputs", "packs", "python.md")).st_mode
        self.assertEqual(stat.S_IMODE(mode), 0o600)

    def test_handoff_carries_the_matching_do_not_flag(self):
        self.run_plan()
        py_dnf = tp.sections(tp.read(tp.pack_path("python")))["Do not flag"]
        sql_dnf = tp.sections(tp.read(tp.pack_path("sql")))["Do not flag"]
        mig_dnf = tp.sections(tp.read(tp.pack_path("data-migrations")))["Do not flag"]
        first = lambda lines: next(x for x in lines if x.strip())[:40]  # noqa: E731
        i = self.ingest_lane("defects", CAND)["ids"][0]
        body = self.handoff(i)
        self.assertIn(first(py_dnf), body)
        self.assertNotIn(first(sql_dnf), body)
        j = self.ingest_lane("defects", SQL_CAND)["ids"][0]
        body = self.handoff(j)
        self.assertIn(first(sql_dnf), body)
        self.assertIn(first(mig_dnf), body)
        self.assertNotIn(first(py_dnf), body)


class SecurityPack(Fixture):
    """security-owasp loads for an added subprocess call, not for the same call on an unchanged line."""
    CALL = "import subprocess\nsubprocess.run(['ls'])\n"
    ADD = "\ndef add(a, b):\n    return a - b\n"

    def names(self):
        rc, out = self.plan("plan", "--run", self.run_dir)
        self.assertEqual(rc, 0, out)
        return [x["name"] for x in self.plan_json()["packs"]]


class SecurityPackUnchanged(SecurityPack):
    base_files = {"src/calc.py": tr.BASE["src/calc.py"] + SecurityPack.CALL}
    feat_files = {"src/calc.py": tr.BASE["src/calc.py"] + SecurityPack.CALL + SecurityPack.ADD}

    def test_unchanged_call_does_not_load(self):
        self.assertEqual(self.names(), ["python"])


class SecurityPackAdded(SecurityPack):
    base_files = {"src/calc.py": tr.BASE["src/calc.py"]}
    feat_files = {"src/calc.py": tr.BASE["src/calc.py"] + SecurityPack.CALL}

    def test_added_call_loads(self):
        self.assertEqual(self.names(), ["python", "security-owasp"])


PAD = "value = 1\n"
BIG = "x" * 59 + "\n"  # 60 bytes
SMALL = "y = 1\n\n\n\n"  # 10 bytes


class ShardBase(Fixture):
    """Path and repo targets; SHARD_BYTES is patched small, so plan runs in process."""
    pin_args = ["."]

    def run_plan(self, shard_bytes=1, cfg=None, tier="standard", **kw):
        import plan as mod
        with mock.patch.object(mod, "SHARD_BYTES", shard_bytes), mock.patch.dict(os.environ):
            for k in ENV_VARS:
                os.environ.pop(k, None)
            mod.plan(self.run_dir, tier, cfg or {}, **kw)
        return self.plan_json()

    def read_text(self, *rel):
        with open(os.path.join(self.run_dir, *rel), encoding="utf-8") as fh:
            return fh.read()

    def read_json(self, *rel):
        return json.loads(self.read_text(*rel))


class ShardMerge(ShardBase):
    base_files = {"big/a/x.py": BIG, "big/b/y.py": BIG, "small1/m.py": SMALL, "small2/n.py": SMALL}
    feat_files = {"small1/m.py": SMALL + "#\n"}

    def test_big_group_splits_and_small_groups_merge(self):
        p = self.run_plan(shard_bytes=100)
        self.assertEqual(sorted(sorted(s["files"]) for s in p["shards"]),
                         [["big/a/x.py"], ["big/b/y.py", "small1/m.py", "small2/n.py", ]])

    def test_one_group_is_not_sharded(self):
        p = self.run_plan(shard_bytes=10 ** 9)
        self.assertEqual((p["shards"], [c["shard"] for c in p["calls"]]), ([], [None] * len(p["calls"])))


class Shards(ShardBase):
    base_files = {"auth/login.py": PAD, "docs/guide.md": PAD, "util/helpers.py": PAD}
    feat_files = {"auth/login.py": PAD + "other = 2\n"}

    def test_cap_keeps_the_riskiest_and_lists_the_rest(self):
        p = self.run_plan(cfg={"max_shards": 2})
        self.assertEqual([(s["id"], s["files"]) for s in p["shards"]],
                         [(1, ["auth/login.py"]), (2, ["docs/guide.md"])])
        self.assertGreater(p["shards"][0]["risk"], p["shards"][1]["risk"])
        self.assertIn({"what": "shard 3", "why": "shard cap: 1 files", "files": 1}, p["skipped"])

    def test_each_shard_has_its_own_run_tree_and_ledger(self):
        before = self.read_text("ledger.json")
        parent_tree = sorted(os.listdir(os.path.join(self.run_dir, "tree")))
        p = self.run_plan()
        for s in p["shards"]:
            d = os.path.join(self.run_dir, "shards", str(s["id"]))
            self.assertEqual(s["run"], d)
            self.assertEqual(stat.S_IMODE(os.stat(d).st_mode), 0o700)
            tj = self.read_json("shards", str(s["id"]), "target.json")
            self.assertEqual((tj["included"], tj["run"]), (s["files"], d))
            self.assertEqual(self.read_text("shards", str(s["id"]), "change.diff"), "")
            tree = os.path.join(d, "tree")
            on_disk = {os.path.relpath(os.path.join(r, f), tree) for r, _, fs in os.walk(tree) for f in fs}
            self.assertEqual(on_disk & {"auth/login.py", "docs/guide.md", "util/helpers.py"}, set(s["files"]))
            self.assertTrue(os.path.isfile(os.path.join(d, "ledger.json")))
        self.assertEqual(self.read_text("ledger.json"), before)
        self.assertEqual(sorted(os.listdir(os.path.join(self.run_dir, "tree"))), parent_tree)

    def test_shards_inherit_the_single_trust_answer(self):
        import ledger
        for stored in ("own", "external", None):
            with self.subTest(stored=stored):
                self.run_dir = self.pin(["."])
                if stored:
                    ledger.trust_set(self.run_dir, stored)
                with mock.patch.object(ledger, "trust_set", wraps=ledger.trust_set) as spy:
                    p = self.run_plan(cfg={"max_shards": 2})
                self.assertEqual(len(p["shards"]), 2)
                for s in p["shards"]:
                    led = self.read_json("shards", str(s["id"]), "ledger.json")
                    self.assertEqual((led["trust"], led["trust_asked"]), (stored or "unset", stored is not None))
                self.assertNotIn(self.run_dir, [c.args[0] for c in spy.call_args_list])
                self.assertEqual(len(spy.call_args_list), 2 if stored else 0)

    def test_calls_per_shard(self):
        p = self.run_plan(cfg={"max_shards": 2}, tier="deep")
        self.assertEqual(sorted((c["lane"], c["shard"]) for c in p["calls"] if c["lane"] != "spec"),
                         [("defects", 1), ("defects", 2), ("standards", 1), ("standards", 2)])
        for c in p["calls"]:
            self.assertEqual(c["input"], os.path.join(self.run_dir, "shards", str(c["shard"]), "inputs",
                                                      c["lane"] + ".md"))

    def test_packs_go_into_each_shard_input(self):
        self.run_plan(cfg={"max_shards": 2})
        self.assertIn("packs/python.md", self.read_text("shards", "1", "inputs", "defects.md"))
        self.assertTrue(os.path.isfile(os.path.join(self.run_dir, "shards", "1", "inputs", "packs", "python.md")))
        self.assertNotIn("packs/python.md", self.read_text("shards", "2", "inputs", "defects.md"))

    def test_rerun_rebuilds_the_shard_dirs(self):
        self.run_plan(cfg={"max_shards": 3})
        self.run_plan(cfg={"max_shards": 2})
        self.assertEqual(sorted(os.listdir(os.path.join(self.run_dir, "shards"))), ["1", "2"])

    def test_end_to_end_ingest_and_render_list_the_skipped_shard(self):
        self.run_plan(cfg={"max_shards": 2})
        c = cand("value = 1", 1, path="auth/login.py", category="bug")
        rc, out = self.rep("ingest", tr.lines(c), extra=["--lane", "defects", "--shard", "1"])
        self.assertEqual(rc, 0, out)
        rc, out = self.rep("render")
        self.assertIn("shard skipped: shard 3 (shard cap: 1 files) 1 files", out)


class Inventory(Fixture):
    feat_files = {"src/calc.py": tr.FEAT + "import requests\n", "requirements.txt": "flask==2.0\n"}

    def test_change_yields_names_with_locations_and_the_sota_prompt(self):
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "deep")
        self.assertEqual(rc, 0, out)
        p = self.plan_json()
        inv = [{"line": 1, "name": "flask", "path": "requirements.txt"},
               {"line": 6, "name": "requests", "path": "src/calc.py"}]
        self.assertEqual(p["sota_inventory"], inv)
        sota = [c for c in p["calls"] if c["lane"] == "sota"]
        self.assertEqual([c["prompt"] for c in sota], ["SOTA lane. Inventory: " + json.dumps(inv, sort_keys=True, separators=(",", ":"))])
        self.assertNotIn({"what": "sota", "why": "no dependency or import names in the change"}, p["skipped"])

    def test_names_by_file_kind(self):
        import plan as mod
        src = {"a.py": [(1, "import os, numpy.linalg as la"), (2, "from pandas.io import x"), (3, "from . import rel")],
               "b.ts": [(1, "import { a } from '@scope/pkg/sub'"), (2, "const m = require('left-pad')"),
                        (3, "import './local'")],
               "c.R": [(1, "library(dplyr)"), (2, "x <- require('ggplot2')")],
               "requirements-dev.txt": [(1, "# comment"), (2, "pytest>=7 ; python_version>'3'"), (3, "-r base.txt")],
               "pyproject.toml": [(1, '    "httpx[cli]>=0.2",'), (2, 'name = "mine"')],
               "package.json": [(1, '  "express": "^4.1.0",'), (2, '  "version": "1.0.0",'), (3, '  "name": "mine",')],
               "DESCRIPTION": [(1, "Imports: cli,"), (2, "    rlang (>= 1.0),"), (3, "Title: Thing")]}
        self.assertEqual(sorted(i["name"] for i in mod._inventory(src)),
                         ["@scope/pkg", "cli", "dplyr", "express", "ggplot2", "httpx", "left-pad", "numpy", "os",
                          "pandas", "pytest", "rlang"])

    def test_unsafe_overlong_and_surplus_names_are_dropped(self):
        import plan as mod
        src = {"a.js": [(1, "require('bad;name')"), (2, "require('" + "a" * 101 + "')"), (3, "require('good')"),
                        (4, "require('has space')"), (5, "require('../up')")],
               "b.py": [(i, "import m%d" % i) for i in range(1, 80)]}
        inv = mod._inventory(src)
        self.assertEqual(len(inv), mod.INVENTORY_MAX)
        self.assertEqual(inv[0], {"line": 3, "name": "good", "path": "a.js"})
        self.assertTrue(all(set(i) == {"path", "line", "name"} for i in inv))
        self.assertEqual(len({i["name"] for i in inv}), len(inv))
        for i in inv:
            self.assertRegex(i["name"], r"\A[A-Za-z0-9@/._-]{1,100}\Z")


class InventoryEmpty(Fixture):
    def test_no_names_means_no_sota_call(self):
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "deep")
        p = self.plan_json()
        self.assertEqual(p["sota_inventory"], [])
        self.assertNotIn("sota", [c["lane"] for c in p["calls"]])
        self.assertIn({"what": "sota", "why": "no dependency or import names in the change"}, p["skipped"])


def _tree_bytes(d):
    return sum(os.path.getsize(os.path.join(r, f)) for r, _, fs in os.walk(d) for f in fs)


class Preview(Fixture):
    def test_deep_preview_states_calls_caps_and_ceilings(self):
        rc, out = self.plan("plan", "--run", self.run_dir, "--tier", "deep")
        p = self.plan_json()
        self.assertEqual((out["confirm"], p["confirm"], out["preview"]), (True, True, p["preview"]))
        pv = "\n".join(p["preview"])
        self.assertTrue(p["preview"][0].startswith("Tier deep, mode parallel"))
        for want in ("Call defects: opus/xhigh", "Call standards: opus/xhigh", "Verifier calls: up to 60, sonnet/high",
                     "Agent calls: %d" % (len(p["calls"]) + 60), "finder 40", "verifier 12", "Packs: python"):
            self.assertIn(want, pv)
        self.assertEqual(p["preview"][-1], "Estimate only: real token use varies; no prices are shown.")
        self.assertNotRegex(pv, r"[$]|USD|EUR|price per")

    def test_token_estimate_is_total_bytes_over_four(self):
        self.plan("plan", "--run", self.run_dir)
        p = self.plan_json()
        call = next(c for c in p["calls"] if c["lane"] == "defects")
        total = (_tree_bytes(os.path.join(self.run_dir, "tree")) + os.path.getsize(call["input"])
                 + os.path.getsize(os.path.join(self.run_dir, "change.diff")))
        line = next(x for x in p["preview"] if x.startswith("Call defects:"))
        self.assertIn("about %d input tokens (estimate)" % ((total + 3) // 4), line)

    def test_confirm_by_tier_and_target(self):
        for args, want in ((["--tier", "quick"], False), (["--tier", "standard"], False), (["--tier", "deep"], True)):
            rc, out = self.plan("plan", "--run", self.run_dir, *args)
            self.assertEqual(out["confirm"], want, args)

    def test_serial_preview_counts_no_agent_calls(self):
        rc, out = self.plan("plan", "--run", self.run_dir, "--serial")
        pv = "\n".join(out["preview"])
        self.assertIn("Agent calls: 0", pv)
        self.assertIn("main thread", pv)


class PreviewShards(ShardBase):
    base_files = {"auth/login.py": PAD, "docs/guide.md": PAD, "util/helpers.py": PAD}
    feat_files = {"auth/login.py": PAD + "other = 2\n"}

    def test_repo_target_confirms_and_shows_shards_without_paths(self):
        import plan as mod
        with mock.patch.object(mod, "SHARD_BYTES", 1):
            out = mod.plan(self.run_dir, "standard", {"max_shards": 2})
        self.assertTrue(out["confirm"])
        p = self.plan_json()
        self.assertIn("Shards: 2 of 3 kept", p["preview"])
        self.assertIn("Call defects, shard 2:", "\n".join(p["preview"]))
        tj = self.read_json("target.json")
        for line in p["preview"]:
            self.assertNotIn(self.run_dir, line)
            for path in tj["included"]:
                self.assertNotIn(path, line)
                self.assertNotIn(path.split("/")[0], line)
            self.assertRegex(line, r"\A[A-Za-z0-9 .,:;/()\[\]_-]+\Z")


if __name__ == "__main__":
    unittest.main()
