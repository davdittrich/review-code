# Pack: llm-generated-code

Loaded when the user asks for extra scrutiny of machine-written code or the review runs at the deepest tier. Authorship cannot be known from a diff, so none of these checks assumes it. Treat each as a place to look and report only what a search or trace shows.

## Checks

- A dependency imported or added that is absent from the manifest, or is a different package from the one the code means (a near-identical name). Check the manifest, the lock file and the import. (`bug`)
- A call to a function, method or option that does not exist in the installed version found in the repository or a vendored copy. Quote the call and the signature actually present. (`bug`)
- Inconsistency across changed files: a name, argument order, default or error convention that one changed file uses differently from another. Name both sites. (`bug`)
- A test that asserts nothing, asserts a constant, or only that the code runs, so it cannot fail when the code is wrong. (`test`)
- A diff that follows its plan or description step by step. This is a prompt to trace the paths the plan does not mention, never a finding by itself. Report what the trace finds. (`bug`)

## Do not flag

- Comment or naming style that merely looks machine-written. Style alone says nothing about correctness.
- A dependency or API that a search of the manifest, lock file and repository confirms exists in the used version.
- A plan-mirroring diff on its own, with no unplanned path traced to a defect.
- Test assertions that are narrow but real, such as a single exact-value check.
- Claims about who or what wrote the code.

## Sources

- Spracklen et al., "We Have a Package for You! A Comprehensive Analysis of Package Hallucinations by Code Generating LLMs", arXiv 2406.10279, 2024, https://arxiv.org/abs/2406.10279, abstract read 2026.
- Contract rows docs/ccr-contract.md L42 and L44 to L46 (authorship is not knowable from a diff).
