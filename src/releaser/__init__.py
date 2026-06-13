"""Opinionated zero-config release gate for GitHub repositories."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("releaser")
except PackageNotFoundError:
    __version__ = "unknown"
