"""Port for running external processes.

Every adapter that shells out (Chocolatey, apt, git, ngrok, sc.exe,
systemctl...) does so through this one seam. That is what makes ``--dry-run``
possible as a Null Object
(:class:`~devtunnel.infrastructure.process.dry_run_runner.DryRunProcessRunner`)
and what lets the unit tests assert on exact argv without a subprocess ever
starting.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class CompletedProcess:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


class ManagedProcess(Protocol):
    """A handle to a long-running child process (e.g. the ngrok agent)."""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None:
        """Return the exit code if the process has exited, else ``None``."""

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...


class ProcessRunnerPort(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CompletedProcess:
        """Run ``argv`` to completion and capture its output.

        Raises on a non-zero exit code when ``check`` is True (the default).
        """

    def spawn(
        self,
        argv: Sequence[str],
        *,
        env: Mapping[str, str] | None = None,
    ) -> ManagedProcess:
        """Start ``argv`` and return immediately with a handle to it."""

    def which(self, executable: str) -> str | None:
        """Resolve ``executable`` on PATH, or ``None`` if it is not found."""
