# Pack: data-migrations

Applies to files under `migrations/`, `alembic/` and similar, and to `*.sql` files that contain DDL. Report a check only with the evidence its category needs.

## Checks

- Destructive or irreversible DDL (`DROP TABLE`, `DROP COLUMN`, `TRUNCATE`, a type change that loses precision) with no backup step and no working down step. Quote the statement and say what data it removes. (`data-loss`)
- A column or table dropped or renamed while code in tree/ still reads or writes it. Name the reader, its file and line. (`bug`)
- A non-null column added to a table that already holds rows, with no default and no backfill step in the same migration. The migration fails or leaves invalid rows. (`bug`)
- A statement that takes a long lock on a table the code treats as large (an index build without a concurrent option, a rewrite of every row, a constraint added with a full scan). Name the engine rule that produces the lock. (`bug`)

## Do not flag

- Migrations that are reversible by design: a working down step, or a step that only adds and is dropped by the matching down step.
- Purely additive changes: a new table, or a new nullable column or index on a table the same migration creates.
- A drop of a column or table that the diff shows no code in tree/ reads, after a search for its name.
- Lock-time concerns for tables shown to be small (seed or lookup tables) or created in the same migration.
- Destructive statements in test fixtures, local-only seed scripts and migrations already released and applied (editing history is a separate finding).

## Sources

- PostgreSQL docs, ALTER TABLE (most forms take an ACCESS EXCLUSIVE lock; DROP COLUMN), https://www.postgresql.org/docs/current/sql-altertable.html, read 2026. Other engines have their own lock rules; state which engine applies.
- Pack scope and checks follow the phase 4 research plan for LANE-06 (data-migrations), own wording.
