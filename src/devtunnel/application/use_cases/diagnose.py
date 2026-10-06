"""Use case: detect drift between the journal and the machine's real state.

``doctor`` catches the case the journal alone cannot: something devtunnel
installed was later changed or removed by other means. It never fixes drift
automatically -- it only reports it, since automatically resolving it could
mean silently reinstalling something the user chose to remove.
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
                if not self._ctx.toolkit.manager_for(key).is_installed(package):
                    findings.append(
                        DriftFinding(
                            record.id,
                            record.target,
                            "recorded as installed by devtunnel but no longer present",
                        )
                    )

            elif record.kind is ChangeKind.BINARY_INSTALLED:
                path = record.details.get("path")
                if path and not self._ctx.binary_installer.is_installed(path):
                    findings.append(
                        DriftFinding(record.id, record.target, f"{path} is no longer present")
                    )

            elif record.kind is ChangeKind.KEY_GENERATED:
                name = record.target.removeprefix("tailcatkey:")
                if not self._ctx.tunnel_provider.has_key(name):
                    findings.append(
                        DriftFinding(record.id, record.target, "tailcat key no longer exists")
                    )

            elif record.kind is ChangeKind.FILE_MODIFIED:
                path = record.details.get("path") or record.prior_state.get("path")
                if path and not self._ctx.filesystem.exists(path):
                    findings.append(
                        DriftFinding(record.id, record.target, f"{path} is no longer present")
                    )

            elif record.kind is ChangeKind.SERVICE_STATE_CHANGED:
                key = record.target.removeprefix("service:")
                service = catalog.SERVICES_BY_KEY.get(key)
                if service is None:
                    continue
                state = self._ctx.toolkit.service_manager_for(service.scope).get_state(service)
                if not state.exists:
                    findings.append(
                        DriftFinding(record.id, record.target, "service no longer exists")
                    )
                elif not state.running:
                    # The listener is the thing a user most wants to know the
                    # truth about.
                    findings.append(
                        DriftFinding(record.id, record.target, "service is no longer running")
                    )

        return DiagnosisReport(findings)
