"""Exception hierarchy for devtunnel.

Every error the tool can raise deliberately derives from :class:`DevtunnelError`
so the CLI layer can catch one type and render a clean message instead of a
traceback. Nothing in this module performs I/O.
"""

from __future__ import annotations


class DevtunnelError(Exception):
    """Base class for all errors raised intentionally by devtunnel."""


class UnsupportedPlatformError(DevtunnelError):
    """Raised when no platform adapter recognises the current machine."""


class ElevationRequiredError(DevtunnelError):
    """Raised when a step needs administrator/root privileges that are absent."""


class PreflightError(DevtunnelError):
    """Raised when a precondition for install/uninstall is not met."""


class StepFailedError(DevtunnelError):
    """Raised when a plan step's apply() fails in a way that halts the plan."""

    def __init__(self, step_description: str, reason: str) -> None:
        self.step_description = step_description
        self.reason = reason
        super().__init__(f"{step_description}: {reason}")


class RevertFailedError(DevtunnelError):
    """Raised when reverting a journaled change fails."""

    def __init__(self, target: str, reason: str) -> None:
        self.target = target
        self.reason = reason
        super().__init__(f"could not revert {target}: {reason}")


class CredentialResolutionError(DevtunnelError):
    """Raised when a required credential cannot be resolved from any source."""


class NonInteractiveInputRequiredError(DevtunnelError):
    """Raised when running with --non-interactive but a value has no other source."""

    def __init__(self, what: str) -> None:
        self.what = what
        super().__init__(
            f"{what} was not provided and --non-interactive forbids prompting"
        )
