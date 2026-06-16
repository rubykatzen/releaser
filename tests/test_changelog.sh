#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck source=../.github/actions/lib/changelog.sh
source "$ROOT/.github/actions/lib/changelog.sh"

assert_eq() {
  local expected="$1"
  local actual="$2"
  local label="$3"
  if [ "$expected" != "$actual" ]; then
    printf 'FAIL %s\nexpected:\n%s\nactual:\n%s\n' "$label" "$expected" "$actual" >&2
    exit 1
  fi
}

sample_changelog() {
  cat <<'EOF'
# Changelog

## [Unreleased]

- feat: publish baseline Ruby gem
- fix: silence RuboCop tips

## [v0.4.10] - 2026-06-16

- feat: inline standard rubocop configs
EOF
}

tmp=$(mktemp)
trap 'rm -f "$tmp"' EXIT
sample_changelog > "$tmp"

assert_eq $'- feat: publish baseline Ruby gem\n- fix: silence RuboCop tips' \
  "$(extract_unreleased_content "$tmp")" \
  "extract_unreleased_content"

assert_eq $'## [v0.4.10] - 2026-06-16

- feat: inline standard rubocop configs' \
  "$(tail -n +3 "$tmp" | strip_unreleased_section)" \
  "strip_unreleased_section"

new_entry="## [v0.5.0] - 2026-06-16

Release summary prose."
rest=$(tail -n +3 "$tmp" | strip_unreleased_section)
result="# Changelog

## [Unreleased]

${new_entry}

${rest}"

assert_eq $'# Changelog

## [Unreleased]

## [v0.5.0] - 2026-06-16

Release summary prose.

## [v0.4.10] - 2026-06-16

- feat: inline standard rubocop configs' \
  "$result" \
  "promoted changelog layout"

printf 'OK changelog helpers\n'
