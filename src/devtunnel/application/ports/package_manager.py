"""Port for installing/removing a single named package.

The same interface fronts Chocolatey packages, apt packages, *and* Windows
optional-feature capabilities (OpenSSH Client/Server) -- from a step's point
of view "ensure this named thing is present" is one operation, however the
underlying platform implements it. See
:class:`devtunnel.infrastructure.windows.windows_capability.WindowsCapabilityManager`
for the capability-flavoured implementation.
"""

from __future__ import annotations

from typing import Protocol

from devtunnel.domain.models import PackageSpec, PlatformId


class PackageManagerPort(Protocol):
    """Manages the presence of packages for one platform's package manager."""

    platform: PlatformId

    def is_installed(self, package: PackageSpec) -> bool: ...

    def install(self, package: PackageSpec) -> None:
        """Install ``package``. Must be safe to call only when not installed."""

    def uninstall(self, package: PackageSpec) -> None:
        """Remove ``package``. Must be safe to call only when installed."""
