"""``ServiceManagerPort`` for Windows, via a per-user Scheduled Task.

:mod:`devtunnel.infrastructure.windows.sc_service` registers a real Windows
service, which means ``Set-Service``, the SCM, and Administrator. This adapter
registers a logon-triggered Scheduled Task in the *current user's own* context
instead, and needs no elevation at all:

* ``/RL LIMITED`` asks for the task's **least** privilege run level -- the
  token the user already has, with no elevation prompt. ``/RL HIGHEST`` is
  what would demand Administrator, both to register the task and every time it
  fires.
* No ``/RU``/``/RP`` is passed, so the task is created in the account running
  ``schtasks`` and stored under that account. Registering a task for *another*
  user, or in the machine-wide ``SYSTEM`` context, is the other thing that
  would require elevation.

Together those two are the Windows half of what ``loginctl enable-linger``
plus a systemd user unit do on Debian, and they are why ``devtunnel serve
--install-service`` stays inside the unprivileged default story on both
platforms.

Like its Debian counterpart the task's command is injected rather than built
here: tailcat's pre-1.0 argv is assembled in exactly one place, and this class
knows Task Scheduler, not tailcat.
"""

from __future__ import annotations

from collections.abc import Sequence

from devtunnel.application.ports.process_runner import CompletedProcess, ProcessRunnerPort
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.models import ServiceSpec


def quote_task_command(command: Sequence[str]) -> str:
    """Flatten an argv into the single string ``schtasks /TR`` expects.

    ``/TR`` takes one opaque string that Task Scheduler re-parses when the task
    fires, so a path with a space in it has to carry its *own* quotes all the
    way through -- the quoting the process runner applies when it builds the
    ``schtasks`` command line is consumed by ``schtasks`` itself and is gone by
    the time the task is stored. Without this,
    ``C:\\Program Files\\devtunnel\\tailcat.exe serve`` is stored verbatim and
    fires as ``C:\\Program`` with ``Files\\devtunnel\\tailcat.exe`` as its
    first argument, which fails at logon rather than at install time.
    """

    return " ".join(_quote_part(part) for part in command)


def _quote_part(part: str) -> str:
    if part and not any(character in part for character in ' \t"'):
        return part
    return '"' + part.replace('"', '\\"') + '"'


class WindowsScheduledTaskManager:
    """Satisfies ``ServiceManagerPort`` with a per-user Scheduled Task.

    ``ServiceState``'s three booleans map onto Task Scheduler as cleanly as
    they map onto systemd: *exists* is whether ``/Query`` finds the task at
    all, *running* is ``Status: Running`` versus ``Ready``, and
    *startup_automatic* is ``Scheduled Task State: Enabled`` -- a disabled task
    still exists and still has its ONLOGON trigger, it simply will not fire,
    which is precisely the distinction ``startup_automatic`` draws for a
    service. That correspondence is the whole reason ``EnsureServiceStep`` and
    its reverter need no change to drive this backend.
    """

    def __init__(
        self,
        process: ProcessRunnerPort,
        *,
        task_command: Sequence[str],
    ) -> None:
        if not task_command:
            raise ValueError("task_command must contain at least the executable to run")
        self._process = process
        self._task_command = tuple(task_command)

    # -- ServiceManagerPort -------------------------------------------------

    def get_state(self, service: ServiceSpec) -> ServiceState:
        result = self._query(service)
        if not result.ok:
            # schtasks exits non-zero with "The system cannot find the file
            # specified" for a task that was never created. Absent is a state,
            # not an error -- EnsureServiceStep captures it as the prior state
            # and the reverter deletes the task to get back to it.
            return ServiceState(exists=False, running=False, startup_automatic=False)
        fields = self._parse_list_output(result.stdout)
        status = fields.get("status", "")
        task_state = fields.get("scheduled task state")
        return ServiceState(
            exists=True,
            running=status == "running",
            # Older schtasks builds omit "Scheduled Task State" even with /V;
            # "Status: Disabled" is then the only signal, so fall back to it
            # rather than guessing the task is enabled.
            startup_automatic=(task_state == "enabled")
            if task_state is not None
            else status != "disabled",
        )

    def ensure_running_and_enabled(self, service: ServiceSpec) -> None:
        name = self._name(service)
        self._process.run(
            [
                "schtasks",
                "/Create",
                "/TN",
                name,
                "/TR",
                quote_task_command(self._task_command),
                "/SC",
                "ONLOGON",
                "/RL",
                "LIMITED",
                "/F",
            ]
        )
        # ONLOGON means "at the next logon"; the caller asked for it running
        # now, so start it explicitly rather than making them log out.
        self._process.run(["schtasks", "/Run", "/TN", name], check=False)

    def apply_state(self, service: ServiceSpec, state: ServiceState) -> None:
        name = self._name(service)
        if not state.exists:
            # devtunnel created the task, so revert deletes it. /End first: a
            # delete leaves an already-running instance alive, which would keep
            # a listener on the box after an uninstall claimed to have removed
            # it.
            self._process.run(["schtasks", "/End", "/TN", name], check=False)
            self._process.run(["schtasks", "/Delete", "/TN", name, "/F"], check=False)
            return

        self._process.run(
            [
                "schtasks",
                "/Change",
                "/TN",
                name,
                "/ENABLE" if state.startup_automatic else "/DISABLE",
            ],
            check=False,
        )
        self._process.run(
            ["schtasks", "/Run" if state.running else "/End", "/TN", name], check=False
        )

    # -- helpers ------------------------------------------------------------

    def _query(self, service: ServiceSpec) -> CompletedProcess:
        # /V is needed for "Scheduled Task State"; the plain /FO LIST form
        # reports Status but not whether the task is enabled, and an enabled
        # flag that always reads True would make revert unable to restore a
        # task the user had deliberately disabled.
        return self._process.run(
            ["schtasks", "/Query", "/TN", self._name(service), "/FO", "LIST", "/V"], check=False
        )

    @staticmethod
    def _parse_list_output(stdout: str) -> dict[str, str]:
        """``/FO LIST`` is ``Label: value`` lines; keys and values are folded
        to lower case so parsing survives localised casing differences."""

        fields: dict[str, str] = {}
        for line in stdout.splitlines():
            label, separator, value = line.partition(":")
            if not separator:
                continue
            fields[label.strip().lower()] = value.strip().lower()
        return fields

    @staticmethod
    def _name(service: ServiceSpec) -> str:
        assert service.windows_service_name is not None, f"{service.key} has no service name"
        return service.windows_service_name
