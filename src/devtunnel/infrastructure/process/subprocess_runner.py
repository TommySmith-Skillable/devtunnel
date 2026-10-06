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
import queue
import shutil
import subprocess
import threading
from collections.abc import Mapping, Sequence

from devtunnel.application.ports.process_runner import CompletedProcess, ManagedProcess

_EOF = object()
"""Sentinel pushed by a reader thread when its stream closes, so that EOF and
"nothing yet" are distinguishable inside the queue rather than both surfacing as
a timeout."""


class SubprocessManagedProcess:
    """A spawned child whose output streams are pumped by daemon threads.

    **Why threads and a queue at all.** tailcat announces the tunnel's address
    on *stderr* at startup, so ``start()`` has to wait for a specific line while
    still being able to give up. ``proc.stderr.readline()`` has no timeout:
    against a child that starts, says nothing and never exits -- a tailcat that
    cannot reach the data plane, a binary waiting on a prompt -- it blocks the
    caller forever, and the ``timeout`` argument on
    :meth:`~devtunnel.application.ports.tunnel_provider.TunnelProviderPort.start`
    becomes a lie. Pushing every line into a :class:`queue.Queue` from a reader
    thread turns the wait into ``queue.get(timeout=...)``, which is the one form
    of this that can actually time out.

    **Both** streams are pumped, not just stderr: an unread pipe fills its OS
    buffer and then blocks the *child* on its next write, which is the same
    deadlock arriving from the other direction.

    The threads are ``daemon=True`` so that a child which hangs -- or which we
    deliberately left running -- can never hold the interpreter open at exit.
    """

    def __init__(self, popen: subprocess.Popen) -> None:
        self._popen = popen
        self._stdout_lines = self._pump(popen.stdout)
        self._stderr_lines = self._pump(popen.stderr)

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
        """The raw stdout pipe, kept for callers that predate the pump.

        Prefer :meth:`read_stdout_line`: the reader thread owns this stream now,
        so reading it directly races with the pump for lines.
        """

        return self._popen.stdout

    def read_stderr_line(self, timeout: float | None = None) -> str | None:
        return self._next_line(self._stderr_lines, timeout)

    def read_stdout_line(self, timeout: float | None = None) -> str | None:
        return self._next_line(self._stdout_lines, timeout)

    @staticmethod
    def _next_line(lines: queue.Queue, timeout: float | None) -> str | None:
        try:
            item = lines.get() if timeout is None else lines.get(timeout=timeout)
        except queue.Empty:
            return None
        if item is _EOF:
            # EOF is sticky: put it back so every later call reports it too,
            # instead of the second caller blocking on a stream that is closed.
            lines.put(_EOF)
            return None
        return item

    @staticmethod
    def _pump(stream) -> queue.Queue:
        lines: queue.Queue = queue.Queue()
        if stream is None:
            lines.put(_EOF)
            return lines

        def reader() -> None:
            try:
                while True:
                    line = stream.readline()
                    if not line:
                        break
                    lines.put(line.rstrip("\r\n"))
            except (ValueError, OSError):
                # The stream was closed underneath us (terminate, interpreter
                # teardown). That is an EOF, not a failure worth propagating
                # from a thread nobody is joining.
                pass
            finally:
                lines.put(_EOF)

        threading.Thread(target=reader, daemon=True).start()
        return lines


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
            # Decoding is pinned here rather than left to the locale: tailcat's
            # startup banner opens with a non-ASCII emoji and a Windows console
            # is rarely UTF-8, so the default encoding would raise
            # UnicodeDecodeError on the one line we most need to read.
            # `errors="replace"` means a mojibake'd glyph costs us nothing --
            # the address we parse out of that line is pure ASCII.
            #
            # Of the two correct routes (binary pipes decoded in each reader
            # thread, or text pipes configured here) this one is chosen because
            # it keeps decoding in a single place that both streams and the
            # legacy `.stdout` property share, and keeps the reader threads
            # free of anything but line handling.
            text=True,
            encoding="utf-8",
            errors="replace",
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
