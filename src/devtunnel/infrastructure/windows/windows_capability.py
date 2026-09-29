"""``PackageManagerPort`` over Windows optional-feature capabilities.

Used for OpenSSH Client/Server, which Windows ships as capabilities rather
than as installable packages -- ``Add-WindowsCapability`` /
``Remove-WindowsCapability``, not Chocolatey. Same port, same
``EnsurePackageStep``, different mechanism underneath: exactly what the
``PackageManagerPort`` abstraction is for.
"""

from __future__ import annotations

from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.domain.models import PackageSpec, PlatformId


class WindowsCapabilityManager:
    platform = PlatformId.WINDOWS

    def __init__(self, process: ProcessRunnerPort) -> None:
        self._process = process

    def is_installed(self, package: PackageSpec) -> bool:
        cap_id = self._capability_id(package)
        script = f"(Get-WindowsCapability -Online -Name '{cap_id}').State"
        result = self._process.run(["powershell", "-NoProfile", "-Command", script], check=False)
        return result.ok and "Installed" in result.stdout

    def install(self, package: PackageSpec) -> None:
        cap_id = self._capability_id(package)
        script = f"Add-WindowsCapability -Online -Name '{cap_id}'"
        self._process.run(["powershell", "-NoProfile", "-Command", script])

    def uninstall(self, package: PackageSpec) -> None:
        cap_id = self._capability_id(package)
        script = f"Remove-WindowsCapability -Online -Name '{cap_id}'"
        self._process.run(["powershell", "-NoProfile", "-Command", script], check=False)

    @staticmethod
    def _capability_id(package: PackageSpec) -> str:
        assert package.windows_capability_id is not None, (
            f"{package.key} has no windows_capability_id"
        )
        return package.windows_capability_id
