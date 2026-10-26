# 0003: `--strict` lifts an inferred concern only when the verifier re-derives the trigger

Status: accepted (2026-10-26). Amends the "refuted is dropped" rule, under `--strict` only. Issues: #82, #83, #85, #86.

## Context

The verification gate reports a concern only at `traced`. A concern below that goes to the Unverifiable list, and a
verdict of `refuted` is dropped. Maintainers who want recall over precision asked for a way to see the middle tier
without lowering the default gate. Separately, a finding often has siblings elsewhere in the repository, and reviewers
asked for them to be listed. Rubrics came with a stdin form that no caller used.

## Decision

- `--strict` lifts a concern into a finding of class `inferred` only when the verifier re-derives the trigger from
  the cited source (reason prefix `re-derived:`). Any other `unverifiable` verdict stays Unverifiable.
- A `refuted` concern is not dropped under `--strict`: it stays Unverifiable with a `refuted:` reason. Without
  `--strict` the earlier rule holds and it is dropped.
- `--sweep` is opt-in (plan.json `"sweep": true`). Without it, output is byte-identical to a run before the sweep existed.
- `--rubric <path>` is the only rubric form (repeatable). `--rubric -` (stdin) is removed.

## Alternatives considered

- Lift every `unverifiable` verdict: turns the gate into noise. Rejected.
- Sweep on by default: changes every default report and costs a `git grep` per finding. Rejected.
- Check the re-derivation in code: the script cannot judge a derivation. The lift trusts the reason prefix (known limit, `references/strict.md`).

## Consequences

Under `--strict`, a refuted Defects concern can make the verdict Needs discussion; the `refuted:` reason says why.
`--fix` skips inferred findings. Default and serial runs are unchanged.
