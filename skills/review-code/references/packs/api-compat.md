# Pack: api-compat

Applies to changes in public surface files: package exports (`__init__.py`, `index.ts`, `NAMESPACE`, `package.json` `exports`), API descriptions (`openapi*`, `*.proto`) and documented command-line options or file formats. Report a check only with the evidence its category needs.

## Checks

- A public function, class, option or export removed or renamed while a caller in the ledger or in the repository still uses the old name. Name the caller, its file and line. (`bug`)
- A changed signature (parameter removed, reordered, made required, type narrowed) or a changed default, where a caller in the ledger or in the repository passes the old shape or relies on the old default. (`bug`)
- A changed wire or file format (field renamed, removed or retyped; encoding changed) that a reader in the repository or a documented client still expects in the old form. (`bug`)

## Do not flag

- Private symbols: names with a leading underscore, symbols not exported from the package, and `internal/` packages.
- Removals or renames after which a search of the repository finds no remaining caller, with the repo having no external consumers stated.
- Additive changes: a new optional parameter, a new field readers ignore, a new export.
- A breaking change that the change itself documents as a major release with a migration note.
- Compatibility guesses about callers outside the repo that the ledger or the repository cannot show. List them as unverifiable.

## Sources

- Semantic Versioning 2.0.0 (public API and breaking changes), https://semver.org/, read 2026.
