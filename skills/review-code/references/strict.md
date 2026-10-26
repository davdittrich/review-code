# Strict mode and same-class sweep

## Strict mode

`--strict` (separate from `--nit`) lowers the floor of the verification gate from `traced` to `inferred`, for Defects, Standards and
Spec concerns only. It reaches `plan.py plan --strict`, which writes `"strict": true` to plan.json; it also adds a `## Strict mode` section to the Defects, Spec
and Standards inputs (an untraced concern with file:line, quote and trigger keeps its real severity). No new lane field.

- `report.py` adds a `strict:` line to each verify handoff. A verifier that re-derives the trigger from the cited source but cannot
  trace every path answers `unverifiable` with the cite and a reason that starts `re-derived:`.
- `report.py` lifts exactly those concerns into findings of class `inferred`, severity kept (`STRICT_CLASS` is the one constant
  `fix.py` and `post.py` also read). Any other `unverifiable` verdict stays Unverifiable; a `refuted` one also stays Unverifiable, reason prefixed `refuted: `, not dropped
  (`dropped.refuted` stays 0; default and serial runs still drop it); `speculative` stays a question; web-sourced `sota` findings never lift.
- Because a refuted concern is kept, a non-nit Defects concern the verifier refuted makes the verdict Needs discussion under `--strict`
  (Standards and Spec: only at Required); read its `refuted:` reason before acting. `--comment` counts these in "Not posted: N unverifiable".
- At Blocking or Required an inferred finding makes the verdict not clean; a Suggestion does not. A nit never changes the verdict
  (ADR 0001), with or without `--strict`.
- `--fix` and `--fix-tests` skip inferred findings (`why: inferred`). `--comment` labels them "inferred (source read)".
- Serial mode (every candidate self-scored) ignores the flag and adds a lost guarantee saying so.
- JSON adds `strict` (true) and `inferred` (the count) only under the flag; the text summary adds `strict: N inferred`; SARIF keeps the
  severity level and carries the class in `properties.evidenceClass`. Without the flag, none of these appear.

## Same-class sweep

After verification, `report.py` searches the pinned tree for the first quoted line of each finding (8 characters or more, SOTA never)
with `git grep -F`: deterministic, no LLM, no new lane field. Hits on lines the diff adds, and the finding's own lines, are dropped.
The rest ride under the finding as `same_class` (`path`, `line`, `text`; at most 5, sorted) with `same_class_omitted` for the surplus, and
as "same class elsewhere (outside the diff)" rows in the text report. Generated files are searched too. The sweep reads no verdict and
changes none. It is on by default (gh#86), so default output carries `same_class` rows; only `--strict`'s own keys are absent without the flag. Whole-file targets (`--repo`, path) have no diff and skip it.
