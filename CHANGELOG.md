# Changelog

## [v0.1.0] - 2026-06-14

The release mechanism is now CI-owned: `releaser patch|minor|major` dispatches a `release.yml` workflow via `gh workflow run` instead of pushing a tag directly. The release workflow uses the shared `release-shared.yml` reusable workflow from baseline, which verifies the base SHA, checks CI, generates AI release notes, commits the changelog, and creates the tag and GitHub Release.

## [v0.0.6] - 2026-06-13

- switch release workflow to baseline create-release action
- normalize baseline refs to v0.0.12
- add pre-commit autoupdate workflow, normalize schedules to Berlin time
- pin baseline actions to v0.0.10
- rename workflows to dependabot-automerge and telegram-release-notify
- bump baseline to v0.0.8
- rename workflows to merge-dependabot and notify-telegram
- add dependabot, auto-merge, and telegram notify workflows
- quote GITHUB_REF_NAME to fix shellcheck SC2086
- switch lint workflow to baseline actions
