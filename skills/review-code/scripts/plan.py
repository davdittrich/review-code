"""Choose lanes, models and caps for one run and write <run>/plan.json (mode 0600). Stdlib only, Python 3.9 grammar.

    plan.py plan --run DIR [--tier quick|standard|deep]     stdout: one JSON line (status, reason, detail, tier,
                                                            mode, calls (count), skipped)
    stdin (optional, not a TTY): key=value lines, keys in CONFIG_KEYS; empty values and `${...}` (an unset
    userConfig reference) are ignored; anything else invalid refuses `bad-config`, naming only the key.

plan.json (sort_keys): version, tier, mode (parallel|serial), calls[{lane, axis, agent, model, effort, input, shard,
prompt}], verifier{agent, model, effort}, spec_verifier{...}, caps{max_verifier_calls, max_shards, max_packs,
wave_size}, max_turns{name: n}, packs, shards, skipped[{what, why}], lost_guarantees, sota_inventory, preview,
confirm, effective_overrides{key: value}. The orchestrator obeys it; report.py reads caps.max_verifier_calls and skipped.
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

STDOUT_KEYS = {"status", "reason", "detail", "tier", "mode", "calls", "skipped"}
DEFAULT_TIER = "standard"
# (model, effort) per role. Unvalidated defaults: there is no eval corpus to tune them against.
TIERS = {
    "quick": {"lanes": ("defects", "standards"), "finder": ("sonnet", "low"), "verifier": ("sonnet", "medium"),
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


def plan(run, tier, cfg, flag_tier=False):
    _target_exists(run)
    led = ledger._load(run)
    spec = led.get("spec")
    spec_none = not isinstance(spec, dict) or spec.get("status") == "none"
    t = TIERS[tier]
    fm, fe = cfg.get("finder_model", t["finder"][0]), cfg.get("finder_effort", t["finder"][1])
    vm, ve = cfg.get("verifier_model", t["verifier"][0]), cfg.get("verifier_effort", t["verifier"][1])
    caps = {k: cfg.get(k, t[k]) for k in CAP_BOUNDS}
    inventory, skipped, calls = [], [], []  # inventory: 04-07 fills it; an empty one drops the sota lane
    for lane in t["lanes"]:
        if lane == "spec" and spec_none:
            skipped.append({"what": "spec", "why": "no spec"})
        elif lane == "sota" and not inventory:
            skipped.append({"what": "sota", "why": "no dependency or import names in the change"})
        else:
            path = os.path.join(run, "inputs", lane + ".md")
            calls.append({"lane": lane, "axis": report.LANE_AXIS[lane], "agent": LANE_AGENT[lane], "model": fm,
                          "effort": fe, "input": path, "shard": None,
                          "prompt": "%s lane. Input: %s" % (lane.capitalize(), path)})
    ver = {"model": vm, "effort": ve}
    doc = {"version": 1, "tier": tier, "mode": "parallel", "calls": calls,
           "verifier": dict(ver, agent="review-code:verifier"),
           "spec_verifier": dict(ver, agent="review-code:spec-verifier"),
           "caps": dict(caps, wave_size=WAVE_SIZE),
           "max_turns": dict(MAX_TURNS), "packs": [], "shards": [], "skipped": skipped, "lost_guarantees": [],
           "sota_inventory": inventory, "preview": [], "confirm": False,
           "effective_overrides": {k: v for k, v in sorted(cfg.items()) if k != "default_tier" or not flag_tier}}
    target.write_private(os.path.join(run, "plan.json"), json.dumps(doc, sort_keys=True, indent=2))
    return {"status": "ok", "tier": tier, "mode": doc["mode"], "calls": len(calls), "skipped": skipped}


def _usage():
    return target.Refuse("usage", "usage: plan.py plan --run DIR [--tier quick|standard|deep]")


def main(argv):
    try:
        if argv[:2] != ["plan", "--run"] or len(argv) not in (3, 5) or (len(argv) == 5 and argv[3] != "--tier"):
            raise _usage()
        flag_tier = len(argv) == 5
        if flag_tier and argv[4] not in TIERS:
            raise _usage()
        cfg = _read_config()
        tier = argv[4] if flag_tier else cfg.get("default_tier", DEFAULT_TIER)
        out, rc = plan(argv[2], tier, cfg, flag_tier), 0
    except target.Refuse as r:
        out, rc = {"status": "refuse", "reason": r.reason, "detail": r.detail}, 1
    except (OSError, KeyError, ValueError, TypeError) as e:  # one JSON line, never a traceback
        out, rc = {"status": "refuse", "reason": "internal-error", "detail": type(e).__name__}, 1
    print(json.dumps({k: v for k, v in out.items() if k in STDOUT_KEYS}, sort_keys=True))
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
