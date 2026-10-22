"""--nit: the one owner of nit logic. A nit is severity nit with a basis: a quoted rule (category convention) or a counted
norm (category norm). Candidate checks, the norm counter, the wording of a basis and the text-report rows live here.
Stdlib only; imports nothing from report.py or post.py, which both call in."""
import fnmatch
import json
import os

NIT_RULE_CAP = 15  # rules the text report lists under Nits; the JSON keeps every nit
NORM_SCAN_BYTES = 8 << 20  # bytes of tree/ files norm() may read per call: a norm_glob of "*" on a big repo is refused, not slow
NORM_MIN = 20  # lines naming either idiom that a norm needs before a deviation from it is a finding

# Reasons a nit-mode candidate is refused for: the line itself claimed severity nit or category norm and did not qualify, so
# dropping it loses no finding. nit-only is not here: it fires only for a candidate that claims neither, a real finding the mode discarded.
REFUSED = frozenset(("nit-not-enabled", "nit-basis", "norm-malformed", "norm-quote-mismatch", "norm-too-large",
                     "norm-sample-small", "norm-not-majority"))


def check(c, lane, mode):
    """The failing check of a candidate c (severity, category) sent by lane under nit mode (False, True or "only"), else None.
    Order is part of the wire contract: bad-type, nit-not-enabled, nit-basis, nit-only."""
    if c["severity"] == "nit" or c["category"] == "norm":
        if lane != "standards":
            return "bad-type"
        if not mode:
            return "nit-not-enabled"
        if not (c["severity"] == "nit" and c["category"] in ("convention", "norm")):
            return "nit-basis"
    if mode == "only" and c["severity"] != "nit":
        return "nit-only"
    return None


def basis(c):
    """The basis a candidate carries: "norm" or "rule" for a nit, None for any other severity."""
    return None if c["severity"] != "nit" else "norm" if c["category"] == "norm" else "rule"


def norm(o, tree, quote):
    """(fields, None) of a norm line, else (None, the failing check). The skill counts the lines of tree/ files matching
    norm_glob that hold each literal idiom; the lane's own counts are never read. shortcut: literal strings, not
    patterns (a model-written regex over repo text can backtrack without end); upgrade when lanes need patterns."""
    got = {k: o.get(k) for k in ("norm_for", "norm_against", "norm_glob")}
    if not all(isinstance(v, str) and v.strip() and len(v) <= 200 for v in got.values()):
        return None, "norm-malformed"
    a, b = got["norm_for"], got["norm_against"]
    if min(len(a), len(b)) < 3 or a in b or b in a:  # a line holding both would count for both: no majority to speak of
        return None, "norm-malformed"
    if got["norm_against"] not in quote:
        return None, "norm-quote-mismatch"
    if got["norm_for"] in quote:  # the deviating line must not use the norm it deviates from
        return None, "norm-malformed"
    n = {"for": 0, "against": 0}
    budget = NORM_SCAN_BYTES
    for root, _, files in os.walk(tree):
        for f in files:
            if os.path.islink(os.path.join(root, f)):  # tree/ holds no link by construction; never read one that appears
                continue
            rel = os.path.relpath(os.path.join(root, f), tree)
            if not (fnmatch.fnmatch(rel, got["norm_glob"]) or fnmatch.fnmatch(f, got["norm_glob"])):
                continue
            path = os.path.join(root, f)
            with open(path, "rb") as fh:
                if b"\0" in fh.read(8192):  # binary: no idiom lives there
                    continue
            budget -= os.path.getsize(path)
            if budget < 0:
                return None, "norm-too-large"
            with open(path, encoding="utf-8", errors="replace") as fh:
                for row in fh:
                    has_for, has_against = got["norm_for"] in row, got["norm_against"] in row
                    if has_for != has_against:  # a line holding both idioms says nothing about which one the repo prefers
                        n["for" if has_for else "against"] += 1
    total = n["for"] + n["against"]
    if total < NORM_MIN:
        return None, "norm-sample-small"
    if n["for"] <= n["against"]:
        return None, "norm-not-majority"
    return dict(got, norm=dict(n, total=total)), None


def basis_text(f):
    """What a nit enforces: the quoted rule, or the counted norm. "" for any other finding. The single place this is worded."""
    if f.get("basis") == "rule":
        return f["rule"]
    if f.get("basis") == "norm":
        return "norm %s over %s (%d of %d lines)" % (json.dumps(f["norm_for"]), json.dumps(f["norm_against"]),
                                                      f["norm"]["for"], f["norm"]["total"])
    return ""


def rows(nits, e):
    """Nits grouped by what they enforce, the first NIT_RULE_CAP groups listed with their count and up to three places
    (e escapes a text); the JSON report keeps every nit."""
    groups = {}
    for i in nits:
        groups.setdefault("rule: " + basis_text(i), []).append(i)
    out = []
    for key, items in list(groups.items())[:NIT_RULE_CAP]:
        out.append("- %s (%d finding%s)" % (e(key), len(items), "" if len(items) == 1 else "s"))
        out += ["    %s:%d  %s [%s]" % (e(i["path"]), i["line"], e(i["title"]), i["id"]) for i in items[:3]]
    if len(groups) > NIT_RULE_CAP:
        out.append("(+%d more rules not shown)" % (len(groups) - NIT_RULE_CAP))
    return out
