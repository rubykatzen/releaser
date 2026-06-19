from releaser.cli import commit_range, parse_git_log_line


def test_commit_range_with_tag() -> None:
    assert commit_range("v1.0.0", "abc123") == "v1.0.0..abc123"


def test_commit_range_without_tag() -> None:
    assert commit_range(None, "abc123") == "abc123"


def test_parse_git_log_line() -> None:
    commit = parse_git_log_line(
        "fullsha123\tabc1234\t2026-06-17 12:34:56 +0200\tfeat: show unreleased commits in status"
    )
    assert commit is not None
    assert commit.sha == "fullsha123"
    assert commit.short_sha == "abc1234"
    assert commit.subject == "feat: show unreleased commits in status"
    assert commit.committed_at == "2026-06-17 12:34:56 +0200"


def test_parse_git_log_line_rejects_invalid() -> None:
    assert parse_git_log_line("not-a-log-line") is None
