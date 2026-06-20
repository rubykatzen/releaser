import json

from releaser.cli import (
    CiRun,
    CommandResult,
    check_workflow_file,
    ci_status,
    merge_release_pr,
    pr_number_from_url,
    wait_for_pr_checks,
    wait_for_run,
)


def test_check_workflow_file_convention() -> None:
    assert check_workflow_file("lint") == "lint.yml"
    assert check_workflow_file("test") == "test.yml"


def test_pr_number_from_url() -> None:
    assert pr_number_from_url("https://github.com/org/repo/pull/21") == 21


def test_ci_status_success() -> None:
    runs = [CiRun(name="lint", status="completed", conclusion="success", url=None)]
    status, reason = ci_status(runs, ["lint"])
    assert status == "success"
    assert reason is None


def test_ci_status_missing() -> None:
    status, reason = ci_status([], ["lint"])
    assert status == "missing"
    assert reason is not None
    assert "lint" in reason


def test_ci_status_failed() -> None:
    runs = [CiRun(name="lint", status="completed", conclusion="failure", url=None)]
    status, reason = ci_status(runs, ["lint"])
    assert status == "failed"
    assert reason is not None


def test_wait_for_run_filters_by_head_branch(monkeypatch) -> None:
    runs = [
        {
            "databaseId": 1,
            "createdAt": "2026-01-01T00:00:01Z",
            "headBranch": "feature/unrelated",
        },
        {
            "databaseId": 2,
            "createdAt": "2026-01-01T00:00:02Z",
            "headBranch": "release/v1.2.3",
        },
    ]

    def fake_gh(*args, **kwargs):
        return CommandResult(returncode=0, stdout=json.dumps(runs), stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    run_id = wait_for_run(
        "org/repo",
        "publish-release.yml",
        0,
        head_branch="release/v1.2.3",
    )

    assert run_id == "2"


def test_wait_for_pr_checks_returns_when_checks_never_appear(monkeypatch) -> None:
    def fake_gh(*args, **kwargs):
        return CommandResult(
            returncode=0,
            stdout=json.dumps({"statusCheckRollup": []}),
            stderr="",
        )

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    wait_for_pr_checks("org/repo", 21, checks_appear_timeout=0)


def test_merge_release_pr_waits_for_checks_on_policy_rejection(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("releaser.cli.repo_allow_auto_merge", lambda _: False)

    def fake_gh(args, **kwargs):
        calls.append(list(args))
        merge_attempt = sum(1 for c in calls if c[:2] == ["pr", "merge"])
        if args[:2] == ["pr", "merge"] and merge_attempt == 1:
            return CommandResult(
                returncode=1,
                stdout="",
                stderr="Pull request #21 is not mergeable: the base branch policy prohibits the merge.",
            )
        if args[:2] == ["pr", "view"]:
            return CommandResult(
                returncode=0,
                stdout=json.dumps({"statusCheckRollup": [
                    {"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"},
                ]}),
                stderr="",
            )
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    mode = merge_release_pr("org/repo", 21)

    assert mode == "direct"
    merge_calls = [c for c in calls if c[:2] == ["pr", "merge"]]
    assert len(merge_calls) == 2
