"""``ServiceManagerPort`` for a systemd **user** unit, via ``systemctl --user``.

The system-scope sibling (:mod:`devtunnel.infrastructure.debian.systemd`)
registers a unit in ``/etc/systemd/system`` and therefore needs root. This
adapter registers ``~/.config/systemd/user/<unit>.service`` instead, which
needs no elevation at all -- that is what keeps
``devtunnel serve --install-service`` inside the unprivileged default story.

Three design choices are worth the ink:

**The unit's ``ExecStart`` is injected, not hardcoded.** The adapter takes
``unit_command`` from the caller rather than assembling a ``tailcat serve``
argv from a binary path, a key name and an ``authorized_keys`` path. An
adapter that built its own argv would be a second place where tailcat's
pre-1.0 command line is spelled out, and the plan is explicit that argv
construction lives in exactly one method
(``TailcatTunnelProvider.build_argv``) precisely so a flag rename stays a
one-line change. This class is then genuinely a *service* manager: it knows
systemd, not tailcat, and the same class could supervise anything.

**The unit is written under ``FileSystemPort.real_user_home()``, never
``os.path.expanduser``.** Under ``sudo`` -- which ``--with-git`` and
``--system`` can both put us in -- ``~`` resolves to ``/root``, so the unit
would land in ``/root/.config/systemd/user`` where the invoking user's systemd
instance will never look for it, and the install would report success having
configured nothing.

**A failed ``loginctl enable-linger`` is surfaced, never swallowed.** Without
lingering, the user's systemd instance is torn down at logout and the tunnel
dies with it -- while ``systemctl --user is-enabled`` still cheerfully reports
``enabled``. Reporting a healthy service that will silently stop existing at
logout is the worst outcome available here: the operator walks away believing
the box is reachable and finds out only when it is not, with nothing in the
output to explain why. ``enable-linger`` needs polkit or root and routinely
fails on headless hosts (plan section 17), so it is run with ``check=False``
and its failure is recorded in :attr:`last_linger_error` for the caller to
report, alongside the :meth:`linger_enabled` query for checking the real state.
"""

from __future__ import annotations

import os
import posixpath
import shlex
from collections.abc import Sequence

from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.models import ServiceSpec

UNIT_TEMPLATE = """\
[Unit]
Description={description}
After=network-online.target

[Service]
ExecStart={exec_start}
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
"""


class SystemdUserServiceManager:
    """Satisfies ``ServiceManagerPort`` with a systemd user unit.

    ``ServiceState``'s three booleans describe this backend exactly as well as
    they describe the system one, so ``EnsureServiceStep`` and its reverter
    work against it unmodified.
    """

    def __init__(
        self,
        process: ProcessRunnerPort,
        filesystem: FileSystemPort,
        *,
        unit_command: Sequence[str],
        user: str | None = None,
    ) -> None:
        if not unit_command:
            raise ValueError("unit_command must contain at least the executable to run")
        self._process = process
        self._filesystem = filesystem
        self._unit_command = tuple(unit_command)
        self._user = user
        self.last_linger_error: str | None = None
        """Why ``loginctl enable-linger`` failed on the most recent
        :meth:`ensure_running_and_enabled`, or ``None`` if it succeeded or was
        not needed. The caller is expected to print this and suggest
        ``--system``: a unit that will not survive logout must never be
        reported as a healthy install."""

    # -- paths --------------------------------------------------------------

    def unit_path(self, service: ServiceSpec) -> str:
        return posixpath.join(self._unit_dir(), f"{self._unit(service)}.service")

    def unit_content(self, service: ServiceSpec) -> str:
        """The rendered unit file.

        ``ExecStart`` is shell-quoted because systemd splits that line on
        whitespace, so an install directory containing a space would otherwise
        silently become two arguments. ``%`` is deliberately *not* escaped to
        ``%%``: systemd specifiers such as ``%h`` are a legitimate thing for a
        caller to put in the command, and devtunnel's own callers pass an
        already-resolved absolute path (plan section 8.1).
        """

        return UNIT_TEMPLATE.format(
            description=service.display_name,
            exec_start=shlex.join(self._unit_command),
        )

    # -- ServiceManagerPort -------------------------------------------------

    def get_state(self, service: ServiceSpec) -> ServiceState:
        """Mirror of the system-scope adapter with ``--user`` added.

        Keeping the probes identical is deliberate: both backends answer the
        same three questions, so neither the step nor the journal record shape
        has to know which manager produced the state it is restoring.
        """

        unit = self._unit(service)
        exists = self._process.run(["systemctl", "--user", "cat", unit], check=False).ok
        if not exists:
            return ServiceState(exists=False, running=False, startup_automatic=False)
        active = self._process.run(["systemctl", "--user", "is-active", unit], check=False)
        is_enabled = self._process.run(["systemctl", "--user", "is-enabled", unit], check=False)
        return ServiceState(
            exists=True,
            running=active.stdout.strip() == "active",
            startup_automatic=is_enabled.stdout.strip() == "enabled",
        )

    def ensure_running_and_enabled(self, service: ServiceSpec) -> None:
        unit = self._unit(service)
        self.last_linger_error = None

        self._filesystem.ensure_dir(self._unit_dir())
        path = self.unit_path(service)
        self._filesystem.write_text(path, self.unit_content(service), mode=0o644)
        # Ownership is corrected for the same reason the path came from
        # real_user_home(): a unit written while elevated would otherwise sit
        # root-owned inside the user's own config tree.
        self._filesystem.take_ownership_for_real_user(path)

        self._process.run(["systemctl", "--user", "daemon-reload"])
        self._process.run(["systemctl", "--user", "enable", "--now", unit])
        self._enable_linger(unit)

    def apply_state(self, service: ServiceSpec, state: ServiceState) -> None:
        unit = self._unit(service)
        if not state.exists:
            # devtunnel created this unit, so revert removes it outright rather
            # than merely stopping it -- a disabled unit file left behind in
            # the user's config tree is exactly the residue this project exists
            # to prevent.
            self._process.run(["systemctl", "--user", "disable", "--now", unit], check=False)
            self._filesystem.remove_file(self.unit_path(service))
            self._process.run(["systemctl", "--user", "daemon-reload"], check=False)
            self._filesystem.remove_dir_if_empty(self._unit_dir())
        else:
            self._process.run(
                ["systemctl", "--user", "enable" if state.startup_automatic else "disable", unit],
                check=False,
            )
            self._process.run(
                ["systemctl", "--user", "start" if state.running else "stop", unit], check=False
            )
        self._release_linger(unit)

    # -- lingering ----------------------------------------------------------

    def linger_enabled(self, user: str | None = None) -> bool:
        """Whether this user's systemd instance survives logout.

        ``--property=Linger`` rather than ``--value``: the latter needs systemd
        243+, and this has to answer honestly on older Debian stable too. A
        user with no session at all makes ``show-user`` exit non-zero, which is
        correctly read as "not lingering".
        """

        target = user or self._resolve_user()
        result = self._process.run(
            ["loginctl", "show-user", target, "--property=Linger"], check=False
        )
        return result.ok and result.stdout.strip().lower().endswith("=yes")

    def _enable_linger(self, unit: str) -> None:
        user = self._resolve_user()
        if self.linger_enabled(user):
            # Already on, and not by us. Skipping the call is what makes the
            # marker file below an accurate record of what devtunnel changed.
            return

        result = self._process.run(["loginctl", "enable-linger", user], check=False)
        if not result.ok:
            detail = (result.stderr or result.stdout).strip()
            self.last_linger_error = (
                f"loginctl enable-linger {user} failed (exit {result.returncode})"
                f"{': ' + detail if detail else ''}. The unit is installed and enabled, but this "
                "user's systemd instance is torn down at logout, so the tunnel will not survive "
                "logging out or rebooting. Enabling lingering needs polkit or root; re-run with "
                "--system to register a machine-scope service instead."
            )
            return

        # Record that *devtunnel* turned lingering on, so revert can turn it
        # back off without ever disabling it for a user who already had it.
        # This has to be on disk rather than an attribute: install and
        # uninstall are separate process invocations, and ServiceState -- all
        # the journal carries for a service record -- has exactly three
        # booleans and must not grow a fourth, or EnsureServiceStep and every
        # other ServiceManagerPort implementation would have to change with it.
        marker = self._linger_marker(unit)
        self._filesystem.ensure_dir(posixpath.dirname(marker))
        self._filesystem.write_text(
            marker,
            f"devtunnel enabled lingering for {user} when it installed {unit}.service\n",
        )
        self._filesystem.take_ownership_for_real_user(marker)

    def _release_linger(self, unit: str) -> None:
        marker = self._linger_marker(unit)
        if not self._filesystem.exists(marker):
            # No marker means lingering was already on before devtunnel ran, or
            # could never be enabled at all. Never touch what we did not
            # install: disabling it here would quietly kill every other
            # long-running user service on the box.
            return
        self._process.run(["loginctl", "disable-linger", self._resolve_user()], check=False)
        self._filesystem.remove_file(marker)
        self._filesystem.remove_dir_if_empty(posixpath.dirname(marker))

    def _linger_marker(self, unit: str) -> str:
        return posixpath.join(
            self._filesystem.real_user_home(),
            ".local",
            "share",
            "devtunnel",
            f"{unit}.linger-owned",
        )

    # -- helpers ------------------------------------------------------------

    def _unit_dir(self) -> str:
        # posixpath, not os.path: these are paths on a Debian box no
        # matter which platform the process (or the test suite) runs on.
        return posixpath.join(self._filesystem.real_user_home(), ".config", "systemd", "user")

    def _resolve_user(self) -> str:
        """The account whose systemd instance owns the unit.

        ``SUDO_USER`` first, for the same reason the home directory is resolved
        through the port: under ``sudo`` the ambient identity is root, and
        enabling lingering for root does nothing whatsoever for the user whose
        unit this is.
        """

        if self._user:
            return self._user
        for variable in ("SUDO_USER", "USER", "LOGNAME", "USERNAME"):
            value = os.environ.get(variable)
            if value:
                return value
        return posixpath.basename(self._filesystem.real_user_home().rstrip("/\\"))

    @staticmethod
    def _unit(service: ServiceSpec) -> str:
        assert service.systemd_unit is not None, f"{service.key} has no systemd unit"
        return service.systemd_unit
