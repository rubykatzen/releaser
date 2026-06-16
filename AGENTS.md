# AGENTS.md

This file provides guidance to AI coding agents when working with this repository.

## Purpose

`releaser` is a CLI tool that orchestrates a PR-based release flow for GitHub
repositories. It is the release engine used by all `rubykatzen` repos.

## Repository Structure

- `src/releaser/cli.py` — all CLI logic: state computation, version bumping,
  workflow dispatch, PR creation
- `src/releaser/__init__.py` — package version
- `src/releaser/__main__.py` — entry point
- `tests/test_versions.py` — unit tests for version bump logic
- `.github/actions/` — composite actions used by consuming repos:
  - `verify-release` — checks `base_sha` still matches `origin/main` and lint is green
  - `generate-notes` — generates release notes via GitHub Models API (gpt-4o-mini) from commit messages; uses the GitHub token, no separate API key required
  - `prepare-release-branch` — creates the `release/vX.Y.Z` branch
  - `update-changelog` — prepends the new entry to `CHANGELOG.md`
  - `bump-pyproject-version` — bumps `version` in `pyproject.toml`
  - `bump-ruby-gem-version` — bumps `VERSION` constant in `lib/**/version.rb`; silently skips if no such file exists
  - `push-release-branch` — pushes the release branch to origin
  - `read-release-data` — extracts version and notes from the merged branch
  - `create-release` — creates the annotated tag and GitHub Release
  - `send-telegram-message` — sends a Telegram message; no-op when text is empty
  - `format-telegram-unreleased-message` — builds unreleased/CI alert text
  - `format-telegram-open-pr-digest` — builds open PR digest text
  - `format-telegram-pr-opened-message` — builds PR opened/reopened alert text
- `.github/workflows/prepare-release.yml` — releaser's own release preparation
- `.github/workflows/publish-release.yml` — releaser's own tag and release creation
- `.github/workflows/merge-dependabot-pr-shared.yml` — **reusable**: merges
  Dependabot PRs immediately; consumed by other repos
- `.github/workflows/notify-telegram-unreleased-shared.yml` — **reusable**: checks
  main CI + unreleased commits, sends Telegram notification; consumed by other repos
- `.github/workflows/notify-telegram-open-pr-shared.yml` — **reusable**: daily digest
  of open non-draft PRs with links and age; consumed by other repos
- `.github/workflows/notify-telegram-pr-opened-shared.yml` — **reusable**: notifies
  Telegram when a non-draft PR is opened, reopened, or marked ready for review;
  consumed by other repos

## Full Release Flow

When a user runs `releaser patch` (or `minor`, `major`, `cut VERSION`):

1. **State check** (`state()` in `cli.py`):
   - `git fetch origin main --tags --prune`
   - Resolve `origin/main` SHA
   - Find latest SemVer tag merged into HEAD
   - Count commits since that tag
   - Query GitHub branch protection for required CI checks
   - Verify all required checks passed on `origin/main`
   - Calculate next version; reject if tag already exists or CI not green

2. **Dispatch** (`_dispatch_and_open_pr()`):
   - `gh workflow run prepare-release.yml --field version=X.Y.Z --field base_sha=SHA`
   - Polls `gh run list` every 3 s (up to 20 attempts) until the new run appears
   - `gh run watch RUN_ID --exit-status` — streams output, aborts on failure

3. **PR** (after workflow succeeds):
   - `gh pr create --head release/vX.Y.Z --base main --title "chore: release vX.Y.Z"`
   - `gh pr merge release/vX.Y.Z --auto --squash`

4. **Publish** (automated, triggered by PR merge):
   - `publish-release.yml` extracts version + notes from the merged branch
   - Creates annotated tag: `git tag -a vX.Y.Z`
   - `gh release create vX.Y.Z`

## Cutting a Release of Releaser Itself

Use the CLI on itself from inside this repository:

```bash
releaser status           # verify readiness
releaser patch --dry-run  # confirm what would be dispatched
releaser patch            # cut the release
```

Choose `minor` or `major` when the changes warrant it.

## Adding a New Command

1. Add a subparser in `parser()` with `set_defaults(func=command_<name>)`.
2. Implement `command_<name>(args)` returning an `ExitCode`.
3. If the command performs external calls, follow the `run()` / `gh()` / `git()`
   pattern — do not call `subprocess` directly.
4. Add `--json` and `--dry-run` flags if the command is stateful.
5. Update `README.md` Usage section.
6. Add tests to `tests/` if the logic is non-trivial.

## Reusable Workflows for Consumers

| Workflow | Purpose |
|---|---|
| `merge-dependabot-pr-shared.yml` | Merge Dependabot PRs immediately |
| `notify-telegram-unreleased-shared.yml` | Notify Telegram when main is broken or has unreleased commits |
| `notify-telegram-open-pr-shared.yml` | Daily digest of open non-draft PRs |
| `notify-telegram-pr-opened-shared.yml` | Notify Telegram when a PR is opened, reopened, or ready for review |

Consumer repos call them as:
```yaml
uses: rubykatzen/releaser/.github/workflows/notify-telegram-open-pr-shared.yml@vX
```

Secrets required: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (same as other Telegram workflows).
