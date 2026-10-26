# Strict mode and same-class sweep

## Strict mode

`--strict` (separate from `--nit`) lowers the floor of the verification gate from `traced` to `inferred`, for Defects, Standards and
Spec concerns only. It reaches `plan.py plan --strict`, which writes `"strict": true` to plan.json; the lanes get no new input and no
new field.

- `report.py` adds a `strict:` line to each verify handoff. A verifier that re-derives the trigger from the cited source but cannot
  trace every path answers `unverifiable` with the cite and a reason that starts `re-derived:`.
- `report.py` lifts exactly those concerns into findings of class `inferred`, severity kept (`STRICT_CLASS` is the one constant
  `fix.py` and `post.py` also read). Any other `unverifiable` verdict stays Unverifiable; a `refuted` one is dropped; `speculative`
  stays a question; web-sourced `sota` findings never lift.
- At Blocking or Required an inferred finding makes the verdict not clean; a Suggestion does not. A nit never changes the verdict
  (ADR 0001), with or without `--strict`.
- `--fix` and `--fix-tests` skip inferred findings (`why: inferred`). `--comment` labels them "inferred (source read)".
- Serial mode (every candidate self-scored) ignores the flag and adds a lost guarantee saying so.
- JSON adds `strict` (true) and `inferred` (the count) only under the flag; the text summary adds `strict: N inferred`; SARIF keeps the
  severity level and carries the class in `properties.evidenceClass`. Without the flag, output is unchanged.

## Same-class sweep

After verification, `report.py` searches the pinned tree for the first quoted line of each finding (8 characters or more, SOTA never)
with `git grep -F`: deterministic, no LLM, no new lane field. Hits on lines the diff adds, and the finding's own lines, are dropped.
The rest ride under the finding as `same_class` (`path`, `line`, `text`; at most 5, sorted) with `same_class_omitted` for the surplus, and
as "same class elsewhere (outside the diff)" rows in the text report. Generated files are searched too. The sweep reads no verdict and
changes none; whole-file targets (`--repo`, path) have no diff and skip it.
