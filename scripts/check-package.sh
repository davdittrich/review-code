#!/bin/sh
# Package gate for review-code. Exports the tracked tree (staged and modified tracked files,
# no untracked ones), then validates and installs it. Prints "ok - <id>" or "FAIL - <id>: <detail>"
# per check and "<n> ok, <m> FAIL" last. Exit 1 when any check failed.
# Needs: git, jq, claude, tar.
set -eu

nok=0
nfail=0
ok() { printf 'ok - %s\n' "$1"; nok=$((nok + 1)); }
bad() { printf 'FAIL - %s: %s\n' "$1" "$2"; nfail=$((nfail + 1)); }
finish() {
  printf '%s ok, %s FAIL\n' "$nok" "$nfail"
  [ "$nfail" -eq 0 ] || exit 1
  exit 0
}

for dep in git jq claude tar; do
  if ! command -v "$dep" >/dev/null 2>&1; then
    printf 'missing dependency: %s\n' "$dep"
    bad deps "$dep not on PATH"
    finish
  fi
done
ok deps

scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/cfg"

if [ "$#" -gt 0 ]; then
  # Check the given directory as-is (used by the mutation probes).
  if [ ! -f "$1/.claude-plugin/plugin.json" ]; then
    printf 'not a plugin root: %s\n' "$1"
    exit 2
  fi
  tree=$(cd "$1" && pwd)
else
  # Pin to the script's own checkout so the gate gives the same answer from any cwd.
  root=$(git -C "$(dirname "$0")/.." rev-parse --show-toplevel)
  mkdir "$scratch/tree"
  # Tracked state: a stash commit when the tree is dirty, else HEAD. The stash ref is not stored.
  ref=$(git -C "$root" stash create) || ref=""
  if ! git -C "$root" archive --format=tar -o "$scratch/tree.tar" "${ref:-HEAD}"; then
    bad export "git archive failed"
    finish
  fi
  if ! tar -x -C "$scratch/tree" -f "$scratch/tree.tar"; then
    bad export "tar extract failed"
    finish
  fi
  ok export
  tree="$scratch/tree"
fi

if (cd "$tree" && claude plugin validate --strict . >"$scratch/validate.log" 2>&1); then
  ok validate-strict
else
  bad validate-strict "$(grep -E '❯|✘' "$scratch/validate.log" | tr '\n' ';')"
fi

pj="$tree/.claude-plugin/plugin.json"
mj="$tree/.claude-plugin/marketplace.json"
if jq -e '.name == "review-code"' "$pj" >/dev/null 2>&1 &&
  jq -e '.name == "review-code"' "$mj" >/dev/null 2>&1; then
  ok manifest-name
else
  bad manifest-name 'plugin.json and marketplace.json .name must both be "review-code"'
fi
if jq -e '.plugins[0].source == "./"' "$mj" >/dev/null 2>&1; then
  ok marketplace-source
else
  bad marketplace-source 'plugins[0].source must be "./"'
fi
if jq -e '.plugins[0] | has("version") | not' "$mj" >/dev/null 2>&1; then
  ok marketplace-no-version
else
  bad marketplace-no-version 'plugins[0] must not carry a version'
fi
if jq -e '.version | test("^[0-9]+\\.[0-9]+\\.[0-9]+$")' "$pj" >/dev/null 2>&1; then
  ok semver
else
  bad semver 'plugin.json .version must be MAJOR.MINOR.PATCH'
fi

# CLAUDE_CONFIG_DIR confines install and details to the scratch dir; the real config is untouched.
cfg="$scratch/cfg"
if (cd "$tree" && CLAUDE_CONFIG_DIR="$cfg" claude plugin marketplace add "$tree" >"$scratch/add.log" 2>&1 &&
  CLAUDE_CONFIG_DIR="$cfg" claude plugin install review-code@review-code >>"$scratch/add.log" 2>&1); then
  ok install-fresh
else
  bad install-fresh "$(tail -n 2 "$scratch/add.log" | tr '\n' ';')"
fi

if CLAUDE_CONFIG_DIR="$cfg" claude plugin details review-code >"$scratch/details.log" 2>&1; then
  cat "$scratch/details.log"
  if grep -Eq '^ *Skills \([0-9]+\) +review-code( |$)' "$scratch/details.log"; then
    ok details-skill
  else
    bad details-skill 'details output does not list skill review-code under Skills'
  fi
else
  bad details-skill "$(tail -n 2 "$scratch/details.log" | tr '\n' ';')"
fi

# PKG-03: properties claude plugin validate does not check.
skill="$tree/skills/review-code/SKILL.md"
if [ ! -f "$skill" ]; then
  bad skill-file "missing $skill"
else
  fm="$scratch/frontmatter"
  if awk 'NR == 1 { if ($0 != "---") exit 1; next }
    /^---[ \t]*$/ { found = 1; exit }
    { print }
    END { if (!found) exit 1 }' "$skill" >"$fm"; then
    ok skill-frontmatter-start
    name=$(sed -n 's/^name:[ \t]*//p' "$fm" | head -n 1)
    dir=$(basename "$(dirname "$skill")")
    if [ "$name" = "$dir" ] && [ "$name" = "review-code" ]; then
      ok skill-name-dir
    else
      bad skill-name-dir "name '$name' must equal directory '$dir' and review-code"
    fi
    desc=$(awk '/^description:/ { sub(/^description:[ \t]*/, ""); if ($0 !~ /^[>|][-+]?$/) d = $0; inb = 1; next }
      inb && /^[ \t]+/ { gsub(/^[ \t]+|[ \t]+$/, ""); d = (d == "" ? $0 : d " " $0); next }
      { inb = 0 }
      END { print d }' "$fm")
    if [ "${#desc}" -ge 40 ] && [ "${#desc}" -le 1024 ]; then
      ok skill-description-length
    else
      bad skill-description-length "${#desc} characters, need 40 to 1024"
    fi
    if printf '%s\n' "$desc" | grep -qiwE 'i|you|your|my|we'; then
      bad skill-description-person "description uses a first or second person word"
    else
      ok skill-description-person
    fi
    if grep -q '^license: MIT$' "$fm"; then ok skill-license; else bad skill-license 'need "license: MIT"'; fi
    if grep -q '^compatibility:' "$fm"; then ok skill-compatibility; else bad skill-compatibility 'need a compatibility line'; fi
    if grep -Eq '^(disable-model-invocation|user-invocable):' "$fm"; then
      bad skill-invocation-keys 'auto-trigger and slash invocation must both stay enabled (D-02)'
    else
      ok skill-invocation-keys
    fi
  else
    bad skill-frontmatter-start 'line 1 must be --- with a closing ---'
  fi
  lines=$(wc -l <"$skill")
  if [ "$lines" -lt 500 ]; then ok skill-lines; else bad skill-lines "$lines lines, need below 500"; fi
  if head -c 15000 "$skill" | grep -qi 'iron law'; then
    ok skill-iron-law-window
  else
    bad skill-iron-law-window '"iron law" not in the first 15000 bytes'
  fi
  if head -c 15000 "$skill" | grep -qi 'verification gate'; then
    ok skill-gate-window
  else
    bad skill-gate-window '"verification gate" not in the first 15000 bytes'
  fi
  if grep -q '!`' "$skill"; then
    bad skill-no-injection 'dynamic shell injection sequence found'
  else
    ok skill-no-injection
  fi
fi

# PKG-02: licence and upstream attribution (D-07, D-08).
lic="$tree/LICENSE"
notice="$tree/NOTICE"
# The full MIT text must travel with copies: grant, notice-inclusion clause and warranty disclaimer.
has_mit_text() {
  grep -qF 'Permission is hereby granted, free of charge' "$1" 2>/dev/null &&
    grep -qF 'The above copyright notice and this permission notice shall be included' "$1" 2>/dev/null &&
    grep -qF 'THE SOFTWARE IS PROVIDED "AS IS"' "$1" 2>/dev/null
}
holder=$(sed -n 's/^Copyright (c) 20[0-9][0-9] //p' "$lic" 2>/dev/null | head -n 1)
if [ -f "$lic" ] && [ "$(head -n 1 "$lic")" = "MIT License" ] && [ -n "$holder" ] && has_mit_text "$lic"; then
  ok license-mit
else
  bad license-mit 'LICENSE line 1 must be "MIT License" with a "Copyright (c) YYYY holder" line and the full MIT permission, inclusion and disclaimer text'
fi
if grep -qF 'Copyright (c) 2025 Posit PBC' "$notice" 2>/dev/null; then
  ok notice-posit-copyright
else
  bad notice-posit-copyright 'NOTICE lacks the Posit PBC copyright line'
fi
if has_mit_text "$notice"; then
  ok notice-permission
else
  bad notice-permission 'NOTICE lacks the full upstream MIT text (grant, inclusion clause, disclaimer)'
fi
if grep -qF 'Garrick Aden-Buie' "$notice" 2>/dev/null && grep -qF 'Ziad Hassan' "$notice" 2>/dev/null; then
  ok notice-credits
else
  bad notice-credits 'NOTICE must credit Garrick Aden-Buie and Ziad Hassan'
fi
if grep -qF 'has not been verified' "$notice" 2>/dev/null; then
  ok notice-accepted-risk
else
  bad notice-accepted-risk 'NOTICE must record the unverified v1.3 licence (D-08)'
fi
if [ -n "$holder" ] && [ "$(jq -r '.author.name // empty' "$pj" 2>/dev/null)" = "$holder" ]; then
  ok plugin-author
else
  bad plugin-author 'plugin.json .author.name must equal the LICENSE copyright holder'
fi

# Tracked files ship to every installer (plugin source is "./"): no tooling, pointer or third-party text.
banned='^(CLAUDE|AGENTS|GEMINI)\.md$|^\.(wolf|serena|codex|opencode|cursor|agents|beads)/|^\.claude/(commands|rules|skills)/|^\.claude/CLAUDE\.md$|^\.claude/settings(\.local)?\.json$|^\.planning/(\.gsd-beads|milestone)\.lock$|^docs/(ccr|matt|ponytail|caveman|cavecrew-reviewer|gsd-code-reviewer|code-review)\.md$'
# List the tree under test (the export, or the given directory), never the caller's repo.
if tracked=$(cd "$tree" && find . -type f ! -path './.git/*' | sed 's|^\./||'); then
  hits=$(printf '%s\n' "$tracked" | grep -E "$banned" | tr '\n' ' ' || true)
  if [ -z "$hits" ]; then ok hygiene-tracked; else bad hygiene-tracked "tracked: $hits"; fi
else
  bad hygiene-tracked "listing $tree failed"
fi

finish
