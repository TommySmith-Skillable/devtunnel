"""The Abstract Factory interface for a platform's consistent family of
provisioning adapters.

``PlanBuilder`` and the revert path depend only on this Protocol, never on
:mod:`devtunnel.infrastructure.toolkits` directly -- the concrete
``WindowsToolkit``/``DebianToolkit`` are chosen once, in the composition root
(:mod:`devtunnel.container`), and handed in as ``ExecutionContext.toolkit``.
Adding a platform (RHEL, Arch, ...) means writing one new class that satisfies
this Protocol; nothing in ``application`` changes.
"""

from __future__ import annotations

from typing import Protocol

from devtunnel.application.ports.package_bootstrap import PackageManagerBootstrapPort
from devtunnel.application.ports.package_manager import PackageManagerPort
from devtunnel.application.ports.repository_provider import RepositoryProviderPort
from devtunnel.application.ports.service_manager import ServiceManagerPort
from devtunnel.domain.models import PlatformId


class PlatformToolkit(Protocol):
    platform: PlatformId
    package_bootstrap: PackageManagerBootstrapPort
    service_manager: ServiceManagerPort
    repository_provider: RepositoryProviderPort | None
    """``None`` on platforms (e.g. Windows) whose packages need no repository
    setup of their own."""

    def manager_for(self, package_key: str) -> PackageManagerPort:
        """The package manager responsible for ``package_key`` on this
        platform (e.g. Chocolatey for "git"/"ngrok" but the Windows-capability
        manager for "openssh-server" on Windows; always apt on Debian)."""

    def is_elevated(self) -> bool:
        """Whether the current process has the privileges this platform's
        installs require (Administrator / root)."""

    def elevation_hint(self) -> str:
        """A human-readable instruction for gaining the privileges above."""
