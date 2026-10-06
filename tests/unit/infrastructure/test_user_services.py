"""Unit tests for the two unprivileged, user-scope service adapters.

Both are driven entirely through fakes: no subprocess starts, no network call
is made and no path outside the in-memory ``FakeFileSystem`` is touched, which
is what lets a Windows ``schtasks`` adapter and a Debian ``systemctl --user``
adapter both be tested from whichever machine happens to be running the suite.
"""

from __future__ import annotations

import pytest

from devtunnel.application.catalog import TUNNEL_SERVICE
from devtunnel.application.ports.process_runner import CompletedProcess
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.infrastructure.debian.systemd_user import SystemdUserServiceManager
from devtunnel.infrastructure.windows.scheduled_task import WindowsScheduledTaskManager
from tests.fakes.fake_ports import FakeFileSystem, FakeProcessRunner

UNIT = "devtunnel-tailcat"
UNIT_PATH = "/home/fake/.config/systemd/user/devtunnel-tailcat.service"
LINGER_MARKER = "/home/fake/.local/share/devtunnel/devtunnel-tailcat.linger-owned"
TASK = "DevtunnelTailcat"

TAILCAT_COMMAND = (
    "/home/fake/.local/share/devtunnel/bin/tailcat",
    "serve",
    "--key=default",
    "--ssh-authorized-keys=/home/fake/.ssh/authorized_keys",
    "ssh",
)

SHOW_LINGER = ("loginctl", "show-user", "tommy", "--property=Linger")
ENABLE_LINGER = ("loginctl", "enable-linger", "tommy")
DISABLE_LINGER = ("loginctl", "disable-linger", "tommy")


def _respond(runner: FakeProcessRunner, argv, *, returncode=0, stdout="", stderr="") -> None:
    runner.responses[tuple(argv)] = CompletedProcess(tuple(argv), returncode, stdout, stderr)


def _systemd(runner: FakeProcessRunner, filesystem: FakeFileSystem, command=TAILCAT_COMMAND):
    return SystemdUserServiceManager(runner, filesystem, unit_command=command, user="tommy")


def _existing_unit(runner: FakeProcessRunner, *, active: str, enabled: str) -> None:
    _respond(runner, ("systemctl", "--user", "cat", UNIT))
    _respond(runner, ("systemctl", "--user", "is-active", UNIT), stdout=f"{active}\n")
    _respond(runner, ("systemctl", "--user", "is-enabled", UNIT), stdout=f"{enabled}\n")


# ---------------------------------------------------------------------------
# systemd user unit
# ---------------------------------------------------------------------------


def test_the_user_unit_is_written_under_the_real_user_home_and_not_a_tilde_expansion():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()

    _systemd(runner, filesystem).ensure_running_and_enabled(TUNNEL_SERVICE)

    # real_user_home() is "/home/fake"; under sudo os.path.expanduser("~")
    # would have produced /root/... and the user's systemd would never look
    # there. The write landing on this exact path is the whole guard.
    assert UNIT_PATH in filesystem.files
    assert "/root" not in "".join(filesystem.files)


def test_the_user_unit_content_matches_the_planned_template():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()

    _systemd(runner, filesystem).ensure_running_and_enabled(TUNNEL_SERVICE)

    assert filesystem.files[UNIT_PATH] == (
        "[Unit]\n"
        "Description=devtunnel tailcat tunnel\n"
        "After=network-online.target\n"
        "\n"
        "[Service]\n"
        "ExecStart=/home/fake/.local/share/devtunnel/bin/tailcat serve --key=default "
        "--ssh-authorized-keys=/home/fake/.ssh/authorized_keys ssh\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def test_exec_start_quotes_an_install_directory_containing_a_space():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    command = ("/home/fake/dev tools/tailcat", "serve", "ssh")

    manager = _systemd(runner, filesystem, command)

    # systemd splits ExecStart on whitespace, so an unquoted path with a space
    # would silently become two arguments and the unit would fail to start.
    assert "ExecStart='/home/fake/dev tools/tailcat' serve ssh\n" in manager.unit_content(
        TUNNEL_SERVICE
    )


def test_enable_runs_daemon_reload_then_enable_now_then_enable_linger_in_that_order():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()

    _systemd(runner, filesystem).ensure_running_and_enabled(TUNNEL_SERVICE)

    assert runner.calls == [
        ["systemctl", "--user", "daemon-reload"],
        ["systemctl", "--user", "enable", "--now", UNIT],
        # Probed before enabling so the marker file is an accurate record of
        # what devtunnel itself changed.
        list(SHOW_LINGER),
        list(ENABLE_LINGER),
    ]


def test_a_failing_loginctl_enable_linger_is_surfaced_and_never_reported_as_healthy():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    _respond(
        runner,
        ENABLE_LINGER,
        returncode=1,
        stderr="Failed to enable linger: Interactive authentication required.",
    )
    manager = _systemd(runner, filesystem)

    manager.ensure_running_and_enabled(TUNNEL_SERVICE)

    assert manager.last_linger_error is not None
    assert "Interactive authentication required" in manager.last_linger_error
    assert "--system" in manager.last_linger_error
    # The unit would die at logout, so the manager must not claim lingering is
    # on, and must not leave a marker claiming devtunnel enabled something it
    # did not -- that would make revert disable lingering it never turned on.
    assert manager.linger_enabled("tommy") is False
    assert LINGER_MARKER not in filesystem.files


def test_get_state_parses_an_active_and_enabled_user_unit():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    _existing_unit(runner, active="active", enabled="enabled")

    state = _systemd(runner, filesystem).get_state(TUNNEL_SERVICE)

    assert state == ServiceState(exists=True, running=True, startup_automatic=True)


def test_get_state_parses_an_inactive_and_disabled_user_unit():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    _existing_unit(runner, active="inactive", enabled="disabled")

    state = _systemd(runner, filesystem).get_state(TUNNEL_SERVICE)

    assert state == ServiceState(exists=True, running=False, startup_automatic=False)


def test_get_state_reports_the_unit_as_absent_when_systemctl_user_cat_fails():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    _respond(runner, ("systemctl", "--user", "cat", UNIT), returncode=1, stderr="No files found")

    state = _systemd(runner, filesystem).get_state(TUNNEL_SERVICE)

    assert state == ServiceState(exists=False, running=False, startup_automatic=False)
    # Nothing further is probed once the unit is known not to exist.
    assert runner.calls == [["systemctl", "--user", "cat", UNIT]]


def test_apply_state_back_to_absent_removes_the_unit_file_and_reloads_the_daemon():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    manager = _systemd(runner, filesystem)
    manager.ensure_running_and_enabled(TUNNEL_SERVICE)
    runner.calls.clear()

    manager.apply_state(TUNNEL_SERVICE, ServiceState(False, False, False))

    assert UNIT_PATH not in filesystem.files
    assert ["systemctl", "--user", "disable", "--now", UNIT] in runner.calls
    assert ["systemctl", "--user", "daemon-reload"] in runner.calls


def test_revert_disables_lingering_that_devtunnel_itself_enabled():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    manager = _systemd(runner, filesystem)
    manager.ensure_running_and_enabled(TUNNEL_SERVICE)
    assert LINGER_MARKER in filesystem.files

    manager.apply_state(TUNNEL_SERVICE, ServiceState(False, False, False))

    assert list(DISABLE_LINGER) in runner.calls
    assert LINGER_MARKER not in filesystem.files


def test_revert_leaves_lingering_alone_when_the_user_already_had_it_enabled():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    _respond(runner, SHOW_LINGER, stdout="Linger=yes\n")
    manager = _systemd(runner, filesystem)
    manager.ensure_running_and_enabled(TUNNEL_SERVICE)

    manager.apply_state(TUNNEL_SERVICE, ServiceState(False, False, False))

    # Never touch what we did not install: disabling lingering here would kill
    # every other long-running user service on the box.
    assert list(ENABLE_LINGER) not in runner.calls
    assert list(DISABLE_LINGER) not in runner.calls
    assert manager.last_linger_error is None


def test_apply_state_restores_a_pre_existing_unit_instead_of_deleting_it():
    runner, filesystem = FakeProcessRunner(), FakeFileSystem()
    filesystem.write_text(UNIT_PATH, "someone else's unit")
    manager = _systemd(runner, filesystem)

    manager.apply_state(TUNNEL_SERVICE, ServiceState(True, False, True))

    assert filesystem.files[UNIT_PATH] == "someone else's unit"
    assert runner.calls == [
        ["systemctl", "--user", "enable", UNIT],
        ["systemctl", "--user", "stop", UNIT],
    ]


def test_a_user_service_manager_without_a_command_to_run_is_rejected_at_construction():
    with pytest.raises(ValueError):
        SystemdUserServiceManager(FakeProcessRunner(), FakeFileSystem(), unit_command=())


# ---------------------------------------------------------------------------
# Windows Scheduled Task
# ---------------------------------------------------------------------------

TASK_COMMAND = ("C:\\Users\\tommy\\AppData\\Local\\devtunnel\\bin\\tailcat.exe", "serve", "ssh")
SPACED_COMMAND = ("C:\\Program Files\\devtunnel\\tailcat.exe", "serve", "--key=default", "ssh")


def _query_output(status: str, task_state: str | None = "Enabled") -> str:
    lines = [
        "Folder: \\",
        "HostName:                             DEVBOX",
        f"TaskName:                             \\{TASK}",
        "Next Run Time:                        N/A",
        f"Status:                               {status}",
        "Logon Mode:                           Interactive only",
        "Run As User:                          tommy",
    ]
    if task_state is not None:
        lines.append(f"Scheduled Task State:                 {task_state}")
    return "\n".join(lines) + "\n"


def _query_response(runner: FakeProcessRunner, *, returncode=0, stdout="") -> None:
    _respond(
        runner,
        ("schtasks", "/Query", "/TN", TASK, "/FO", "LIST", "/V"),
        returncode=returncode,
        stdout=stdout,
    )


def test_the_scheduled_task_is_created_at_logon_with_a_limited_run_level():
    runner = FakeProcessRunner()

    WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).ensure_running_and_enabled(
        TUNNEL_SERVICE
    )

    # /SC ONLOGON plus /RL LIMITED in the user's own context is what keeps
    # registering (and firing) this task free of an elevation prompt.
    assert runner.calls[0] == [
        "schtasks",
        "/Create",
        "/TN",
        TASK,
        "/TR",
        "C:\\Users\\tommy\\AppData\\Local\\devtunnel\\bin\\tailcat.exe serve ssh",
        "/SC",
        "ONLOGON",
        "/RL",
        "LIMITED",
        "/F",
    ]


def test_creating_the_scheduled_task_is_followed_by_an_explicit_run():
    runner = FakeProcessRunner()

    WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).ensure_running_and_enabled(
        TUNNEL_SERVICE
    )

    # ONLOGON alone would not start anything until the next logon.
    assert runner.calls[1] == ["schtasks", "/Run", "/TN", TASK]


def test_a_task_command_containing_a_space_round_trips_quoted_into_the_task():
    runner = FakeProcessRunner()

    WindowsScheduledTaskManager(runner, task_command=SPACED_COMMAND).ensure_running_and_enabled(
        TUNNEL_SERVICE
    )

    # Task Scheduler re-parses the /TR string when the task fires, so the path
    # has to carry its own quotes; otherwise it fires as "C:\Program".
    assert runner.calls[0][runner.calls[0].index("/TR") + 1] == (
        '"C:\\Program Files\\devtunnel\\tailcat.exe" serve --key=default ssh'
    )


def test_scheduled_task_get_state_reports_a_running_task():
    runner = FakeProcessRunner()
    _query_response(runner, stdout=_query_output("Running"))

    state = WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).get_state(TUNNEL_SERVICE)

    assert state == ServiceState(exists=True, running=True, startup_automatic=True)


def test_scheduled_task_get_state_reports_a_ready_task_as_existing_but_not_running():
    runner = FakeProcessRunner()
    _query_response(runner, stdout=_query_output("Ready"))

    state = WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).get_state(TUNNEL_SERVICE)

    assert state == ServiceState(exists=True, running=False, startup_automatic=True)


def test_scheduled_task_get_state_reports_a_disabled_task_as_not_starting_automatically():
    runner = FakeProcessRunner()
    _query_response(runner, stdout=_query_output("Ready", task_state="Disabled"))

    state = WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).get_state(TUNNEL_SERVICE)

    assert state == ServiceState(exists=True, running=False, startup_automatic=False)


def test_scheduled_task_get_state_reports_an_absent_task_as_not_existing():
    runner = FakeProcessRunner()
    _query_response(
        runner, returncode=1, stdout="ERROR: The system cannot find the file specified.\n"
    )

    state = WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).get_state(TUNNEL_SERVICE)

    assert state == ServiceState(exists=False, running=False, startup_automatic=False)


def test_scheduled_task_apply_state_back_to_absent_deletes_the_task_with_force():
    runner = FakeProcessRunner()

    WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).apply_state(
        TUNNEL_SERVICE, ServiceState(False, False, False)
    )

    # /End first so a delete cannot leave a running listener behind.
    assert runner.calls == [
        ["schtasks", "/End", "/TN", TASK],
        ["schtasks", "/Delete", "/TN", TASK, "/F"],
    ]


def test_scheduled_task_apply_state_restores_a_disabled_and_stopped_task():
    runner = FakeProcessRunner()

    WindowsScheduledTaskManager(runner, task_command=TASK_COMMAND).apply_state(
        TUNNEL_SERVICE, ServiceState(True, False, False)
    )

    assert runner.calls == [
        ["schtasks", "/Change", "/TN", TASK, "/DISABLE"],
        ["schtasks", "/End", "/TN", TASK],
    ]


def test_both_user_scope_managers_satisfy_the_service_manager_port_unchanged():
    managers = (
        SystemdUserServiceManager(
            FakeProcessRunner(), FakeFileSystem(), unit_command=TAILCAT_COMMAND
        ),
        WindowsScheduledTaskManager(FakeProcessRunner(), task_command=TASK_COMMAND),
    )

    # EnsureServiceStep and its reverter only ever call these three, which is
    # why neither needs a change to drive a user-scope backend.
    for manager in managers:
        assert callable(manager.get_state)
        assert callable(manager.ensure_running_and_enabled)
        assert callable(manager.apply_state)
