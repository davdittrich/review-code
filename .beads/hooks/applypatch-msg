#!/usr/bin/env bash
# Global git hook dispatcher.
#  - commit-msg: strip AI-attribution trailers (per ~/.claude/CLAUDE.md), overriding
#    any harness/tool default that injects a 'Co-Authored-By: Claude ...' line.
#  - all hooks: chain to the repo-local .git/hooks/<name> so setting core.hooksPath
#    globally does NOT disable per-repo hooks.
hook="$(basename "$0")"
gitdir="$(git rev-parse --git-dir 2>/dev/null)"
local_hook="$gitdir/hooks/$hook"

if [ "$hook" = "commit-msg" ]; then
  python3 - "$1" <<'PY'
import re, sys
p = sys.argv[1]
L = open(p, encoding='utf-8').read().splitlines(keepends=True)
pat = re.compile(
    r'(^\s*co-authored-by:\s*.*(claude|anthropic|noreply@anthropic).*$)'
    r'|(generated with\s*\[?\s*claude)'
    r'|(^\s*\U0001F916\s*generated with)',
    re.IGNORECASE)
K = [x for x in L if not pat.search(x)]
while len(K) > 1 and K[-1].strip() == '':
    K.pop()
if K and not K[-1].endswith('\n'):
    K[-1] += '\n'
if K != L:
    open(p, 'w', encoding='utf-8').writelines(K)
PY
fi

if [ -x "$local_hook" ] && [ "$(readlink -f "$local_hook")" != "$(readlink -f "$0")" ]; then
  exec "$local_hook" "$@"
fi
exit 0
