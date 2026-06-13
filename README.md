# releaser

`releaser` is an opinionated zero-config release gate for GitHub repositories.

It cuts annotated SemVer tags from `origin/main` only when the repository has
new commits since the latest release tag and GitHub Actions checks for that
exact `origin/main` commit are green.

## Contract

- Run from inside the target git repository.
- Release source is `origin/main`, not local `HEAD`.
- Local dirty worktrees do not block releases.
- Release tags use `vMAJOR.MINOR.PATCH`.
- Tags are annotated.
- GitHub Actions must have successful runs for the `origin/main` SHA.
- GitHub Releases and artifacts are created by repository-owned tag workflows.

## Usage

```bash
releaser status
releaser patch
releaser minor
releaser major
```

Dry-run a release:

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

## Installation

Homebrew installation is available through the rubykatzen tap:

```bash
brew tap rubykatzen/tap
brew trust rubykatzen/tap
brew install releaser
```

Upgrade with:

```bash
brew update
brew upgrade releaser
```

## Requirements

- `git`
- GitHub CLI (`gh`) installed and authenticated
- A GitHub repository with `origin/main`
- GitHub Actions workflows that run on `main`

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Release is not allowed |
| `2` | Usage error |
| `3` | Environment error |
| `4` | External command failed |
