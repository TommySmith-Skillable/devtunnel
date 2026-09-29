"""apt adapters: a Null Object bootstrap (apt ships with Debian) and the
real :class:`PackageManagerPort` implementation.

Uninstall always ``purge``s exactly the recorded package -- never
``apt-get autoremove``, which would reach beyond what devtunnel itself
installed and could remove dependencies something else on the machine still
needs.
"""

from __future__ import annotations

from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.domain.models import PackageSpec, PlatformId


class AptAlreadyPresentBootstrap:
    """Null Object: apt ships with the OS, so there is nothing to bootstrap
    or ever tear down."""

    def is_bootstrapped(self) -> bool:
        return True

    def bootstrap(self) -> dict:
        return {}

    def teardown(self, details: dict) -> None:
        return None


class AptPackageManager:
    platform = PlatformId.DEBIAN

    def __init__(self, process: ProcessRunnerPort) -> None:
        self._process = process

    def is_installed(self, package: PackageSpec) -> bool:
        assert package.apt_id is not None
        result = self._process.run(
            ["dpkg-query", "-W", "-f=${Status}", package.apt_id], check=False
        )
        return result.ok and "install ok installed" in result.stdout

    def install(self, package: PackageSpec) -> None:
        assert package.apt_id is not None
        self._process.run(["apt-get", "install", "-y", package.apt_id])

    def uninstall(self, package: PackageSpec) -> None:
        assert package.apt_id is not None
        self._process.run(["apt-get", "purge", "-y", package.apt_id], check=False)
