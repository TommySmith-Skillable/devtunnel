"""Use case: reverse every journaled change, most recent first.

This is where the reversibility guarantee is enforced end to end: records
never touched by devtunnel (``SKIPPED_PREEXISTING``) are skipped, records
already reverted are skipped (repeated ``uninstall`` runs are safe no-ops),
and a failed revert halts the walk by default so a partial failure is visible
rather than silently ignored -- ``force=True`` continues past failures
instead, recording each as ``REVERT_FAILED`` for a later retry.

The journal it walks is the **merged** view across both scopes, in true
chronological order, so the resulting unwind is:

    peers -> ssh identity -> tailcat key -> authorized_keys -> service
          -> binary -> openssh-client -> git -> Chocolatey

Every ordering constraint falls out of reverse-chronological replay for free.
The service stops before the binary it runs is deleted, because the service was
registered after the binary was installed. Peers unwind first, because they were
added last -- which means access is revoked before the machinery that enforces
it is removed, and that is the order you want if an uninstall is interrupted
half way through.

Elevation is now demanded only when the records actually needing it are present:
a purely user-scope install uninstalls without Administrator or sudo.
"""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application.context import ExecutionContext
from devtunnel.application.revert import StepReverter
from devtunnel.domain.errors import ElevationRequiredError
from devtunnel.domain.events import Phase
from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.domain.models import Scope
from devtunnel.domain.plan import StepOutcome, StepStatus


@dataclass(frozen=True, slots=True)
class UninstallReport:
    outcomes: list[StepOutcome]
    remaining_failures: int

    @property
    def fully_reverted(self) -> bool:
        return self.remaining_failures == 0


class UninstallEnvironmentUseCase:
    def __init__(self, ctx: ExecutionContext, reverter: StepReverter | None = None) -> None:
        self._ctx = ctx
        self._reverter = reverter or StepReverter(ctx.platform)

    def execute(
        self, *, force: bool = False, keep: frozenset[str] = frozenset()
    ) -> UninstallReport:
        self._ctx.phase = Phase.UNINSTALL
        records = self._ctx.journal.load()
        pending = [r for r in records if r.needs_revert and not self._is_kept(r, keep)]

        if not self._ctx.dry_run and any(self._needs_elevation(r) for r in pending):
            if not self._ctx.toolkit.is_elevated():
                raise ElevationRequiredError(self._ctx.toolkit.elevation_hint())

        outcomes: list[StepOutcome] = []
        remaining_failures = 0
        for record in reversed(records):
            if not record.needs_revert or self._is_kept(record, keep):
                continue

            step = self._reverter.for_record(record)
            outcome = step.revert(self._ctx, record)
            outcomes.append(outcome)

            if outcome.status is StepStatus.REVERT_FAILED:
                remaining_failures += 1
                if not force:
                    break

        if not self._ctx.dry_run and not any(r.needs_revert for r in self._ctx.journal.load()):
            self._ctx.journal.clear()

        return UninstallReport(outcomes, remaining_failures)

    @staticmethod
    def _needs_elevation(record: ChangeRecord) -> bool:
        """Whether reverting this one record writes machine-scope state.

        Read from the scope the record was stamped with at apply time. An
        ngrok-era record predates the stamp and is treated as machine-scope,
        which is correct -- everything that era wrote needed elevation.
        """

        scope = record.details.get("scope")
        if scope is None:
            return record.kind in (
                ChangeKind.APT_REPO_ADDED,
                ChangeKind.WINDOWS_CAPABILITY_ADDED,
                ChangeKind.PACKAGE_INSTALLED,
                ChangeKind.PKGMGR_BOOTSTRAPPED,
            )
        return scope == Scope.MACHINE.value

    @staticmethod
    def _is_kept(record: ChangeRecord, keep: frozenset[str]) -> bool:
        if not keep or record.kind is not ChangeKind.PACKAGE_INSTALLED:
            return False
        return record.target.removeprefix("package:") in keep
