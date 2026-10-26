# 0002: `--nit` reports nits from every review lane

Status: accepted (2026-10-25). Amends [0001](0001-nit-mode.md). Issue: #76.

## Context

0001 gave `--nit` one source of nits, the Standards lane, with a basis the scripts check: a rule quoted from a
rule file, or a norm the skill counts. `references/lanes.md` says only the Standards lane may send a `nit`, and
`scripts/nit.py` `check()` refuses a `nit` from any other lane (`bad-type`). 0001 rejected a separate nit lane; it
did not rule out nits from the lanes that already exist. The owner expects `--nit` to nitpick in all lanes. A
whole-repository `--nit` review showed it: the Defects and Spec lanes read the code and the spec closely, and what
they notice below the reporting floor is dropped.

## Decision

Every lane that reads repository evidence may report severity `nit` under `--nit`, each on its own evidence basis.
The lane keeps its gate; only the severity drops.

- Standards: unchanged. A rule nit quotes a rule from a merge-base rule file; a norm nit rests on the skill's own count
  of at least 20 lines.
- Defects: a finding that passes the verification gate in full (file:line, quoted code, a concrete trigger, evidence
  `traced` or better) and has trivial impact. Examples: a misleading name, dead code, a branch nothing reaches, an
  unchecked edge that a reachable input trips. A concern below `traced` is not a nit; it follows the existing rule
  (Unverifiable).
- Spec: a minor deviation from a quoted spec line, with that line cited as for any Spec finding.
- SOTA: not a source of nits. It is advisory web research with no repository line to hang a nit on, and it never
  counts toward the verdict.
- Defects and Spec nits go through the verifier of their axis like any finding; the verifier defaults to refuting. A
  nit it refutes is dropped.
- Everything else in 0001 stays: a nit never changes the verdict, `nits: N` counts all lanes, a diff target reports
  changed lines only and a repo target also reports pre-existing nits, `--fix` ignores nits, `--nit --lean` is the
  nits-only pass, and without `--nit` no lane input and no report changes.

## Alternatives considered

- Keep the rule-or-norm basis for every lane: Defects and Spec nits would only be more convention findings, and a
  nit about a misleading name or a dead branch has no rule to quote. Rejected.
- A separate nit lane: still rejected for 0001's reason (it duplicates reading and adds a verifier path).
- Let SOTA report nits: they would be "a newer practice exists" notes without evidence from the repository.
  Rejected.
- Skip the verifier for nits: an unverified nit is noise, and the verifier is the control against a model's taste.
  Rejected.

## Consequences

- More candidates reach the verifiers under `--nit`. The per-run verifier cap, which would have dropped nits first, went with #73,
  so the implementation lands after it.
- `scripts/nit.py`, `scripts/plan.py` (lane-specific `## Nit mode` sections), `scripts/report.py` (the
  `bad-type` check), `references/lanes.md` and the reason table change. The pinned size caps stay; wording is
  shortened to fit, and a cap is not raised without the owner.
- The nit group label for a Defects or Spec nit is its category, since it has no `rule` or `norm`.
