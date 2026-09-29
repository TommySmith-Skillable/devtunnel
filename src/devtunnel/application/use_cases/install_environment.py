"""Use case: provision git, ngrok and SSH per the resolved settings."""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application.context import ExecutionContext
from devtunnel.application.plan_builder import InstallSettings, PlanBuilder
from devtunnel.domain.errors import ElevationRequiredError
from devtunnel.domain.events import Phase
from devtunnel.domain.plan import StepOutcome


@dataclass(frozen=True, slots=True)
class InstallReport:
    outcomes: list[StepOutcome]

    @property
    def failed(self) -> bool:
        return any(o.status.value == "failed" for o in self.outcomes)


class InstallEnvironmentUseCase:
    def __init__(self, ctx: ExecutionContext, plan_builder: PlanBuilder) -> None:
        self._ctx = ctx
        self._plan_builder = plan_builder

    def execute(self, settings: InstallSettings) -> InstallReport:
        if not self._ctx.dry_run and not self._ctx.toolkit.is_elevated():
            raise ElevationRequiredError(self._ctx.toolkit.elevation_hint())

        self._ctx.phase = Phase.INSTALL
        plan = self._plan_builder.build_install_plan(settings)

        outcomes: list[StepOutcome] = []
        for step in plan.walk():
            outcomes.append(step.apply(self._ctx))
        return InstallReport(outcomes)
