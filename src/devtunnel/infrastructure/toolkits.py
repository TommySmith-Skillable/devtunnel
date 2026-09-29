"""Abstract Factory: bundles each platform's consistent family of adapters.

``WindowsToolkit`` and ``DebianToolkit`` both satisfy
:class:`~devtunnel.application.ports.toolkit.PlatformToolkit`. The
composition root (:mod:`devtunnel.container`) picks exactly one, based on
:func:`devtunnel.infrastructure.platform_detect.detect_platform`, and nothing
above this module ever branches on platform again.
"""

from __future__ import annotations

import os

from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.package_manager import PackageManagerPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.domain.models import PlatformId
from devtunnel.infrastructure.debian.apt import AptAlreadyPresentBootstrap, AptPackageManager
from devtunnel.infrastructure.debian.apt_repository import AptNgrokRepository
from devtunnel.infrastructure.debian.systemd import SystemdServiceManager
from devtunnel.infrastructure.windows.chocolatey import (
    ChocolateyBootstrap,
    ChocolateyPackageManager,
)
from devtunnel.infrastructure.windows.elevation import is_admin
from devtunnel.infrastructure.windows.sc_service import WindowsServiceManager
from devtunnel.infrastructure.windows.windows_capability import WindowsCapabilityManager

_WINDOWS_CAPABILITY_PACKAGES = frozenset({"openssh-client", "openssh-server"})


class WindowsToolkit:
    platform = PlatformId.WINDOWS

    def __init__(self, process: ProcessRunnerPort) -> None:
        self.package_bootstrap = ChocolateyBootstrap(process)
        self.service_manager = WindowsServiceManager(process)
        self.repository_provider = None  # Chocolatey needs no repository setup
        self._choco = ChocolateyPackageManager(process)
        self._capability = WindowsCapabilityManager(process)

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

    def __init__(self, process: ProcessRunnerPort, filesystem: FileSystemPort) -> None:
        self.package_bootstrap = AptAlreadyPresentBootstrap()
        self.service_manager = SystemdServiceManager(process)
        self.repository_provider = AptNgrokRepository(process, filesystem)
        self._apt = AptPackageManager(process)

    def manager_for(self, package_key: str) -> PackageManagerPort:
        return self._apt

    def is_elevated(self) -> bool:
        geteuid = getattr(os, "geteuid", None)
        return geteuid is not None and geteuid() == 0

    def elevation_hint(self) -> str:
        return "Re-run this command with 'sudo'."
