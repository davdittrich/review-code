# Pack: test-quality

Applies to changes that add or edit test files. Severity is `suggestion` unless the repo documents a rule for the point (a testing standard, a CONTRIBUTING section, a lint rule). Then the Standards lane cites that rule as a convention finding. Report a check only with the trigger it names.

## Checks

- A numeric limit, threshold, size or count in the changed code (`if n > MAX`, `len(x) >= 8`, a page size, a retry cap) whose tests use only values far from it. Name the limit and the values tested; the boundary wants `limit-1`, `limit` and `limit+1`. (`test`)
- An assertion that cannot fail: `assert True`, a value compared with itself or with an identical literal, an expected value computed by the code under test, or a test body with no assertion at all. Quote the line. (`test`)
- An exception caught and ignored inside a test (`except: pass`, an empty `catch`), so the test passes whether or not the code throws. Quote the handler. (`test`)
- An assertion on wall-clock or elapsed time (`elapsed < 0.5`), or a fixed sleep used to wait for a result, in a test that has no injectable clock or event to wait on. Name the value that makes it flaky. (`test`)
- A subprocess or network call in a test with no timeout, so a hang blocks the whole suite. Quote the call. (`test`)
- New code that parses or validates input (a parser, a form handler, a request body, a file reader) whose changed tests cover only the happy path. Name the missing case: empty, malformed, hostile, oversized or duplicate input. (`test`)
- A test that reads a source file as text and greps or regex-matches it, instead of calling the code and checking its behaviour. Quote the read. (`test`)

## Do not flag

- Property-based or fuzz tests, which pick boundaries and malformed inputs by generation.
- A deliberate retry, polling or timeout test whose wait is bounded and named, or a test that fakes the clock.
- A self-comparison that tests equality, hashing or sorting itself (`assert a == a`), or a no-assertion test that only checks the call does not raise and says so.
- A limit the tests reach through a named constant or parametrised table that includes its neighbours.
- A source-text test that guards a documented, text-level contract (a generated file, a changelog format, a licence header) and says so.
- Any point where the repo's own test rules allow the pattern.

## Sources

- skills-from-the-artificer (MIT), commands/review-open-prs.md, test-review checks, https://github.com/The-Artificer-of-Ciphers-LLC/skills-from-the-artificer/blob/main/commands/review-open-prs.md.
- gsd-core CONTRIBUTING.md, testing standards, https://github.com/open-gsd/gsd-core/blob/next/CONTRIBUTING.md.
- Wording is re-derived here; neither source text is copied.
