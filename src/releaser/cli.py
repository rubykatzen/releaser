from __future__ import annotations

import argparse
import datetime
import json
import re
import shutil
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from enum import IntEnum
from typing import Any

from releaser import __version__


class ExitCode(IntEnum):
    OK = 0
    NOT_ALLOWED = 1
    USAGE = 2
    ENVIRONMENT = 3
    COMMAND_FAILED = 4


class ReleaseError(Exception):
    exit_code = ExitCode.COMMAND_FAILED


class NotAllowedError(ReleaseError):
    exit_code = ExitCode.NOT_ALLOWED


class EnvError(ReleaseError):
    exit_code = ExitCode.ENVIRONMENT


SEMVER_TAG_RE = re.compile(
    r"^v(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)$"
)
RELEASE_TYPES = ("patch", "minor", "major")


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class CiRun:
    name: str
    status: str
    conclusion: str | None
    url: str | None


@dataclass(frozen=True)
class UnreleasedCommit:
    sha: str
    short_sha: str
    subject: str
    committed_at: str


@dataclass(frozen=True)
class ReleaseState:
    repository: str
    root: str
    remote: str
    branch: str
    head: str
    short_head: str
    latest_tag: str | None
    commits_since_release: int
    ci_status: str
    ci_runs: list[CiRun]
    required_checks: list[str] | None
    next: dict[str, str]
    release_allowed: bool
    refusal_reason: str | None


def run_interactive(cmd: list[str], *, cwd: str | None = None) -> int:
    """Run a command with stdout/stderr streamed to the terminal. Returns exit code."""
    proc = subprocess.run(cmd, cwd=cwd, check=False)
    return proc.returncode


def run(cmd: list[str], *, cwd: str | None = None, check: bool = True) -> CommandResult:
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=False,
    )
    if check and proc.returncode != 0:
        joined = " ".join(cmd)
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise ReleaseError(f"Command failed: {joined}\n{detail}")
    return CommandResult(
        returncode=proc.returncode,
        stdout=proc.stdout.strip(),
        stderr=proc.stderr.strip(),
    )


def require_executable(name: str) -> None:
    if shutil.which(name) is None:
        raise EnvError(f"Required executable is missing: {name}")


def git(args: list[str], *, cwd: str | None = None, check: bool = True) -> CommandResult:
    return run(["git", *args], cwd=cwd, check=check)


def gh(args: list[str], *, cwd: str | None = None, check: bool = True) -> CommandResult:
    return run(["gh", *args], cwd=cwd, check=check)


def repo_root() -> str:
    result = git(["rev-parse", "--show-toplevel"])
    if not result.stdout:
        raise EnvError("Current directory is not inside a git repository")
    return result.stdout


def fetch(root: str, remote: str, branch: str) -> None:
    git(["fetch", remote, branch, "--tags", "--prune"], cwd=root)


def origin_head(root: str, remote: str, branch: str) -> str:
    ref = f"refs/remotes/{remote}/{branch}"
    result = git(["rev-parse", ref], cwd=root)
    if not result.stdout:
        raise EnvError(f"Could not resolve {remote}/{branch}")
    return result.stdout


def github_repository(root: str) -> str:
    result = gh(["repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"], cwd=root)
    if not result.stdout:
        raise EnvError("Could not determine GitHub repository")
    return result.stdout


def latest_semver_tag(root: str, head: str) -> str | None:
    result = git(
        [
            "tag",
            "--list",
            "v[0-9]*.[0-9]*.[0-9]*",
            "--merged",
            head,
            "--sort=-v:refname",
        ],
        cwd=root,
    )
    for tag in result.stdout.splitlines():
        tag = tag.strip()
        if SEMVER_TAG_RE.match(tag):
            return tag
    return None


def parse_version(tag: str | None) -> tuple[int, int, int]:
    if tag is None:
        return (0, 0, 0)
    match = SEMVER_TAG_RE.match(tag)
    if not match:
        raise ReleaseError(f"Invalid SemVer tag: {tag}")
    return (int(match["major"]), int(match["minor"]), int(match["patch"]))


def bump(tag: str | None, release_type: str) -> str:
    major, minor, patch = parse_version(tag)
    if release_type == "major":
        major += 1
        minor = 0
        patch = 0
    elif release_type == "minor":
        minor += 1
        patch = 0
    elif release_type == "patch":
        patch += 1
    else:
        raise ReleaseError(f"Unknown release type: {release_type}")
    return f"v{major}.{minor}.{patch}"


def commit_range(latest_tag: str | None, head: str) -> str:
    if latest_tag is None:
        return head
    return f"{latest_tag}..{head}"


def parse_git_log_line(line: str) -> UnreleasedCommit | None:
    parts = line.split("\t", 3)
    if len(parts) != 4:
        return None
    sha, short_sha, committed_at, subject = parts
    if not sha or not short_sha:
        return None
    return UnreleasedCommit(
        sha=sha,
        short_sha=short_sha,
        subject=subject,
        committed_at=committed_at,
    )


def commits_since(root: str, latest_tag: str | None, head: str) -> int:
    result = git(["rev-list", "--count", commit_range(latest_tag, head)], cwd=root)
    return int(result.stdout)


def unreleased_commits(root: str, latest_tag: str | None, head: str) -> list[UnreleasedCommit]:
    result = git(
        [
            "log",
            commit_range(latest_tag, head),
            "--format=%H\t%h\t%ci\t%s",
            "--no-merges",
        ],
        cwd=root,
    )
    commits: list[UnreleasedCommit] = []
    for line in result.stdout.splitlines():
        commit = parse_git_log_line(line)
        if commit is not None:
            commits.append(commit)
    return commits


def tag_exists(root: str, tag: str) -> bool:
    result = git(["rev-parse", "--quiet", "--verify", f"refs/tags/{tag}"], cwd=root, check=False)
    return bool(result.stdout)


def required_check_contexts(repository: str, branch: str) -> list[str] | None:
    result = gh(
        ["api", f"repos/{repository}/branches/{branch}/protection/required_status_checks"],
        check=False,
    )
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
        checks = data.get("checks") or []
        if checks:
            return [c["context"] for c in checks if c.get("context")]
        return [c for c in data.get("contexts", []) if c]
    except (json.JSONDecodeError, KeyError):
        return None


def ci_runs(repository: str, head: str) -> list[CiRun]:
    result = gh(
        ["api", f"repos/{repository}/commits/{head}/check-runs", "--paginate",
         "--jq", ".check_runs[] | {name, status, conclusion, url: .html_url}"],
        check=False,
    )
    runs: list[CiRun] = []
    if result.returncode != 0 or not result.stdout:
        return runs
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
            runs.append(CiRun(
                name=item["name"],
                status=item["status"],
                conclusion=item.get("conclusion"),
                url=item.get("url"),
            ))
        except (json.JSONDecodeError, KeyError):
            continue
    return runs


def ci_status(runs: list[CiRun], required: list[str] | None) -> tuple[str, str | None]:
    if required is None:
        return ("unchecked", None)
    if not required:
        return ("success", None)
    by_name = {run.name: run for run in runs}
    missing, pending, failed = [], [], []
    for ctx in required:
        run = by_name.get(ctx)
        if run is None:
            missing.append(ctx)
        elif run.status != "completed":
            pending.append(ctx)
        elif run.conclusion != "success":
            failed.append(f"{ctx}={run.conclusion or 'unknown'}")
    if missing:
        return ("missing", f"Required checks not found: {', '.join(missing)}")
    if pending:
        return ("pending", f"Required checks still running: {', '.join(pending)}")
    if failed:
        return ("failed", f"Required checks did not pass: {', '.join(failed)}")
    return ("success", None)


def wait_for_run(repository: str, workflow: str, after_time: float) -> str:
    """Poll gh run list until a run for workflow created after after_time appears."""
    after_iso = datetime.datetime.utcfromtimestamp(after_time).strftime("%Y-%m-%dT%H:%M:%SZ")
    for _ in range(20):
        time.sleep(3)
        result = gh(
            ["run", "list", "--workflow", workflow, "--repo", repository,
             "--limit", "5", "--json", "databaseId,createdAt"],
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            continue
        try:
            for r in json.loads(result.stdout):
                if r.get("createdAt", "") >= after_iso:
                    return str(r["databaseId"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    raise ReleaseError(f"Could not find {workflow} run after dispatch")


def state(*, remote: str = "origin", branch: str = "main", do_fetch: bool = True) -> ReleaseState:
    require_executable("git")
    require_executable("gh")
    root = repo_root()
    if do_fetch:
        fetch(root, remote, branch)
    head = origin_head(root, remote, branch)
    short_head = git(["rev-parse", "--short", head], cwd=root).stdout
    repository = github_repository(root)
    latest_tag = latest_semver_tag(root, head)
    commit_count = commits_since(root, latest_tag, head)
    next_versions = {release_type: bump(latest_tag, release_type) for release_type in RELEASE_TYPES}
    if commit_count == 0:
        reason: str | None = f"Nothing to release: {remote}/{branch} is already tagged as {latest_tag}"
    else:
        reason = None
        for version in next_versions.values():
            if tag_exists(root, version):
                reason = f"Next release tag already exists: {version}"
                break

    required = required_check_contexts(repository, branch)
    runs = ci_runs(repository, head)
    status, ci_reason = ci_status(runs, required)
    if reason is None:
        reason = ci_reason

    return ReleaseState(
        repository=repository,
        root=root,
        remote=remote,
        branch=branch,
        head=head,
        short_head=short_head,
        latest_tag=latest_tag,
        commits_since_release=commit_count,
        ci_status=status,
        ci_runs=runs,
        required_checks=required,
        next=next_versions,
        release_allowed=reason is None,
        refusal_reason=reason,
    )


def print_state(
    state_: ReleaseState,
    *,
    verbose: bool,
    commits: list[UnreleasedCommit],
) -> None:
    latest = state_.latest_tag or "none"
    print(f"Repository: {state_.repository}")
    print(f"Branch: {state_.remote}/{state_.branch}")
    print(f"HEAD: {state_.short_head}")
    print(f"Latest release: {latest}")
    print(f"Commits since release: {state_.commits_since_release}")
    for commit in commits:
        print(f"  {commit.short_sha}  {commit.subject}  ({commit.committed_at[:10]})")
    if state_.required_checks is None:
        print("CI: unchecked (no branch protection)")
    else:
        print(f"CI: {state_.ci_status}")
    print(f"Next patch: {state_.next['patch']}")
    print(f"Next minor: {state_.next['minor']}")
    print(f"Next major: {state_.next['major']}")
    if state_.release_allowed:
        print("Release allowed: yes")
    else:
        print(f"Release allowed: no ({state_.refusal_reason})")
    if verbose:
        print()
        if state_.required_checks is None:
            print("Branch protection: none")
        elif not state_.required_checks:
            print("Branch protection: enabled, no required checks")
        else:
            by_name = {r.name: r for r in state_.ci_runs}
            print("Required checks:")
            for ctx in state_.required_checks:
                run_ = by_name.get(ctx)
                if run_ is None:
                    print(f"  - {ctx}: not found")
                else:
                    conclusion = run_.conclusion or run_.status
                    print(f"  - {ctx}: {conclusion}")


def to_json(data: Any) -> None:
    def default(value: Any) -> Any:
        if hasattr(value, "__dataclass_fields__"):
            return asdict(value)
        raise TypeError(f"Object is not JSON serializable: {value!r}")

    print(json.dumps(data, indent=2, sort_keys=True, default=default))


def command_status(args: argparse.Namespace) -> int:
    state_ = state()
    commits = unreleased_commits(state_.root, state_.latest_tag, state_.head)
    if args.json:
        payload: dict[str, Any] = asdict(state_)
        payload["unreleased_commits"] = [asdict(c) for c in commits]
        to_json(payload)
    else:
        print_state(state_, verbose=args.verbose, commits=commits)
    return int(ExitCode.OK if state_.release_allowed else ExitCode.NOT_ALLOWED)


def command_doctor(args: argparse.Namespace) -> int:
    checks: dict[str, str] = {}
    for executable in ("git", "gh"):
        checks[executable] = shutil.which(executable) or "missing"
    try:
        root = repo_root()
        checks["git_repo"] = root
    except ReleaseError as error:
        checks["git_repo"] = str(error)
    auth = gh(["auth", "status"], check=False)
    checks["gh_auth"] = "ok" if auth.returncode == 0 else (auth.stdout or auth.stderr)
    ok = (
        all(value != "missing" for value in checks.values())
        and "not inside" not in checks["git_repo"]
        and checks.get("gh_auth") == "ok"
    )
    if args.json:
        to_json({"ok": ok, "checks": checks})
    else:
        for name, value in checks.items():
            print(f"{name}: {value}")
        print(f"doctor: {'ok' if ok else 'failed'}")
    return int(ExitCode.OK if ok else ExitCode.ENVIRONMENT)


def _dispatch_and_open_pr(
    state_: ReleaseState,
    version: str,
    *,
    dry_run: bool,
    as_json: bool,
    verbose: bool = False,
) -> int:
    version_num = version.lstrip("v")
    branch = f"release/{version}"
    workflow = "prepare-release.yml"
    dispatch_args = [
        "workflow", "run", workflow,
        "--repo", state_.repository,
        "--field", f"version={version_num}",
        "--field", f"base_sha={state_.head}",
    ]

    if dry_run:
        if as_json:
            to_json({
                "dry_run": True, "version": version,
                "command": ["gh"] + dispatch_args, "state": state_,
            })
        else:
            print(
                f"Would dispatch {workflow} for {version} "
                f"from {state_.remote}/{state_.branch}@{state_.short_head}"
            )
            print("Would run:", " ".join(["gh"] + dispatch_args))
        return int(ExitCode.OK)

    dispatch_time = time.time()
    gh(dispatch_args, cwd=state_.root)
    if not as_json:
        print(f"Dispatched {workflow} for {version} — waiting for run to appear...")

    run_id = wait_for_run(state_.repository, workflow, dispatch_time)
    if not as_json:
        print(f"Watching run {run_id}...")

    watch_result = run_interactive(
        ["gh", "run", "watch", run_id, "--repo", state_.repository, "--exit-status"],
        cwd=state_.root,
    )
    if watch_result != 0:
        raise ReleaseError(f"{workflow} failed for {version} (run {run_id})")

    pr_result = gh(
        [
            "pr", "create",
            "--repo", state_.repository,
            "--title", f"chore: release {version}",
            "--head", branch,
            "--base", "main",
            "--body", f"Automated release PR for {version}.",
        ],
        cwd=state_.root,
    )
    pr_url = pr_result.stdout.strip()

    gh(
        ["pr", "merge", branch, "--repo", state_.repository, "--auto", "--squash"],
        cwd=state_.root,
    )

    if as_json:
        to_json({"version": version, "pr_url": pr_url, "state": state_})
    else:
        print(f"\nRelease PR: {pr_url}")
        print("Auto-merge enabled — CI pass → merge → publish-release creates tag + GitHub Release.")
    return int(ExitCode.OK)


def command_release(args: argparse.Namespace) -> int:
    state_ = state()
    version = state_.next[args.release_type]
    if not state_.release_allowed:
        if args.json:
            to_json({"release_allowed": False, "reason": state_.refusal_reason, "state": state_})
        else:
            print_state(
                state_,
                verbose=args.verbose,
                commits=unreleased_commits(state_.root, state_.latest_tag, state_.head),
            )
        return int(ExitCode.NOT_ALLOWED)
    return _dispatch_and_open_pr(
        state_, version, dry_run=args.dry_run, as_json=args.json, verbose=args.verbose,
    )


def command_cut(args: argparse.Namespace) -> int:
    version = args.target_version.lstrip("v")
    tag = f"v{version}"
    if not SEMVER_TAG_RE.match(tag):
        raise ReleaseError(f"Invalid semver version: {args.target_version}")
    state_ = state()
    if not state_.release_allowed:
        if args.json:
            to_json({"release_allowed": False, "reason": state_.refusal_reason, "state": state_})
        else:
            print_state(
                state_,
                verbose=args.verbose,
                commits=unreleased_commits(state_.root, state_.latest_tag, state_.head),
            )
        return int(ExitCode.NOT_ALLOWED)
    latest = state_.latest_tag
    if latest is not None:
        latest_parts = parse_version(latest)
        target_parts = parse_version(tag)
        if target_parts <= latest_parts:
            raise ReleaseError(f"Target version {tag} must be greater than latest tag {latest}")
    if tag_exists(state_.root, tag):
        raise ReleaseError(f"Tag {tag} already exists")
    return _dispatch_and_open_pr(
        state_, tag, dry_run=args.dry_run, as_json=args.json, verbose=args.verbose,
    )


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="releaser",
        description="Opinionated zero-config release gate for GitHub repositories.",
    )
    root.add_argument("-v", "--version", action="version", version=f"releaser {__version__}")
    subparsers = root.add_subparsers(dest="command", required=True)

    status = subparsers.add_parser(
        "status",
        help="Show release readiness for the current repository.",
    )
    status.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    status.add_argument("--verbose", action="store_true", help="Print detailed CI run information.")
    status.set_defaults(func=command_status)

    doctor = subparsers.add_parser("doctor", help="Check local releaser prerequisites.")
    doctor.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    doctor.set_defaults(func=command_doctor)

    cut = subparsers.add_parser("cut", help="Cut a release with an explicit version.")
    cut.add_argument("target_version", metavar="VERSION", help="Version to release (e.g. 1.2.0 or v1.2.0).")
    cut.add_argument("--dry-run", action="store_true", help="Run checks without creating a tag.")
    cut.add_argument("--json", action="store_true", help="Print machine-readable JSON output.")
    cut.add_argument("--verbose", action="store_true", help="Print detailed CI run information.")
    cut.set_defaults(func=command_cut)

    for release_type in RELEASE_TYPES:
        release_parser = subparsers.add_parser(release_type, help=f"Cut a {release_type} release.")
        release_parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Run checks without creating a tag.",
        )
        release_parser.add_argument(
            "--json",
            action="store_true",
            help="Print machine-readable JSON output.",
        )
        release_parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print detailed CI run information.",
        )
        release_parser.set_defaults(func=command_release, release_type=release_type)

    return root


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return int(args.func(args))
    except ReleaseError as error:
        print(str(error), file=sys.stderr)
        return int(error.exit_code)
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return int(ExitCode.COMMAND_FAILED)


if __name__ == "__main__":
    raise SystemExit(main())
