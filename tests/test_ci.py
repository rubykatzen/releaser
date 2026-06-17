from releaser.cli import CiRun, check_workflow_file, ci_status, pr_number_from_url


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
