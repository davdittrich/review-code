# Pack: r

Applies to `*.R`, `*.r`, `*.Rmd`, `DESCRIPTION` and `NAMESPACE` changes. Each check names a place to look; report it only with the evidence its category needs.

## Checks

- Partial argument matching: a call that names an argument by a prefix of its formal name, or `x$na` reading `x$name`. A later rename or a new sibling argument changes which one matches. (`bug`)
- `as.integer(f)` or `as.numeric(f)` on a factor. The result is the internal level codes, not the labels. Use `as.numeric(levels(f))[f]` for numeric labels, or `as.character(f)` first. (`bug`)
- `rbind()` of data frames whose columns differ in order or name. Data frames bind by column name, not position, and column classes come from the first frame, so a reordered frame is silently realigned and a differing first-frame class sets the result class. Check each call site's frames. (`bug`)
- `NaN` entering `mean`, `sum`, `max` and similar aggregates. `NaN` is treated as missing, so `na.rm = TRUE` drops it, but without it one `NaN` makes the whole result `NaN`. Convert to `NA` or drop explicitly, and say which the code does. (`bug`)
- `T` or `F` used for `TRUE` or `FALSE` when no linter is configured. Both can be reassigned. (`convention`)
- An explicit loop over a vector or rows in the changed lines where a vectorised or `*apply` form does the same work without carried state. Performance claims need evidence, so this stays a Suggestion. (`lean`)
- The same `.Call()` or other foreign-function call repeated across branches in the change. Hoist the shared call. (`lean`)
- The same compound condition (`!ci && cond_a`) written more than once in changed lines. Bind it once. (`lean`)

## Do not flag

- `T` or `F` when lintr (or another configured linter) already reports it.
- A loop whose iterations depend on earlier results, perform side effects (I/O, assignment into an outer environment), or iterate over a short fixed set.
- `rbind()` where every input is built in the same change with the same names in the same order.
- `as.integer()` on a factor when the code, a comment or the variable name shows level codes are intended.
- A `NaN` that the changed code cannot reach, or one already removed by an explicit `na.rm = TRUE` or `is.na()` filter.

## Sources

- R base, `cbind`/`rbind` data frame methods (match by name, classes from the first frame), https://stat.ethz.ch/R-manual/R-devel/library/base/html/cbind.html, read 2026.
- R base, `factor` (`as.integer` returns internal codes; `as.numeric(levels(f))[f]`), https://stat.ethz.ch/R-manual/R-devel/library/base/html/factor.html, read 2026.
- R base, `mean` and `NA` (`na.rm`, `NaN` treated as missing), https://stat.ethz.ch/R-manual/R-devel/library/base/html/mean.html, read 2026.
- R language definition, argument matching and `$` partial matching, https://cran.r-project.org/doc/manuals/r-release/R-lang.html, read 2026.
- Contract rows docs/ccr-contract.md L58-L66, restated in own words.
