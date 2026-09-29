"""Null Object wrapper for ``--dry-run``.

Every :class:`~devtunnel.application.steps.JournaledStep` already checks
``ctx.dry_run`` itself and returns before calling ``perform()`` -- so, in
practice, this wrapper's ``run``/``spawn`` are never reached by v1's step
library. It is wired in anyway as a second line of defence: if a future step
is added that forgets its own dry-run guard, this Null Object still guarantees
no real mutation happens, rather than depending on every step remembering to
check a flag.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from devtunnel.application.ports.process_runner import (
    CompletedProcess,
    ManagedProcess,
    ProcessRunnerPort,
)


class DryRunProcessRunner:
    def __init__(self, delegate: ProcessRunnerPort) -> None:
        # `which()` is read-only, so delegate it for realistic detection
        # (e.g. "is ngrok already on PATH?") even while dry-running.
        self._delegate = delegate

    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CompletedProcess:
        return CompletedProcess(tuple(argv), 0, "", "")

    def spawn(
        self, argv: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> ManagedProcess:
        raise RuntimeError("cannot start a long-running process during --dry-run")

    def which(self, executable: str) -> str | None:
        return self._delegate.which(executable)
