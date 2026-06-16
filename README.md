# releaser

`releaser` is an opinionated zero-config release orchestrator for GitHub repositories.

It verifies that `origin/main` is releasable (new commits, CI green), then
dispatches `prepare-release.yml`, watches it run, opens a `release/vX.Y.Z` PR,
and enables auto-merge. The annotated tag and GitHub Release are created by
`publish-release.yml` once the PR merges.

## Usage

Check release readiness:

```bash
releaser status
releaser status --verbose   # includes per-check CI details
```

Cut a release (version calculated automatically):

```bash
releaser patch
releaser minor
releaser major
```

Cut a release with an explicit version:

```bash
releaser cut 1.2.3
```

Dry-run without triggering anything:

```bash
releaser patch --dry-run
```

Check local tooling:

```bash
releaser doctor
```

Machine-readable output:

```bash
releaser status --json
releaser patch --dry-run --json
```

## How it works

1. Fetches `origin/main` and finds the latest SemVer tag.
2. Checks that there are new commits since that tag.
3. Queries GitHub branch protection to find required CI checks.
4. Verifies all required checks passed for the `origin/main` SHA.
5. Dispatches `prepare-release.yml` with the computed `version` and `base_sha`.
6. Watches the workflow run; aborts if it fails.
7. Opens a PR from `release/vX.Y.Z` → `main` and enables auto-merge.
8. `publish-release.yml` fires on PR merge and creates the annotated tag and
   GitHub Release.

## Contract

- Run from inside the target git repository.
- Release source is `origin/main`, not local `HEAD`.
- Local dirty worktrees do not block releases.
- Release tags use `vMAJOR.MINOR.PATCH` and are annotated.
- GitHub Actions must have successful runs for the `origin/main` SHA.
- The repository must provide `prepare-release.yml` (triggered by workflow
  dispatch) and `publish-release.yml` (triggered by merged `release/*` PRs).
- GitHub Releases are created by `publish-release.yml`, not by the CLI.

## Releasing

`releaser` releases itself. Run from inside this repository:

```bash
releaser patch   # or: releaser minor / releaser major
```

Check readiness without triggering anything:

```bash
releaser status
releaser patch --dry-run
```

`prepare-release.yml` fires on dispatch, creates the `release/vX.Y.Z` branch,
generates AI release notes, bumps `pyproject.toml`, and opens a PR with
auto-merge enabled. `publish-release.yml` fires on PR merge and creates the
annotated tag and GitHub Release.

## Installation

```bash
brew tap rubykatzen/tap
brew trust rubykatzen/tap
brew install releaser
```

Upgrade with:

```bash
brew update && brew upgrade releaser
```

## Requirements

- `git`
- GitHub CLI (`gh`) installed and authenticated
- A GitHub repository with `origin/main`
- `prepare-release.yml` workflow (workflow dispatch, fields: `version`, `base_sha`)
- `publish-release.yml` workflow (triggers on merged `release/*` PRs)

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Release is not allowed |
| `2` | Usage error |
| `3` | Environment error |
| `4` | External command failed |
