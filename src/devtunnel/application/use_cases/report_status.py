"""Use case: report what devtunnel has done to this machine so far.

Reads the merged journal, so a machine with both an unprivileged install and a
``--with-git`` or ``--system`` one sees a single chronological history rather
than having to be asked twice.

It also answers the question plan section 13.7 insists on: **is there a
listener?** Service mode leaves a process accepting connections, and ``status``
must state plainly whether a tunnel service is registered and running rather
than making the user infer it from a list of journal records.
"""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application import catalog
from devtunnel.application.context import ExecutionContext
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.journal import ChangeRecord
from devtunnel.domain.models import PlatformId


@dataclass(frozen=True, slots=True)
class StatusReport:
    platform: PlatformId
    journal_location: str
    records: list[ChangeRecord]
    elevated: bool
    tunnel_service: ServiceState
    address: str | None

    @property
    def listening(self) -> bool:
        return self.tunnel_service.exists and self.tunnel_service.running


class ReportStatusUseCase:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    def execute(self) -> StatusReport:
        return StatusReport(
            platform=self._ctx.platform,
            journal_location=self._ctx.journal.location(),
            records=self._ctx.journal.load(),
            elevated=self._ctx.toolkit.is_elevated(),
            tunnel_service=self._service_state(),
            address=self._address(),
        )

    def _service_state(self) -> ServiceState:
        service = catalog.TUNNEL_SERVICE
        try:
            return self._ctx.toolkit.service_manager_for(service.scope).get_state(service)
        except Exception:  # noqa: BLE001 - status must never fail on a probe
            return ServiceState(exists=False, running=False, startup_automatic=False)

    def _address(self) -> str | None:
        try:
            return self._ctx.tunnel_provider.address_for(catalog.DEFAULT_SERVER_KEY)
        except Exception:  # noqa: BLE001
            return None
