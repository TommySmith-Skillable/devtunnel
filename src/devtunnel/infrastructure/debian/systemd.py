"""``ServiceManagerPort`` for Debian, via ``systemctl``."""

from __future__ import annotations

from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.models import ServiceSpec


class SystemdServiceManager:
    def __init__(self, process: ProcessRunnerPort) -> None:
        self._process = process

    def get_state(self, service: ServiceSpec) -> ServiceState:
        unit = self._unit(service)
        exists = self._process.run(["systemctl", "cat", unit], check=False).ok
        if not exists:
            return ServiceState(exists=False, running=False, startup_automatic=False)
        active = self._process.run(["systemctl", "is-active", unit], check=False)
        is_enabled = self._process.run(["systemctl", "is-enabled", unit], check=False)
        running = active.stdout.strip() == "active"
        enabled = is_enabled.stdout.strip() == "enabled"
        return ServiceState(exists=True, running=running, startup_automatic=enabled)

    def ensure_running_and_enabled(self, service: ServiceSpec) -> None:
        self._process.run(["systemctl", "enable", "--now", self._unit(service)])

    def apply_state(self, service: ServiceSpec, state: ServiceState) -> None:
        unit = self._unit(service)
        self._process.run(
            ["systemctl", "enable" if state.startup_automatic else "disable", unit], check=False
        )
        self._process.run(["systemctl", "start" if state.running else "stop", unit], check=False)

    @staticmethod
    def _unit(service: ServiceSpec) -> str:
        assert service.systemd_unit is not None, f"{service.key} has no systemd unit"
        return service.systemd_unit
