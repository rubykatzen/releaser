import json

import pytest

from releaser.cli import (
    CiRun,
    CommandResult,
    ReleaseError,
    check_workflow_file,
    ci_status,
    merge_release_pr,
    pr_number_from_url,
    wait_for_pr_checks,
    wait_for_run,
    wait_for_run_completion,
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


def test_wait_for_pr_checks_waits_for_checks_to_appear(monkeypatch) -> None:
    responses = [
        {"statusCheckRollup": []},
        {"statusCheckRollup": [{"name": "lint", "status": "COMPLETED", "conclusion": "SUCCESS"}]},
    ]

    def fake_gh(*args, **kwargs):
        return CommandResult(returncode=0, stdout=json.dumps(responses.pop(0)), stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    wait_for_pr_checks("org/repo", 21, poll_interval=0)


def test_merge_release_pr_waits_when_branch_has_required_checks(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("releaser.cli.repo_allow_auto_merge", lambda _: False)
    monkeypatch.setattr("releaser.cli.required_check_contexts", lambda repo, branch: ["lint"])

    def fake_gh(args, **kwargs):
        calls.append(list(args))
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

    mode = merge_release_pr("org/repo", 21, "main")

    assert mode == "direct"
    assert any(c[:2] == ["pr", "view"] for c in calls)
    assert any(c[:2] == ["pr", "merge"] for c in calls)


def test_merge_release_pr_skips_checks_when_no_branch_protection(monkeypatch) -> None:
    calls: list[list[str]] = []

    monkeypatch.setattr("releaser.cli.repo_allow_auto_merge", lambda _: False)
    monkeypatch.setattr("releaser.cli.required_check_contexts", lambda repo, branch: None)

    def fake_gh(args, **kwargs):
        calls.append(list(args))
        return CommandResult(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    mode = merge_release_pr("org/repo", 21, "main")

    assert mode == "direct"
    assert not any(c[:2] == ["pr", "view"] for c in calls)


def test_wait_for_run_completion_succeeds(monkeypatch) -> None:
    responses = [
        {
            "status": "in_progress",
            "conclusion": None,
            "jobs": [{"name": "lint", "status": "in_progress", "conclusion": None}],
        },
        {
            "status": "completed",
            "conclusion": "success",
            "jobs": [{"name": "lint", "status": "completed", "conclusion": "success"}],
        },
    ]

    def fake_gh(*args, **kwargs):
        return CommandResult(returncode=0, stdout=json.dumps(responses.pop(0)), stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    wait_for_run_completion("org/repo", "123", poll_interval=0)


def test_wait_for_run_completion_raises_on_failure(monkeypatch) -> None:
    response = {
        "status": "completed",
        "conclusion": "failure",
        "jobs": [{"name": "lint", "status": "completed", "conclusion": "failure"}],
    }

    def fake_gh(*args, **kwargs):
        return CommandResult(returncode=0, stdout=json.dumps(response), stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    with pytest.raises(ReleaseError, match="failed with conclusion failure"):
        wait_for_run_completion("org/repo", "123", poll_interval=0)


def test_wait_for_run_completion_includes_label_in_error(monkeypatch) -> None:
    response = {
        "status": "completed",
        "conclusion": "failure",
        "jobs": [],
    }

    def fake_gh(*args, **kwargs):
        return CommandResult(returncode=0, stdout=json.dumps(response), stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    with pytest.raises(ReleaseError, match="prepare-release.yml failed"):
        wait_for_run_completion("org/repo", "123", poll_interval=0, label="prepare-release.yml")


def test_wait_for_run_completion_times_out(monkeypatch) -> None:
    def fake_gh(*args, **kwargs):
        return CommandResult(returncode=0, stdout=json.dumps({"status": "in_progress", "conclusion": None, "jobs": []}), stderr="")

    monkeypatch.setattr("releaser.cli.gh", fake_gh)

    with pytest.raises(ReleaseError, match="Timed out waiting for workflow run 123"):
        wait_for_run_completion("org/repo", "123", timeout=0, poll_interval=0)
