"""``ServiceManagerPort`` for Windows services, via PowerShell.

Named ``sc_service`` for parity with the Debian ``systemd`` adapter, though
the mechanism is PowerShell's ``Get-Service``/``Set-Service``/``Start-Service``
plus a CIM lookup for the startup type (``Get-Service`` alone does not expose
that reliably on Windows PowerShell 5.1, which still ships by default on
Windows 10/11).
"""

from __future__ import annotations

from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.models import ServiceSpec


class WindowsServiceManager:
    def __init__(self, process: ProcessRunnerPort) -> None:
        self._process = process

    def get_state(self, service: ServiceSpec) -> ServiceState:
        name = self._name(service)
        script = (
            f"$s = Get-Service -Name '{name}' -ErrorAction SilentlyContinue; "
            "if ($s) { "
            f"$m = (Get-CimInstance -ClassName Win32_Service -Filter \"Name='{name}'\").StartMode; "
            "Write-Output \"$($s.Status)|$m\" "
            "} else { Write-Output 'NONE|NONE' }"
        )
        result = self._run_ps(script, check=False)
        if not result.ok or not result.stdout.strip():
            return ServiceState(exists=False, running=False, startup_automatic=False)
        status, _, startup = result.stdout.strip().partition("|")
        return ServiceState(
            exists=status != "NONE",
            running=status == "Running",
            startup_automatic=startup.strip().lower() == "auto",
        )

    def ensure_running_and_enabled(self, service: ServiceSpec) -> None:
        name = self._name(service)
        self._run_ps(f"Set-Service -Name '{name}' -StartupType Automatic")
        self._run_ps(f"Start-Service -Name '{name}'", check=False)

    def apply_state(self, service: ServiceSpec, state: ServiceState) -> None:
        name = self._name(service)
        startup_type = "Automatic" if state.startup_automatic else "Manual"
        self._run_ps(f"Set-Service -Name '{name}' -StartupType {startup_type}", check=False)
        verb = "Start-Service" if state.running else "Stop-Service -Force"
        self._run_ps(f"{verb} -Name '{name}'", check=False)

    def _run_ps(self, script: str, *, check: bool = True):
        return self._process.run(["powershell", "-NoProfile", "-Command", script], check=check)

    @staticmethod
    def _name(service: ServiceSpec) -> str:
        assert service.windows_service_name is not None, f"{service.key} has no service name"
        return service.windows_service_name
