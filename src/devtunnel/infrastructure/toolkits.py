"""Abstract Factory: bundles each platform's consistent family of adapters.

``WindowsToolkit`` and ``DebianToolkit`` both satisfy
:class:`~devtunnel.application.ports.toolkit.PlatformToolkit`. The
composition root (:mod:`devtunnel.container`) picks exactly one, based on
:func:`devtunnel.infrastructure.platform_detect.detect_platform`, and nothing
above this module ever branches on platform again.

Two things changed with tailcat. ``repository_provider`` is gone -- the apt
repository existed solely to install the tunnel package, and a
checksum-verified release binary needs no repository on either platform. And each toolkit now holds
*two* service managers rather than one, because the whole point of D3 is that
the tunnel can run as a user-scope unit that needs no elevation, with the
system-scope manager kept for ``--system``.
"""

from __future__ import annotations

import os
from collections.abc import Sequence

from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.package_manager import PackageManagerPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.service_manager import ServiceManagerPort
from devtunnel.domain.models import PlatformId, Scope
from devtunnel.infrastructure.debian.apt import AptAlreadyPresentBootstrap, AptPackageManager
from devtunnel.infrastructure.debian.systemd import SystemdServiceManager
from devtunnel.infrastructure.debian.systemd_user import SystemdUserServiceManager
from devtunnel.infrastructure.windows.chocolatey import (
    ChocolateyBootstrap,
    ChocolateyPackageManager,
)
from devtunnel.infrastructure.windows.elevation import is_admin
from devtunnel.infrastructure.windows.sc_service import WindowsServiceManager
from devtunnel.infrastructure.windows.scheduled_task import WindowsScheduledTaskManager
from devtunnel.infrastructure.windows.windows_capability import WindowsCapabilityManager

_WINDOWS_CAPABILITY_PACKAGES = frozenset({"openssh-client"})
"""Shrunk from two entries to one. tailcat serves SSH itself, so the OpenSSH
*Server* capability left the catalog with D1; the *client* stays, because
``tailcat ssh`` wraps the stock ssh client. One live caller is still a caller
-- the capability branch earns its place."""


class WindowsToolkit:
    platform = PlatformId.WINDOWS

    def __init__(self, process: ProcessRunnerPort, *, tunnel_command: Sequence[str] = ()) -> None:
        self.package_bootstrap = ChocolateyBootstrap(process)
        self._choco = ChocolateyPackageManager(process)
        self._capability = WindowsCapabilityManager(process)
        self._system_service = WindowsServiceManager(process)
        self._user_service = WindowsScheduledTaskManager(
            process, task_command=tuple(tunnel_command) or ("tailcat", "serve", "ssh")
        )

    def service_manager_for(self, scope: Scope) -> ServiceManagerPort:
        return self._user_service if scope is Scope.USER else self._system_service

    @property
    def service_manager(self) -> ServiceManagerPort:
        return self._system_service

    def manager_for(self, package_key: str) -> PackageManagerPort:
        if package_key in _WINDOWS_CAPABILITY_PACKAGES:
            return self._capability
        return self._choco

    def is_elevated(self) -> bool:
        return is_admin()

    def elevation_hint(self) -> str:
        return "Re-run this command from an elevated (Run as Administrator) terminal."


class DebianToolkit:
    platform = PlatformId.DEBIAN

    def __init__(
        self,
        process: ProcessRunnerPort,
        filesystem: FileSystemPort,
        *,
        tunnel_command: Sequence[str] = (),
    ) -> None:
        self.package_bootstrap = AptAlreadyPresentBootstrap()
        self._apt = AptPackageManager(process)
        self._system_service = SystemdServiceManager(process)
        self._user_service = SystemdUserServiceManager(
            process,
            filesystem,
            unit_command=tuple(tunnel_command) or ("tailcat", "serve", "ssh"),
        )

    def service_manager_for(self, scope: Scope) -> ServiceManagerPort:
        return self._user_service if scope is Scope.USER else self._system_service

    @property
    def service_manager(self) -> ServiceManagerPort:
        return self._system_service

    def manager_for(self, package_key: str) -> PackageManagerPort:
        return self._apt

    def is_elevated(self) -> bool:
        geteuid = getattr(os, "geteuid", None)
        return geteuid is not None and geteuid() == 0

    def elevation_hint(self) -> str:
        return "Re-run this command with 'sudo'."
