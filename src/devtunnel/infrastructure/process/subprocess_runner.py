"""The real :class:`ProcessRunnerPort` adapter, backed by :mod:`subprocess`.

This is the only module in the whole codebase that calls ``subprocess``
directly for one-shot commands. Every package manager, service manager and
git-config step goes through it (or through
:class:`~devtunnel.infrastructure.process.dry_run_runner.DryRunProcessRunner`),
which is what makes the unit test suite able to assert on exact argv with a
fake in its place.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence

from devtunnel.application.ports.process_runner import CompletedProcess, ManagedProcess


class SubprocessManagedProcess:
    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen

    @property
    def pid(self) -> int:
        return self._popen.pid

    def poll(self) -> int | None:
        return self._popen.poll()

    def terminate(self) -> None:
        self._popen.terminate()

    def wait(self, timeout: float | None = None) -> int:
        return self._popen.wait(timeout=timeout)

    @property
    def stdout(self):
        return self._popen.stdout


class SubprocessProcessRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CompletedProcess:
        result = subprocess.run(
            list(argv),
            capture_output=True,
            text=True,
            input=input,
            env=self._merged_env(env),
            timeout=timeout,
        )
        completed = CompletedProcess(tuple(argv), result.returncode, result.stdout, result.stderr)
        if check and not completed.ok:
            command = " ".join(argv)
            raise RuntimeError(
                f"command failed ({completed.returncode}): {command}\n{result.stderr.strip()}"
            )
        return completed

    def spawn(
        self, argv: Sequence[str], *, env: Mapping[str, str] | None = None
    ) -> ManagedProcess:
        popen = subprocess.Popen(
            list(argv),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=self._merged_env(env),
        )
        return SubprocessManagedProcess(popen)

    def which(self, executable: str) -> str | None:
        return shutil.which(executable)

    @staticmethod
    def _merged_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
        if env is None:
            return None
        merged = dict(os.environ)
        merged.update(env)
        return merged
