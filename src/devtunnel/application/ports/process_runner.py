"""Port for running external processes.

Every adapter that shells out (Chocolatey, apt, git, tailcat, sc.exe,
systemctl, schtasks...) does so through this one seam. That is what makes ``--dry-run``
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
    """A handle to a long-running child process (e.g. ``tailcat serve``)."""

    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None:
        """Return the exit code if the process has exited, else ``None``."""

    def terminate(self) -> None: ...

    def wait(self, timeout: float | None = None) -> int: ...

    def read_stderr_line(self, timeout: float | None = None) -> str | None:
        """Next line of stderr, or ``None`` on timeout or EOF.

        tailcat writes its address to stderr at startup, so reading a child's
        output is not an adapter-private detail -- it is part of this port.

        Implementations must not block the caller past ``timeout``: a plain
        ``proc.stderr.readline()`` has no timeout and deadlocks the
        wait-for-address loop against a process that simply never speaks.
        The real adapter pumps each stream on a daemon thread into a queue.
        Lines come back with their trailing newline stripped.
        """


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
