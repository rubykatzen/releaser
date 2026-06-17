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
PREPARE_RELEASE_WORKFLOW = "prepare-release.yml"
PUBLISH_RELEASE_WORKFLOW = "publish-release.yml"
DEFAULT_CHECK_TIMEOUT = 900.0
DEFAULT_MERGE_TIMEOUT = 3600.0
DEFAULT_PUBLISH_TIMEOUT = 600.0


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


def commits_since(root: str, latest_tag: str | None, head: str) -> int:
    if latest_tag is None:
        result = git(["rev-list", "--count", head], cwd=root)
    else:
        result = git(["rev-list", "--count", f"{latest_tag}..{head}"], cwd=root)
    return int(result.stdout)


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


def check_workflow_file(check: str) -> str:
    """Map a branch-protection check name to a workflow file (zero-config convention)."""
    return f"{check}.yml"


def repo_allow_auto_merge(repository: str) -> bool:
    result = gh(
        ["api", f"repos/{repository}", "--jq", ".allow_auto_merge"],
        check=False,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


def wait_for_checks(
    repository: str,
    head: str,
    required: list[str],
    *,
    timeout: float = DEFAULT_CHECK_TIMEOUT,
    poll_interval: float = 5.0,
    verbose: bool = False,
) -> list[CiRun]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        runs = ci_runs(repository, head)
        status, reason = ci_status(runs, required)
        if status == "success":
            return runs
        if status == "failed":
            raise NotAllowedError(reason or "Required checks failed")
        if verbose and reason:
            print(f"Waiting for main CI: {reason}")
        time.sleep(poll_interval)
    raise ReleaseError(f"Timed out waiting for required checks on {head[:7]}")


def ensure_main_ci(
    repository: str,
    branch: str,
    head: str,
    required: list[str] | None,
    *,
    verbose: bool = False,
) -> None:
    if not required:
        return
    runs = ci_runs(repository, head)
    status, reason = ci_status(runs, required)
    if status == "success":
        return
    if status == "failed":
        raise NotAllowedError(reason)
    if status == "missing":
        by_name = {run.name for run in runs}
        for ctx in required:
            if ctx in by_name:
                continue
            workflow = check_workflow_file(ctx)
            if verbose:
                print(f"Dispatching {workflow} for missing check {ctx} on {branch}...")
            gh(["workflow", "run", workflow, "--repo", repository, "--ref", branch])
        time.sleep(3)
    wait_for_checks(repository, head, required, verbose=verbose)


def wait_for_run(
    repository: str,
    workflow: str,
    after_time: float,
    *,
    timeout: float = 60.0,
    poll_interval: float = 3.0,
) -> str:
    """Poll gh run list until a run for workflow created after after_time appears."""
    after_iso = datetime.datetime.utcfromtimestamp(after_time).strftime("%Y-%m-%dT%H:%M:%SZ")
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = gh(
            ["run", "list", "--workflow", workflow, "--repo", repository,
             "--limit", "5", "--json", "databaseId,createdAt"],
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            try:
                for run in json.loads(result.stdout):
                    if run.get("createdAt", "") >= after_iso:
                        return str(run["databaseId"])
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        time.sleep(poll_interval)
    raise ReleaseError(f"Could not find {workflow} run after dispatch")


def pr_number_from_url(pr_url: str) -> int:
    return int(pr_url.rstrip("/").split("/")[-1])


def wait_for_pr_checks(
    repository: str,
    pr_number: int,
    *,
    timeout: float = DEFAULT_CHECK_TIMEOUT,
    poll_interval: float = 5.0,
    verbose: bool = False,
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = gh(
            [
                "pr", "view", str(pr_number),
                "--repo", repository,
                "--json", "statusCheckRollup",
            ],
            check=False,
        )
        if result.returncode != 0 or not result.stdout:
            time.sleep(poll_interval)
            continue
        rollup = json.loads(result.stdout).get("statusCheckRollup") or []
        if not rollup:
            time.sleep(poll_interval)
            continue
        pending: list[str] = []
        failed: list[str] = []
        for check in rollup:
            name = check.get("name") or "check"
            status = check.get("status") or ""
            conclusion = check.get("conclusion") or ""
            if status != "COMPLETED":
                pending.append(name)
            elif conclusion not in ("SUCCESS", "NEUTRAL", "SKIPPED"):
                failed.append(f"{name}={conclusion}")
        if failed:
            raise ReleaseError(f"Release PR checks failed: {', '.join(failed)}")
        if not pending:
            return
        if verbose:
            print(f"Waiting for release PR checks: {', '.join(pending)}")
        time.sleep(poll_interval)
    raise ReleaseError(f"Timed out waiting for release PR #{pr_number} checks")


def wait_for_pr_merged(
    repository: str,
    pr_number: int,
    *,
    timeout: float = DEFAULT_MERGE_TIMEOUT,
    poll_interval: float = 5.0,
    verbose: bool = False,
) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = gh(
            ["pr", "view", str(pr_number), "--repo", repository, "--json", "state"],
            check=False,
        )
        if result.returncode == 0 and result.stdout:
            state_value = json.loads(result.stdout).get("state")
            if state_value == "MERGED":
                return
            if state_value == "CLOSED":
                raise ReleaseError(f"Release PR #{pr_number} was closed without merging")
        if verbose:
            print(f"Waiting for release PR #{pr_number} to merge...")
        time.sleep(poll_interval)
    raise ReleaseError(f"Timed out waiting for release PR #{pr_number} to merge")


def merge_release_pr(
    repository: str,
    pr_number: int,
    *,
    verbose: bool = False,
) -> str:
    if repo_allow_auto_merge(repository):
        result = gh(
            ["pr", "merge", str(pr_number), "--repo", repository, "--auto", "--squash"],
            check=False,
        )
        if result.returncode == 0:
            if verbose:
                print("Auto-merge enabled — waiting for CI to merge the PR...")
            return "auto"
        if verbose:
            detail = result.stderr.strip() or result.stdout.strip()
            print(f"Auto-merge unavailable ({detail}); falling back to direct merge.")
    wait_for_pr_checks(repository, pr_number, verbose=verbose)
    gh(["pr", "merge", str(pr_number), "--repo", repository, "--squash"])
    return "direct"


def release_url(repository: str, version: str) -> str:
    result = gh(
        ["release", "view", version, "--repo", repository, "--json", "url", "--jq", ".url"],
        check=False,
    )
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    return f"https://github.com/{repository}/releases/tag/{version}"


def verify_release_published(
    repository: str,
    root: str,
    remote: str,
    branch: str,
    version: str,
) -> str:
    fetch(root, remote, branch)
    if not tag_exists(root, version):
        raise ReleaseError(f"Tag {version} not found after publish-release")
    return release_url(repository, version)


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
    if reason is None and status == "failed":
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


def print_state(state_: ReleaseState, *, verbose: bool) -> None:
    latest = state_.latest_tag or "none"
    print(f"Repository: {state_.repository}")
    print(f"Branch: {state_.remote}/{state_.branch}")
    print(f"HEAD: {state_.short_head}")
    print(f"Latest release: {latest}")
    print(f"Commits since release: {state_.commits_since_release}")
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
    if args.json:
        to_json(state_)
    else:
        print_state(state_, verbose=args.verbose)
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


def run_release(
    state_: ReleaseState,
    version: str,
    *,
    dry_run: bool,
    as_json: bool,
    verbose: bool = False,
    pr_only: bool = False,
) -> int:
    version_num = version.lstrip("v")
    branch = f"release/{version}"
    dispatch_args = [
        "workflow", "run", PREPARE_RELEASE_WORKFLOW,
        "--repo", state_.repository,
        "--field", f"version={version_num}",
        "--field", f"base_sha={state_.head}",
    ]

    if dry_run:
        planned: dict[str, Any] = {
            "dry_run": True,
            "version": version,
            "pr_only": pr_only,
            "state": state_,
        }
        if state_.required_checks and state_.ci_status == "missing":
            planned["ensure_ci"] = [
                check_workflow_file(ctx) for ctx in state_.required_checks
            ]
        planned["dispatch"] = ["gh"] + dispatch_args
        if pr_only:
            planned["stop_after"] = "pr_create"
        else:
            planned["stop_after"] = "release_published"
        if as_json:
            to_json(planned)
        else:
            print(
                f"Would release {version} from "
                f"{state_.remote}/{state_.branch}@{state_.short_head}"
            )
            if state_.required_checks and state_.ci_status != "success":
                print(f"Would ensure main CI ({state_.ci_status}) before dispatch.")
            print("Would run:", " ".join(["gh"] + dispatch_args))
            print(f"Would create PR {branch} → {state_.branch}")
            if pr_only:
                print("Would stop after opening the PR (--pr-only).")
            else:
                merge_mode = "auto-merge" if repo_allow_auto_merge(state_.repository) else "direct merge"
                print(f"Would merge PR via {merge_mode}, watch publish-release, verify tag.")
        return int(ExitCode.OK)

    if state_.required_checks:
        if verbose:
            print("Ensuring required checks on main...")
        ensure_main_ci(
            state_.repository,
            state_.branch,
            state_.head,
            state_.required_checks,
            verbose=verbose,
        )
        head = origin_head(state_.root, state_.remote, state_.branch)
        dispatch_args[-1] = f"base_sha={head}"

    dispatch_time = time.time()
    gh(dispatch_args, cwd=state_.root)
    if not as_json:
        print(f"Dispatched {PREPARE_RELEASE_WORKFLOW} for {version} — waiting for run to appear...")

    prepare_run_id = wait_for_run(
        state_.repository,
        PREPARE_RELEASE_WORKFLOW,
        dispatch_time,
        timeout=DEFAULT_CHECK_TIMEOUT,
    )
    if not as_json:
        print(f"Watching prepare-release run {prepare_run_id}...")

    watch_result = run_interactive(
        ["gh", "run", "watch", prepare_run_id, "--repo", state_.repository, "--exit-status"],
        cwd=state_.root,
    )
    if watch_result != 0:
        raise ReleaseError(
            f"{PREPARE_RELEASE_WORKFLOW} failed for {version} (run {prepare_run_id})"
        )

    pr_result = gh(
        [
            "pr", "create",
            "--repo", state_.repository,
            "--title", f"chore: release {version}",
            "--head", branch,
            "--base", state_.branch,
            "--body", f"Automated release PR for {version}.",
        ],
        cwd=state_.root,
    )
    pr_url = pr_result.stdout.strip()
    pr_number = pr_number_from_url(pr_url)

    if pr_only:
        payload = {"version": version, "pr_url": pr_url, "pr_number": pr_number, "state": state_}
        if as_json:
            to_json(payload)
        else:
            print(f"\nRelease PR: {pr_url}")
            print("Stopped after opening the PR (--pr-only). Merge it manually when ready.")
        return int(ExitCode.OK)

    merge_time = time.time()
    merge_mode = merge_release_pr(state_.repository, pr_number, verbose=verbose)
    if not as_json:
        if merge_mode == "auto":
            print(f"Waiting for release PR #{pr_number} to auto-merge...")
        else:
            print(f"Merged release PR #{pr_number}; waiting for publish-release...")
    wait_for_pr_merged(state_.repository, pr_number, verbose=verbose)

    publish_run_id = wait_for_run(
        state_.repository,
        PUBLISH_RELEASE_WORKFLOW,
        merge_time,
        timeout=DEFAULT_PUBLISH_TIMEOUT,
    )
    if not as_json:
        print(f"Watching publish-release run {publish_run_id}...")

    watch_result = run_interactive(
        ["gh", "run", "watch", publish_run_id, "--repo", state_.repository, "--exit-status"],
        cwd=state_.root,
    )
    if watch_result != 0:
        raise ReleaseError(
            f"{PUBLISH_RELEASE_WORKFLOW} failed for {version} (run {publish_run_id})"
        )

    url = verify_release_published(
        state_.repository,
        state_.root,
        state_.remote,
        state_.branch,
        version,
    )
    payload = {
        "version": version,
        "pr_url": pr_url,
        "pr_number": pr_number,
        "merge_mode": merge_mode,
        "release_url": url,
        "state": state_,
    }
    if as_json:
        to_json(payload)
    else:
        print(f"\nReleased {version}: {url}")
    return int(ExitCode.OK)


def command_release(args: argparse.Namespace) -> int:
    state_ = state()
    version = state_.next[args.release_type]
    if not state_.release_allowed:
        if args.json:
            to_json({"release_allowed": False, "reason": state_.refusal_reason, "state": state_})
        else:
            print_state(state_, verbose=args.verbose)
        return int(ExitCode.NOT_ALLOWED)
    return run_release(
        state_, version, dry_run=args.dry_run, as_json=args.json,
        verbose=args.verbose, pr_only=args.pr_only,
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
            print_state(state_, verbose=args.verbose)
        return int(ExitCode.NOT_ALLOWED)
    latest = state_.latest_tag
    if latest is not None:
        latest_parts = parse_version(latest)
        target_parts = parse_version(tag)
        if target_parts <= latest_parts:
            raise ReleaseError(f"Target version {tag} must be greater than latest tag {latest}")
    if tag_exists(state_.root, tag):
        raise ReleaseError(f"Tag {tag} already exists")
    return run_release(
        state_, tag, dry_run=args.dry_run, as_json=args.json,
        verbose=args.verbose, pr_only=args.pr_only,
    )


def add_release_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run checks without creating a tag.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON output.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed CI run information.",
    )
    parser.add_argument(
        "--pr-only",
        action="store_true",
        help="Stop after opening the release PR; do not merge or publish.",
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
    add_release_arguments(cut)
    cut.set_defaults(func=command_cut)

    for release_type in RELEASE_TYPES:
        release_parser = subparsers.add_parser(release_type, help=f"Cut a {release_type} release.")
        add_release_arguments(release_parser)
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
