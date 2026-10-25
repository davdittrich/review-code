# 0001: `--nit` mode reports rule-cited nits, never gates

Status: accepted (2026-10-22). Ticket: review-code-ilzp. Amended by [0002](0002-nit-mode-all-lanes.md): nits come from every lane that reads repository evidence, not only the Standards lane (see 0002; implemented by #76).

## Context

Maintainers who enforce strict adherence to repo standards want the low-severity tier reported too:
Anthropic's `REVIEW.md` Nit tier, CodeRabbit `profile: assertive`, Greptile `strictness`, checkpatch
`--strict`, and the open-gsd maintainers' review workflow (nits are reported and must be resolved).
The Standards lane today emits style findings only as Required or Suggestion, and only when a linter
would not catch them. No flag lowers the floor.

## Decision

`--nit` is one boolean flag, orthogonal to the tier flags. `--nit --lean` is the nits-only pass.

- A fifth severity, `nit`, ranks between `suggestion` and `noted`. It is emitted only under `--nit`.
  Without the flag the lane behaves as before and no existing finding is reclassified.
- A nit has a basis, and both bases are checked by the skill, not by the model:
  - `rule`: category `convention`, severity `nit`. The rule text must occur in a `conventions/` copy
    (the existing `_rule` check). Rule files are read from the merge base, so a PR cannot rewrite
    the rules it is judged by.
  - `norm`: category `norm`. The lane names two literal idioms and a file glob. The skill counts
    lines in the pinned tree (amended by #52: `tree/` until then); the finding stands only with at least 20 observations, the
    deviating idiom in the quote, and the other idiom the majority. The counts are printed.
    No rule and no counted norm means no finding.
- Nits skip what a linter or CI enforces. The skill never runs a linter.
- Diff targets: changed lines only (the pre-existing drop applies). Repo targets: pre-existing
  violations are reported.
- Report only: the verdict is unchanged, the summary shows the nit count, `--fix` ignores nits.
  The rendered Nits section shows at most 15 groups (0002: a Defects or Spec nit's group is its category), each once with its count.
- The Standards lane ceiling rises from 40 to 60 turns (frontmatter only, so it applies to all tiers).
- codegraph and serena stay optional locators with a Grep fallback. Nothing is required.

## Alternatives considered

- A new `nit` lane: duplicates the Standards lane's rule reading and adds a verifier path. Rejected.
- Verifier re-runs the norm count: the verifier has no shell, and a model count is weaker than a
  script count. The ingest script counts instead.
- Gating on nits (zero tolerance): a human-maintainer policy. A review here never approves or blocks.
- Regular expressions for norms: a model-written pattern over repo text risks catastrophic
  backtracking. Literal strings only; upgrade if lanes need patterns.

## Consequences

Norm nits need 20 observations among the pinned tree's files matching the glob (#52; before it, `tree/`, which
held only the changed files and capped helpers on a diff review).

Norm counts read the pinned tree, which holds head content, so a PR author can add or dilute lines of an idiom. That moves
only nit severity, which never changes the verdict; rule nits come from merge-base files and have no such path.
`.okf/` and `docs/adr` at the merge base become rule sources; a previously merged injection there could steer the
Standards lane, again only at nit severity, and a rule must still occur verbatim in the file.

`--nit --lean` is nits-only: the plan adds that sentence to the lane input and `report.py` refuses any other severity
(`nit-only`).

Not built yet: PR hygiene and co-change nits, and the scope-discipline nit (both approved in the design). They need a commit and PR metadata input that a run does not carry today, so their timing is the owner's decision.

## Credit

The nit definition (a deviation from a measured norm, stated with a count, never blocking on its own)
and the deterministic-versus-judgment split follow the open-gsd maintainers' public review workflow
(`skills-from-the-artificer`, MIT). Wording here is re-derived.
