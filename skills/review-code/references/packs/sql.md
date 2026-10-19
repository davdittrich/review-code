# Pack: sql

Applies to `*.sql` files and to ORM or raw query call sites. Report a check only with the evidence its category needs.

## Checks

- A query built by string interpolation or concatenation where a path runs from untrusted input (request field, file content, message) to the query text. Name the source, each hop and the sink. Parameter binding is the fix. (`security`)
- A related record loaded on first access inside a loop in changed code (one query per item, the ORM's deferred-load default). Name the loop, the access and the eager-loading or join alternative. (`performance`)
- A query with no `LIMIT` or pagination on a table the code treats as growing, in a request path in the change. At most `inferred` evidence. (`performance`)
- A filter or join column in a new query with no matching index in the schema files in tree/. At most `inferred` evidence, because a diff does not show table size or the query plan. (`performance`)

## Do not flag

- Interpolation of identifiers or fragments taken from constants in the code (a table name from an enum), with no untrusted input on the path.
- Raw SQL that uses parameter binding, prepared statements or the ORM's bound-value API.
- A missing index or unbounded query claim that cites neither a query plan nor a table size, and any claim about a table the same migration creates.
- Unbounded or per-row queries in one-off scripts, seeders, tests and fixtures.
- Query style (keyword case, alias names, formatting) that the repo's SQL formatter or linter covers.

## Sources

- OWASP Cheat Sheet Series, SQL Injection Prevention (parameterised queries), https://cheatsheetseries.owasp.org/cheatsheets/SQL_Injection_Prevention_Cheat_Sheet.html, read 2026.
- OWASP Top 10:2025, A05 Injection, https://owasp.org/Top10/2025/, read 2026.
- Contract rows docs/ccr-contract.md L76-L79, restated in own words.
