"""Use case: report what devtunnel has done to this machine so far."""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application.context import ExecutionContext
from devtunnel.domain.journal import ChangeRecord
from devtunnel.domain.models import PlatformId


@dataclass(frozen=True, slots=True)
class StatusReport:
    platform: PlatformId
    journal_location: str
    records: list[ChangeRecord]
    elevated: bool


class ReportStatusUseCase:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    def execute(self) -> StatusReport:
        return StatusReport(
            platform=self._ctx.platform,
            journal_location=self._ctx.journal.location(),
            records=self._ctx.journal.load(),
            elevated=self._ctx.toolkit.is_elevated(),
        )
