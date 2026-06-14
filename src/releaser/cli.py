from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
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
    required = required_check_contexts(repository, branch)
    runs = ci_runs(repository, head)
    status, reason = ci_status(runs, required)

    if reason is None and commit_count == 0:
        reason = f"Nothing to release: {remote}/{branch} is already tagged as {latest_tag}"
    if reason is None:
        for version in next_versions.values():
            if tag_exists(root, version):
                reason = f"Next release tag already exists: {version}"
                break

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


def command_release(args: argparse.Namespace) -> int:
    state_ = state()
    version = state_.next[args.release_type]
    if not state_.release_allowed:
        if args.json:
            to_json({"release_allowed": False, "reason": state_.refusal_reason, "state": state_})
        else:
            print_state(state_, verbose=args.verbose)
        return int(ExitCode.NOT_ALLOWED)

    version_num = version.lstrip("v")
    dispatch_command = [
        "gh", "workflow", "run", "release.yml",
        "--repo", state_.repository,
        "--field", f"version={version_num}",
        "--field", f"base_sha={state_.head}",
    ]
    if args.dry_run:
        if args.json:
            to_json({"dry_run": True, "version": version, "command": dispatch_command, "state": state_})
        else:
            print(
                f"Would dispatch release {version} "
                f"from {state_.remote}/{state_.branch}@{state_.short_head}"
            )
            print("Would run:", " ".join(dispatch_command))
        return int(ExitCode.OK)

    gh(dispatch_command[1:], cwd=state_.root)
    if args.json:
        to_json({"version": version, "dispatched": True, "state": state_})
    else:
        print(f"Dispatched release {version} from {state_.remote}/{state_.branch}@{state_.short_head}")
        print(f"Workflow: https://github.com/{state_.repository}/actions")
    return int(ExitCode.OK)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="releaser",
        description="Opinionated zero-config release gate for GitHub repositories.",
    )
    root.add_argument("--version", action="version", version=f"releaser {__version__}")
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
