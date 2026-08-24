"""Does this worker's configuration govern this run's application?

A worker loads one `kuwarden.yaml` at startup and hands the same `AppConfig` to every node,
whichever application the run belongs to. Credentials are resolved per application; the
configuration is not. So a run for application B, on a worker configured for application A,
reads A's repository list, A's tiering rules and A's auto-merge policy while holding B's
tokens — and nothing anywhere would say so.

That is a silent wrong-repository hazard, and this module is the sentence that turns it into a
refusal. It is a stopgap: the intended shape is to read each application's configuration from
its own repository per run, which is not built.
"""

from __future__ import annotations

from engine.errors import PolicyDenied


def assert_configured_for(configured: str, requested: str) -> None:
    """Fail the run unless the loaded configuration is for the application it is about.

    `requested` empty means the run was started before this check existed. Treated as
    unverifiable rather than as consent: refusing is recoverable in one line, and a run that
    quietly pushed to the wrong repository is not.

    Compared case-insensitively on the trimmed name, because the same application registered
    as `Sasagayo` in the Workbench and `sasagayo` in the YAML is a typo, not a different
    application — and failing on that would train people to disable the check.
    """
    if not requested:
        raise PolicyDenied(
            f"this run does not name the application it is for, so it cannot be checked "
            f"against the loaded configuration for {configured!r}. Start it from the "
            "Workbench, which records the application."
        )
    if configured.strip().casefold() != requested.strip().casefold():
        raise PolicyDenied(
            f"this worker is configured for {configured!r} but the run is for {requested!r}. "
            "One worker serves one application's kuwarden.yaml: start a second worker with "
            "KUWARDEN_CONFIG pointing at that application's file, rather than letting this "
            "run read the wrong repository, tiering rules and merge policy."
        )
