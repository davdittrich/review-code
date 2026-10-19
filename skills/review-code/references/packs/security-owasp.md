# Pack: security-owasp

Applies to changes whose paths or content touch authentication, sessions, tokens, cryptography, uploads, queries or command execution. Checks are grouped by OWASP Top 10:2025 category name. Every finding here needs a traced path from an untrusted source to the sink: name the source, each hop and the sink, or do not report it.

## Checks

- A01 Broken Access Control: an endpoint, handler or file read that serves a record or path chosen by the caller without an ownership or role check on the path. Includes server-side requests to a caller-chosen address. (`security`)
- A02 Security Misconfiguration: a change that turns off a protection by default (debug mode, permissive cross-origin policy, disabled certificate verification) on a path reachable in production. (`security`)
- A03 Software Supply Chain Failures: a new dependency, script or build step fetched from an unpinned or unexpected source, or executed from untrusted input. (`security`)
- A04 Cryptographic Failures: a secret, key or password stored or sent unprotected, a weak or home-made primitive guarding real data, or a fixed or predictable value where randomness is required. (`security`)
- A05 Injection: untrusted input reaching a query, shell command, template, `eval`, `pickle` load or HTML sink (`innerHTML`) without binding or escaping. (`security`)
- A06 Insecure Design: a flow that lets a caller skip a required step (payment, verification, ownership) because the order is enforced only on the client. (`security`)
- A07 Authentication Failures: a login, session or token path that accepts a missing, expired or unsigned credential, or fixes a session across login. (`security`)
- A08 Software or Data Integrity Failures: data deserialised or code updated from a source whose integrity is not checked. (`security`)
- A09 Security Logging and Alerting Failures: a security-relevant failure (denied access, failed login) that the changed code swallows or logs without the actor. (`security`)
- A10 Mishandling of Exceptional Conditions: an error path that fails open (grants access, skips a check, leaks internals) when a call throws or returns an unexpected value. (`security`)

## Do not flag

- Denial of service and resource exhaustion (large inputs, regex backtracking, memory growth) and missing rate limits.
- Generic missing input validation where no path from the input to a dangerous sink is shown.
- An open redirect with no traced impact (token leak, phishing of a logged-in session).
- Theoretical issues in tests, fixtures, examples and documentation, and secrets that are clearly placeholders.
- Findings that rest on speculation about deployment, the framework version or unseen code. List them as unverifiable instead.

## Sources

- OWASP Top 10:2025 (category names A01 to A10), https://owasp.org/Top10/2025/, 2025, read 2026.
- The do-not-flag idea follows the exclusion list of anthropics/claude-code-security-review (MIT), https://github.com/anthropics/claude-code-security-review, wording written here.
- Contract rows docs/ccr-contract.md L40 and L77.
