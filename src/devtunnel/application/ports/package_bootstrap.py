"""Port for bootstrapping a package manager itself (e.g. Chocolatey).

Not part of the plan's original port list, but earned: unlike an ordinary
package, a package manager has no package manager to install it, has no
official uninstaller, and reversing it means undoing PATH/environment-variable
changes rather than running an uninstall command. That lifecycle is distinct
enough from :class:`~devtunnel.application.ports.package_manager.PackageManagerPort`
to deserve its own small port. Debian's toolkit uses the trivial
:class:`~devtunnel.infrastructure.debian.apt.AptAlreadyPresentBootstrap` Null
Object, since apt ships with the OS.
"""

from __future__ import annotations

from typing import Protocol


class PackageManagerBootstrapPort(Protocol):
    def is_bootstrapped(self) -> bool: ...

    def bootstrap(self) -> dict:
        """Install the package manager itself.

        Returns a ``details`` dict to journal (e.g. install directory), used
        later by :meth:`teardown`.
        """

    def teardown(self, details: dict) -> None:
        """Reverse :meth:`bootstrap` using the journaled details."""
