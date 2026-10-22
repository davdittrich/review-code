# Evals

Measures review-code recall and precision on small synthetic changes with planted defects. Paid and optional: nothing in CI or `tests/` runs it. `claude plugin eval` has no custom-code graders, so it cannot score path+line overlap; this is a thin driver around `claude -p`.

## Run

```
python3 evals/run.py lint                         # check fixtures, free
python3 evals/run.py --cheap --yes                # tier --lean, once per case, Standards defects only, indicative (never above AMBER)
python3 evals/run.py [--case NAME] [--runs 3]     # full run; prints a cost estimate and asks y/N (--yes skips; non-tty needs --yes)
python3 evals/run.py --max-cost-usd 20            # abort mid-run with exit 2 once spent
python3 evals/run.py --rescore evals/.out/<stamp> # score saved documents again, free
python3 evals/run.py --accept                     # write this run to evals/baseline.json
```

Raw documents go to `evals/.out/` (gitignored). A run without a review document is an error: excluded from n and flagged. Gate per case against `baseline.json`: exact one-sided binomial p; GREEN p >= 0.25, AMBER < 0.25, RED < 0.10; any RED exits 1. A no-defect case is RED on any counted finding. The plugin copy under test is the working tree's tracked files without `evals/` and `tests/`.

## Add a case in two minutes

Copy a file in `cases/`, edit it: `prompt`, `base_files` and `change_files` (path to text; the change commit overlays the base), `defects` (`id`, `path`, `line`, optional `end_line`, `categories`, `also` locations; `lane` = defects|standards|spec), `decoys` (lines a good reviewer must not flag), `accept` (neutral lines), optional `change_message`. No comments that leak the answer. Run `python3 evals/run.py lint`.

## Does not

Run in CI, prove recall on real code (the cases are tiny and synthetic), or score `sota` findings. Counted severities: blocking, required, suggestion.

## UNVERIFIED (offline-built)

All of it sits in `run_claude()` in `run.py`: the `claude -p` flags (`--plugin-dir`, `--output-format json`, `--permission-mode dontAsk` with `--allowedTools Read Grep Glob Agent` (least privilege: the skill pre-approves its own scripts), `--max-budget-usd`, `--no-session-persistence`; only their existence in `claude --help` was checked), that the JSON result carries `result` and `total_cost_usd`, that a natural-language prompt invokes the skill headless, and that the skill prints the `--json` document (with `schema_version` and `findings`) in the final reply. The baseline is empty until a first `--accept`. `--accept` refuses (exit 2, nothing written) when a selected case has an errored or missing run, and stores the run's `git_sha`, `claude_version` and `tiers_hash` per case, so `--accept --case X` leaves the other cases' staleness warnings intact. Lint also rejects a decoy within the scoring tolerance (2 lines) of a defect.
