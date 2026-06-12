from releaser.cli import bump, parse_version


def test_parse_missing_tag_starts_from_zero() -> None:
    assert parse_version(None) == (0, 0, 0)


def test_bump_patch() -> None:
    assert bump("v1.2.3", "patch") == "v1.2.4"


def test_bump_minor() -> None:
    assert bump("v1.2.3", "minor") == "v1.3.0"


def test_bump_major() -> None:
    assert bump("v1.2.3", "major") == "v2.0.0"


def test_first_release() -> None:
    assert bump(None, "patch") == "v0.0.1"
