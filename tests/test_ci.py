import json

from releaser.cli import (
    CiRun,
    CommandResult,
    check_workflow_file,
    ci_status,
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


def test_wait_for_pr_checks_allows_empty_rollup(monkeypatch) -> None:
    def fake_gh(*args, **kwargs):
        return CommandResult(
            returncode=0,
            stdout=json.dumps({"statusCheckRollup": []}),
            stderr="",
        )

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    wait_for_pr_checks("org/repo", 21)
