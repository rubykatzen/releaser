#!/usr/bin/env bash
# Shared helpers for Keep a Changelog-style [Unreleased] sections.

extract_unreleased_content() {
  local file="${1:-CHANGELOG.md}"
  [ -f "$file" ] || return 0

  awk '
    /^## \[Unreleased\][[:space:]]*$/ {
      in_unreleased = 1
      next
    }
    in_unreleased && /^## \[/ {
      exit
    }
    in_unreleased {
      lines[++n] = $0
    }
    END {
      first = 0
      last = 0
      for (i = 1; i <= n; i++) {
        if (lines[i] ~ /[^[:space:]]/) {
          if (!first) first = i
          last = i
        }
      }
      if (first) {
        for (i = first; i <= last; i++) {
          print lines[i]
        }
      }
    }
  ' "$file"
}

strip_unreleased_section() {
  awk '
    /^## \[Unreleased\][[:space:]]*$/ {
      skip = 1
      next
    }
    skip && /^## \[/ {
      skip = 0
    }
    !skip {
      print
    }
  '
}
