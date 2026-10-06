"""Unit tests for the real process runner's stderr reader.

This is the one unit test module that spawns a real child. It has to: the thing
under test *is* the reader thread, the pipe and the queue, and a fake pipe would
only prove the fake does not deadlock. Every child is a short-lived
``sys.executable -c ...`` with no network and no filesystem access, and each one
is terminated before the test returns.
"""

from __future__ import annotations

import sys
import time

from devtunnel.infrastructure.process.dry_run_runner import DRY_RUN_ADDRESS, DryRunProcessRunner
from devtunnel.infrastructure.process.subprocess_runner import SubprocessProcessRunner


def spawn_python(source: str):
    return SubprocessProcessRunner().spawn([sys.executable, "-c", source])


def test_read_stderr_line_returns_lines_in_the_order_the_child_wrote_them():
    process = spawn_python(
        "import sys\n"
        "print('first', file=sys.stderr)\n"
        "print('second', file=sys.stderr)\n"
        "sys.stderr.flush()\n"
    )
    try:
        assert process.read_stderr_line(timeout=10.0) == "first"
        assert process.read_stderr_line(timeout=10.0) == "second"
    finally:
        process.terminate()
        process.wait(timeout=10.0)


def test_read_stderr_line_reports_eof_as_none_once_the_child_has_finished():
    process = spawn_python("import sys; print('only', file=sys.stderr)")
    try:
        assert process.read_stderr_line(timeout=10.0) == "only"
        # EOF is sticky -- a second caller must not block on a closed stream.
        assert process.read_stderr_line(timeout=10.0) is None
        assert process.read_stderr_line(timeout=10.0) is None
    finally:
        process.terminate()
        process.wait(timeout=10.0)


def test_read_stderr_line_times_out_promptly_against_a_child_that_says_nothing():
    """The deadlock regression. ``stderr.readline()`` here would never return."""

    process = spawn_python("import time; time.sleep(30)")
    try:
        started = time.monotonic()
        line = process.read_stderr_line(timeout=0.3)
        elapsed = time.monotonic() - started

        assert line is None
        assert elapsed < 5.0, "read_stderr_line blocked well past its timeout"
        assert process.poll() is None, "the child should still be running"
    finally:
        process.terminate()
        process.wait(timeout=10.0)


def test_a_non_utf8_byte_is_replaced_rather_than_raising():
    process = spawn_python(
        "import sys\n"
        "sys.stderr.buffer.write(b'caf\\xe9 listening\\n')\n"
        "sys.stderr.buffer.flush()\n"
    )
    try:
        line = process.read_stderr_line(timeout=10.0)

        assert line is not None
        assert line.startswith("caf")
        assert line.endswith("listening")
        assert "�" in line
    finally:
        process.terminate()
        process.wait(timeout=10.0)


def test_a_non_ascii_banner_survives_the_round_trip():
    process = spawn_python(
        "import sys\n"
        "sys.stderr.buffer.write('\\U0001f408 Server listening\\n'.encode('utf-8'))\n"
        "sys.stderr.buffer.flush()\n"
    )
    try:
        assert process.read_stderr_line(timeout=10.0) == "\U0001f408 Server listening"
    finally:
        process.terminate()
        process.wait(timeout=10.0)


def test_stdout_is_pumped_too_so_a_chatty_child_cannot_fill_its_pipe():
    process = spawn_python(
        "import sys\n"
        "print('x' * 100000)\n"
        "print('done', file=sys.stderr)\n"
        "sys.stderr.flush()\n"
    )
    try:
        assert process.read_stderr_line(timeout=10.0) == "done"
    finally:
        process.terminate()
        process.wait(timeout=10.0)


# -- the dry-run Null Object ----------------------------------------------


def test_dry_run_spawn_yields_the_canned_banner_instead_of_raising():
    process = DryRunProcessRunner(SubprocessProcessRunner()).spawn(["tailcat", "serve", "ssh"])

    line = process.read_stderr_line(timeout=0.0)

    assert line is not None
    assert "listening" in line
    assert DRY_RUN_ADDRESS in line


def test_the_dry_run_address_is_obviously_fake():
    assert "DRYRUN" in DRY_RUN_ADDRESS


def test_dry_run_spawn_reports_a_process_that_is_still_running():
    process = DryRunProcessRunner(SubprocessProcessRunner()).spawn(["tailcat", "serve", "ssh"])

    assert process.poll() is None
    process.terminate()
    assert process.wait(timeout=1.0) == 0


def test_the_dry_run_banner_is_handed_out_exactly_once():
    process = DryRunProcessRunner(SubprocessProcessRunner()).spawn(["tailcat", "serve", "ssh"])

    assert process.read_stderr_line() is not None
    assert process.read_stderr_line() is None
