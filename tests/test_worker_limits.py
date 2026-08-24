"""How much one worker will do at once.

One task queue serves every registered application, and with auto-merge enabled an unbounded
worker is an unbounded number of unattended merges. Nothing else in the system caps this.
"""

from __future__ import annotations

import pytest

from engine.config import ConfigError
from engine.worker import (
    DEFAULT_MAX_ACTIVITIES,
    DEFAULT_MAX_WORKFLOW_TASKS,
    MAX_ACTIVITIES_VARIABLE,
    MAX_WORKFLOW_TASKS_VARIABLE,
    limits,
)


def test_the_default_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unset must not mean unlimited — the sandbox takes the same posture for the same reason."""
    monkeypatch.delenv(MAX_ACTIVITIES_VARIABLE, raising=False)
    monkeypatch.delenv(MAX_WORKFLOW_TASKS_VARIABLE, raising=False)
    assert limits() == (DEFAULT_MAX_ACTIVITIES, DEFAULT_MAX_WORKFLOW_TASKS)


def test_the_environment_overrides_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(MAX_ACTIVITIES_VARIABLE, "12")
    monkeypatch.setenv(MAX_WORKFLOW_TASKS_VARIABLE, "100")
    assert limits() == (12, 100)


def test_an_empty_value_is_treated_as_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """`VAR=` in a .env file is a common way to mean "leave it alone"."""
    monkeypatch.setenv(MAX_ACTIVITIES_VARIABLE, "  ")
    assert limits()[0] == DEFAULT_MAX_ACTIVITIES


@pytest.mark.parametrize("bad", ["four", "0", "-3", "3.5"])
def test_a_malformed_limit_refuses_to_start(monkeypatch: pytest.MonkeyPatch, bad: str) -> None:
    """Not defaulted quietly.

    Someone who typed a limit has said what they want. Falling back to the default would leave
    them believing a cap was applied that was not — the same class of error as a sandbox
    reporting a bound it does not enforce.
    """
    monkeypatch.setenv(MAX_ACTIVITIES_VARIABLE, bad)
    with pytest.raises(ConfigError, match=MAX_ACTIVITIES_VARIABLE):
        limits()
