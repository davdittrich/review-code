"""Choose lanes, models and caps for one run and write <run>/plan.json (mode 0600). Stdlib only, Python 3.9 grammar.

    plan.py plan --run DIR [--tier quick|standard|deep] [--sota|--no-sota] [--serial] [--ai]
                                    stdout: one JSON line (status, reason, detail, tier, mode, calls (count),
                                    skipped, lost_guarantees)
    stdin (optional, not a TTY): key=value lines, keys in CONFIG_KEYS; empty values and `${...}` (an unset
    userConfig reference) are ignored; anything else invalid refuses `bad-config`, naming only the key.

plan.json (sort_keys): version, tier, mode (parallel|serial), calls[{lane, axis, agent, model, effort, input, shard,
prompt}], verifier{agent, model, effort}, spec_verifier{...}, caps{max_verifier_calls, max_shards, max_packs,
wave_size}, max_turns{name: n}, packs, shards, skipped[{what, why}], lost_guarantees, sota_inventory, preview,
confirm, effective_overrides{key: value}, ai. The orchestrator obeys it; report.py reads caps.max_verifier_calls and skipped.
"""
import sys

if sys.version_info < (3, 9):
    print('{"reason": "python-too-old", "status": "refuse"}')
    sys.exit(1)

import json
import os
import re

import ledger
import report
import target

STDOUT_KEYS = {"status", "reason", "detail", "tier", "mode", "calls", "skipped", "lost_guarantees"}
DEFAULT_TIER = "standard"
# (model, effort) per role. Unvalidated defaults: there is no eval corpus to tune them against. The quick verifier
# is opus so it differs from the sonnet finder (user's choice b at 04-06 Task 2, LANE-07).
TIERS = {
    "quick": {"lanes": ("defects", "standards"), "finder": ("sonnet", "low"), "verifier": ("opus", "medium"),
              "max_verifier_calls": 10, "max_shards": 3, "max_packs": 2},
    "standard": {"lanes": ("defects", "standards", "spec"), "finder": ("opus", "medium"),
                 "verifier": ("sonnet", "high"), "max_verifier_calls": 25, "max_shards": 6, "max_packs": 4},
    "deep": {"lanes": ("defects", "standards", "spec", "sota"), "finder": ("opus", "xhigh"),
             "verifier": ("sonnet", "high"), "max_verifier_calls": 60, "max_shards": 12, "max_packs": 9},
}
LANE_AGENT = {"defects": "review-code:finder", "standards": "review-code:standards", "spec": "review-code:spec",
              "sota": "review-code:sota"}
# Ceilings, not per-tier values: maxTurns is frontmatter-only (sub-agents doc), so a call cannot lower it.
MAX_TURNS = {"finder": 40, "standards": 40, "spec": 30, "sota": 20, "verifier": 12, "spec-verifier": 12}
EFFORTS = ("low", "medium", "high", "xhigh", "max")
MODEL_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,63}")
CAP_BOUNDS = {"max_verifier_calls": (1, 200), "max_shards": (1, 50), "max_packs": (1, 9)}
CONFIG_KEYS = {"default_tier", "finder_model", "finder_effort", "verifier_model", "verifier_effort"} | set(CAP_BOUNDS)
MAX_STDIN = 65536
# Half the default limit of 20 concurrent subagents, so other subagents in the session are not refused.
WAVE_SIZE = 10


def _target_exists(run):
    if not os.path.isfile(os.path.join(run, "target.json")):
        raise target.Refuse("no-target", "the run directory has no target.json")


def parse_config(text):
    """Validated {key: value} from key=value lines. Never echoes a rejected value (T-04-18)."""
    cfg = {}
    for line in text.splitlines():
        key, eq, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if not (key or eq or val):
            continue
        if not eq or key not in CONFIG_KEYS:
            raise target.Refuse("bad-config", "unknown or malformed config line")
        if not val or val.startswith("${"):
            continue  # unset userConfig reference: the tier's value stands
        if key in CAP_BOUNDS:
            lo, hi = CAP_BOUNDS[key]
            ok = re.fullmatch(r"[0-9]{1,4}", val) is not None and lo <= int(val) <= hi
        elif key.endswith("_model"):
            ok = MODEL_RE.fullmatch(val) is not None
        else:
            ok = val in (EFFORTS if key.endswith("_effort") else TIERS)
        if not ok:
            raise target.Refuse("bad-config", "invalid value for " + key)
        cfg[key] = int(val) if key in CAP_BOUNDS else val
    return cfg


def _read_config():
    if sys.stdin is None or sys.stdin.isatty():
        return {}
    text = sys.stdin.read(MAX_STDIN + 1)
    if len(text) > MAX_STDIN:
        raise target.Refuse("bad-config", "config input too large")
    return parse_config(text)


BOOL_FLAGS = {"--sota": ("sota", True), "--no-sota": ("sota", False), "--serial": ("serial", True),
              "--ai": ("ai", True)}
# Lost-guarantee sentences are fixed text plus a validated model name; never repository text (T-04-19).
SERIAL_LOST = ["no independent verifier: candidates are self-scored and nothing is Blocking",
               "no lane isolation: the main thread reads target code while holding Bash",
               "Spec axis skipped: the main thread never opens inputs/spec.md",
               "SOTA skipped: no isolated web lane"]
HEURISTIC = "(heuristic: settings env may not reach this script)"


def _env_lost():
    lost = []
    if os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL_FORCE", "").strip().lower() in ("1", "true", "yes"):
        lost.append("per-lane models ignored: CLAUDE_CODE_SUBAGENT_MODEL_FORCE is set " + HEURISTIC)
    if os.environ.get("CLAUDE_CODE_EFFORT_LEVEL"):
        lost.append("per-lane effort ignored: CLAUDE_CODE_EFFORT_LEVEL is set " + HEURISTIC)
    return lost


def plan(run, tier, cfg, flag_tier=False, sota=None, serial=False, ai=False):
    _target_exists(run)
    led = ledger._load(run)
    spec = led.get("spec")
    spec_none = not isinstance(spec, dict) or spec.get("status") == "none"
    t = TIERS[tier]
    fm, fe = cfg.get("finder_model", t["finder"][0]), cfg.get("finder_effort", t["finder"][1])
    vm, ve = cfg.get("verifier_model", t["verifier"][0]), cfg.get("verifier_effort", t["verifier"][1])
    caps = {k: cfg.get(k, t[k]) for k in CAP_BOUNDS}
    lanes = [x for x in t["lanes"] if x != "sota" or sota is not False]
    if sota and "sota" not in lanes:
        lanes.append("sota")
    inventory, skipped, calls = [], [], []  # inventory: 04-07 fills it; an empty one drops the sota lane
    lost = []
    if serial:
        lanes = ["defects", "standards"]
        skipped = [{"what": "spec", "why": "serial: the main thread never opens inputs/spec.md"},
                   {"what": "sota", "why": "serial: no isolated web lane"}]
        lost = list(SERIAL_LOST)
    else:
        # shortcut: aliases and full IDs of one model compare unequal; upgrade when a model registry ships.
        if fm.lower() == vm.lower():
            lost.append("verifier not on a different tier: finders and verifier both run on " + fm)
        lost += _env_lost()
    for lane in lanes:
        if lane == "spec" and spec_none:
            skipped.append({"what": "spec", "why": "no spec"})
        elif lane == "sota" and not inventory:
            skipped.append({"what": "sota", "why": "no dependency or import names in the change"})
        else:
            path = os.path.join(run, "inputs", lane + ".md")
            calls.append({"lane": lane, "axis": report.LANE_AXIS[lane], "agent": "main-thread" if serial else LANE_AGENT[lane],
                          "model": None if serial else fm, "effort": None if serial else fe, "input": path,
                          "shard": None, "prompt": "%s lane. Input: %s" % (lane.capitalize(), path)})
    ver = {"model": vm, "effort": ve}
    doc = {"version": 1, "tier": tier, "mode": "serial" if serial else "parallel", "calls": calls,
           "verifier": dict(ver, agent="review-code:verifier"),
           "spec_verifier": dict(ver, agent="review-code:spec-verifier"),
           "caps": dict(caps, wave_size=WAVE_SIZE),
           "max_turns": dict(MAX_TURNS), "packs": [], "shards": [], "skipped": skipped, "lost_guarantees": lost,
           "sota_inventory": inventory, "preview": [], "confirm": False, "ai": ai,
           "effective_overrides": {k: v for k, v in sorted(cfg.items()) if k != "default_tier" or not flag_tier}}
    if serial:
        doc["verifier"] = doc["spec_verifier"] = {"agent": "main-thread", "model": None, "effort": None}
    target.write_private(os.path.join(run, "plan.json"), json.dumps(doc, sort_keys=True, indent=2))
    return {"status": "ok", "tier": tier, "mode": doc["mode"], "calls": len(calls), "skipped": skipped,
            "lost_guarantees": lost}


def _usage():
    return target.Refuse("usage", "usage: plan.py plan --run DIR [--tier quick|standard|deep] "
                                  "[--sota|--no-sota] [--serial] [--ai]")


def _parse(argv):
    if argv[:2] != ["plan", "--run"] or len(argv) < 3:
        raise _usage()
    opts, i = {}, 3
    while i < len(argv):
        a = argv[i]
        if a == "--tier" and "tier" not in opts and i + 1 < len(argv) and argv[i + 1] in TIERS:
            opts["tier"] = argv[i + 1]
            i += 2
        elif a in BOOL_FLAGS and BOOL_FLAGS[a][0] not in opts:
            opts[BOOL_FLAGS[a][0]] = BOOL_FLAGS[a][1]
            i += 1
        else:
            raise _usage()
    return argv[2], opts


def main(argv):
    try:
        run, opts = _parse(argv)
        cfg = _read_config()
        tier = opts.get("tier") or cfg.get("default_tier", DEFAULT_TIER)
        out, rc = plan(run, tier, cfg, "tier" in opts, opts.get("sota"), opts.get("serial", False),
                       opts.get("ai", False)), 0
    except target.Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason, "detail": r.detail}, 1
    except (OSError, KeyError, ValueError, TypeError) as e:  # one JSON line, never a traceback
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    print(json.dumps({k: v for k, v in out.items() if k in STDOUT_KEYS}, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
