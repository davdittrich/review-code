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

root=$(git rev-parse --show-toplevel)
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT
mkdir "$scratch/tree" "$scratch/cfg"

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

finish
