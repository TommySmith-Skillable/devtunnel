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
from devtunnel.application.ports.service_manager import ServiceManagerPort
from devtunnel.domain.models import PlatformId, Scope


class PlatformToolkit(Protocol):
    platform: PlatformId
    package_bootstrap: PackageManagerBootstrapPort

    def service_manager_for(self, scope: Scope) -> ServiceManagerPort:
        """The service manager for ``scope`` on this platform.

        Two coexist by design: a ``USER``-scope manager (systemd *user* unit /
        per-user Scheduled Task) that needs no elevation, and a ``MACHINE``-scope
        one (system unit / Windows service) that does. Which a step gets is
        decided by the :class:`~devtunnel.domain.models.ServiceSpec` it carries,
        so the step itself never branches on scope.
        """

    @property
    def service_manager(self) -> ServiceManagerPort:
        """Alias for ``service_manager_for(Scope.MACHINE)``, kept so callers
        that only ever meant "the system one" read plainly."""

    def manager_for(self, package_key: str) -> PackageManagerPort:
        """The package manager responsible for ``package_key`` on this
        platform.

        On Windows this is Chocolatey for ``git`` but the Windows-capability
        manager for ``openssh-client``, which ships as an optional feature
        rather than a package; on Debian it is always apt.
        """

    def is_elevated(self) -> bool:
        """Whether the current process has Administrator/root privileges.

        Only consulted when a plan actually contains a ``Scope.MACHINE`` step
        -- the default install has none, and must not demand elevation it does
        not need.
        """

    def elevation_hint(self) -> str:
        """A human-readable instruction for gaining the privileges above."""
