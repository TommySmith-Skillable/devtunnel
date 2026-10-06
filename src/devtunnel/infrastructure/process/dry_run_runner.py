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

DRY_RUN_ADDRESS = "tcDRYRUNDRYRUNDRYRUNDRYRUNDRYRUNDRYRUNdryrun"
"""Deliberately, unmistakably synthetic.

``--dry-run`` has to render the *shape* of the output a real run produces --
including ``devtunnel connect <address>`` -- or the preview is not a preview of
anything. A plausible-looking random address would be worse than no address at
all: someone would paste it to a peer. Spelling "DRYRUN" means the only way to
misread this one is not to read it.
"""

_DRY_RUN_BANNER = f'\U0001f408 Server listening with saved key "dry-run": {DRY_RUN_ADDRESS}'


class DryRunManagedProcess:
    """A tunnel that was never started, shaped like one that was.

    Reports itself as still running so the caller's wait-for-address loop takes
    the same path it takes for real, and hands out the canned banner exactly
    once -- after which ``read_stderr_line`` reports EOF rather than looping the
    same line forever.
    """

    def __init__(self, argv: tuple[str, ...]) -> None:
        self.argv = argv
        self._banner_pending = True

    @property
    def pid(self) -> int:
        return 0

    def poll(self) -> int | None:
        return None

    def terminate(self) -> None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def read_stderr_line(self, timeout: float | None = None) -> str | None:
        if self._banner_pending:
            self._banner_pending = False
            return _DRY_RUN_BANNER
        return None


class DryRunProcessRunner:
    def __init__(self, delegate: ProcessRunnerPort) -> None:
        # `which()` is read-only, so delegate it for realistic detection
        # (e.g. "is tailcat already on PATH?") even while dry-running.
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
        # Raising here used to be defensible, when nothing in a dry run reached
        # spawn(). `devtunnel up --dry-run` does: it must print the connection
        # command the real run would print, which means the Null Object has to
        # answer like a tunnel instead of refusing to be one.
        return DryRunManagedProcess(tuple(argv))

    def which(self, executable: str) -> str | None:
        return self._delegate.which(executable)
