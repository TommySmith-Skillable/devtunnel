"""Chocolatey adapters: bootstrapping the package manager itself, and
installing/removing the packages it manages (git, ngrok).

:class:`ChocolateyBootstrap` exists because Chocolatey has no official
uninstaller: reversing it means deleting its install directory, clearing the
``ChocolateyInstall`` machine environment variable, and stripping its entry
from the machine ``PATH`` -- exactly the "gotcha" flagged in the architecture
plan, implemented deliberately rather than left for uninstall to improvise.
"""

from __future__ import annotations

from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.domain.models import PackageSpec, PlatformId

_INSTALL_SCRIPT_URL = "https://community.chocolatey.org/install.ps1"
_DEFAULT_INSTALL_DIR = r"C:\ProgramData\chocolatey"


class ChocolateyBootstrap:
    def __init__(self, process: ProcessRunnerPort) -> None:
        self._process = process

    def is_bootstrapped(self) -> bool:
        return self._process.which("choco") is not None

    def bootstrap(self) -> dict:
        install_dir = _DEFAULT_INSTALL_DIR
        script = (
            "[System.Net.ServicePointManager]::SecurityProtocol = "
            "[System.Net.ServicePointManager]::SecurityProtocol -bor 3072; "
            f"iex ((New-Object System.Net.WebClient).DownloadString('{_INSTALL_SCRIPT_URL}'))"
        )
        self._process.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script]
        )
        return {"install_dir": install_dir}

    def teardown(self, details: dict) -> None:
        install_dir = details.get("install_dir", _DEFAULT_INSTALL_DIR)
        script = (
            f"if (Test-Path '{install_dir}') {{ Remove-Item -Recurse -Force '{install_dir}' }}; "
            "[Environment]::SetEnvironmentVariable('ChocolateyInstall', $null, 'Machine'); "
            "$p = [Environment]::GetEnvironmentVariable('Path', 'Machine'); "
            "$parts = $p -split ';' | Where-Object { $_ -notlike '*chocolatey*' }; "
            "[Environment]::SetEnvironmentVariable('Path', ($parts -join ';'), 'Machine')"
        )
        self._process.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            check=False,
        )


class ChocolateyPackageManager:
    platform = PlatformId.WINDOWS

    def __init__(self, process: ProcessRunnerPort) -> None:
        self._process = process

    def is_installed(self, package: PackageSpec) -> bool:
        assert package.choco_id is not None
        result = self._process.run(
            ["choco", "list", "--local-only", "--exact", package.choco_id], check=False
        )
        return result.ok and package.choco_id.lower() in result.stdout.lower()

    def install(self, package: PackageSpec) -> None:
        assert package.choco_id is not None
        self._process.run(["choco", "install", package.choco_id, "-y"])

    def uninstall(self, package: PackageSpec) -> None:
        assert package.choco_id is not None
        self._process.run(["choco", "uninstall", package.choco_id, "-y"], check=False)
