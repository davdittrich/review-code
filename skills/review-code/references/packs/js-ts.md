# Pack: js-ts

Applies to `*.js`, `*.jsx`, `*.ts`, `*.tsx`, `*.mjs` and `package.json` changes, including front-end code. Report a check only with the evidence its category needs.

## Checks

- A list rendered with the array index as the React `key`, where the list can be reordered, filtered or inserted into. Item state attaches to the wrong row. (`bug`)
- An object, array or function literal created in render and passed as a prop to a memoised child or placed in an effect dependency list, so the child or effect runs on every render. (`bug`)
- A `useEffect`, `useMemo` or `useCallback` that reads a reactive value (prop, state, derived value) missing from its dependency list. The effect keeps a stale value. (`bug`)
- A request, query or file read inside a loop or render path, where the loop and the call both sit in the change. (`performance`)
- An `<img>` or equivalent without alternative text, or a form control without a label or accessible name. State the element and line. Low contrast counts only when literal colour values in the change give a computable ratio. (`accessibility`)
- Loose equality (`==`) or the `any` type in changed code when no linter or type checker forbids it. (`smell`)
- Styling split across inline styles, modules and global sheets with no stated rule for choosing. (`convention`)

## Do not flag

- Missing `memo`, `useMemo` or `useCallback`. Whether it matters needs profiling evidence, which a diff cannot give.
- `==` against `null` used to match both `null` and `undefined`, and `==` or `any` that a configured eslint rule or `strict` type settings already cover.
- `any` in test doubles, type-declaration shims or generated code.
- An index key on a static list that is never reordered, filtered or edited in place.
- A request in a loop that lies outside the change, or one bounded by a small fixed constant visible in the code.

## Sources

- React docs, Rendering Lists (index as key), https://react.dev/learn/rendering-lists, read 2026.
- React docs, `useEffect` (dependencies must list reactive values), https://react.dev/reference/react/useEffect, read 2026.
- WCAG 2.2 success criteria 1.1.1 (text alternatives) and 1.4.3 (contrast), https://www.w3.org/TR/WCAG22/, 2023.
- Contract rows docs/ccr-contract.md L68-L74, restated in own words.
