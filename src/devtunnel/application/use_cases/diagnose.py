"""Use case: detect drift between the journal and the machine's real state.

``doctor`` catches the case the journal alone cannot: something devtunnel
installed was later changed or removed by other means (a colleague ran
``apt remove ngrok``, an org policy script disabled sshd again). It never
fixes drift automatically -- it only reports it, since automatically
resolving it could mean silently reinstalling something the user chose to
remove.
"""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application import catalog
from devtunnel.application.context import ExecutionContext
from devtunnel.domain.journal import ChangeKind, RecordStatus


@dataclass(frozen=True, slots=True)
class DriftFinding:
    record_id: str
    target: str
    message: str


@dataclass(frozen=True, slots=True)
class DiagnosisReport:
    findings: list[DriftFinding]

    @property
    def clean(self) -> bool:
        return not self.findings


class DiagnoseUseCase:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    def execute(self) -> DiagnosisReport:
        findings: list[DriftFinding] = []

        for record in self._ctx.journal.load():
            if record.status is not RecordStatus.APPLIED:
                continue

            if record.kind is ChangeKind.PACKAGE_INSTALLED:
                key = record.target.removeprefix("package:")
                package = catalog.PACKAGES_BY_KEY.get(key)
                if package is None:
                    continue
                manager = self._ctx.toolkit.manager_for(key)
                if not manager.is_installed(package):
                    findings.append(
                        DriftFinding(
                            record.id,
                            record.target,
                            "recorded as installed by devtunnel but no longer present",
                        )
                    )

            elif record.kind is ChangeKind.SERVICE_STATE_CHANGED:
                key = record.target.removeprefix("service:")
                service = catalog.SERVICES_BY_KEY.get(key)
                if service is None:
                    continue
                state = self._ctx.toolkit.service_manager.get_state(service)
                if not state.exists:
                    findings.append(
                        DriftFinding(record.id, record.target, "service no longer exists")
                    )
                elif not state.running:
                    findings.append(
                        DriftFinding(record.id, record.target, "service is no longer running")
                    )

        return DiagnosisReport(findings)
