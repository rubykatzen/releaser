# Changelog

## [Unreleased]

## [v0.4.2] - 2026-06-19

- feat: filter Dependabot auto-merge by update type (#38)
- fix: disable Telegram link previews and remove @ from GitHub usernames (#37)
- chore(deps): bump https://github.com/rubykatzen/baseline
- chore(deps): bump rubykatzen/baseline from 0.5.1 to 0.5.3

## [v0.4.1] - 2026-06-19


- feat: include CHANGELOG `[Unreleased]` draft notes in AI release summarization
- feat: replace `[Unreleased]` section with empty stub when cutting a release

## [v0.4.0] - 2026-06-19

- feat: close the release loop in releaser CLI (#22)
- feat: list unreleased commits in status output (#26)
- docs: add portable agent message prefix (#27)
- chore(deps): bump actions/checkout from 6 to 7
- chore(deps): bump https://github.com/rubykatzen/baseline
- chore(deps): bump rubykatzen/baseline from 0.5.0 to 0.5.1
- feat: add Telegram workflows for open PR digest and events (#18)
- refactor: rename shared workflows to verb-noun pattern (#17)
- chore(deps): bump rubykatzen/baseline from 0.4.10 to 0.5.0
- chore(deps): bump https://github.com/rubykatzen/baseline

## [v0.3.4] - 2026-06-17

- ci: add workflow_dispatch to lint workflow
- chore: re-run lint on main
- fix: skip commit in bump-ruby-gem-version when version already matches (#19)
- chore(deps): bump https://github.com/rubykatzen/baseline
- chore: replace pre-commit autoupdate workflow with Dependabot (#16)

## [v0.3.3] - 2026-06-16

- docs: add release process documentation for humans and agents
- feat: add bump-ruby-gem-version action (#13)
- chore(deps): bump rubykatzen/baseline/.github/workflows/pre-commit-autoupdate-shared.yml
- chore(deps): bump rubykatzen/baseline from 0.4.3 to 0.4.10

## [v0.3.2] - 2026-06-15

- chore(deps): bump rubykatzen/baseline/.github/workflows/pre-commit-autoupdate-shared.yml (#7)
- chore(deps): bump rubykatzen/baseline from 0.0.12 to 0.4.3 (#6)
- fix: add --auto flag to dependabot automerge to wait for required checks (#9)
- docs: fix README inaccuracies and add AGENTS.md (#8)

## [v0.3.1] - 2026-06-15

- feat: move telegram-release-notify-shared and dependabot-automerge-shared from baseline

## [v0.3.0] - 2026-06-15

- feat: move release actions from baseline into releaser

## [v0.2.3] - 2026-06-15

- fix: pass pull_request.head.ref via env to avoid actionlint warning
- feat: PR-based release flow
- feat: add cut subcommand for explicit version release
- feat: add -v alias for --version
- fix: check release preconditions before CI gate

## [v0.2.2] - 2026-06-14

- chore: update baseline ref to v0.2.3
- refactor: add bump-pyproject-version step to release

## [v0.2.1] - 2026-06-14

- chore: update baseline actions ref to v0.2.2
- feat: use branch protection required checks for CI gate
- fix: add actions: read permission to release job

## [v0.2.0] - 2026-06-14

Updated release workflow to use composable baseline actions (`verify-release`, `generate-notes`, `commit-changelog`, `create-release`) at `baseline@v0.2.0` instead of the monolithic `release-shared.yml` reusable workflow.

## [v0.1.1] - 2026-06-14

Switched from `hatch-vcs` dynamic versioning to static versioning in `pyproject.toml`, fixing Homebrew installation from GitHub source tarballs. Updated baseline ref to `v0.1.1`.

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
