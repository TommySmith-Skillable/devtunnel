"""Use case: provision tailcat, keys and SSH access per the resolved settings."""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application.context import ExecutionContext
from devtunnel.application.plan_builder import (
    InstallSettings,
    PlanBuilder,
    plan_requires_elevation,
)
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
        self._ctx.phase = Phase.INSTALL
        plan = self._plan_builder.build_install_plan(settings)

        # Plan-derived, not unconditional. The old check demanded Administrator
        # for every install because every install touched system state; after
        # D1/D2/D5 the default plan touches none, and demanding privileges it
        # will never use would be the single thing keeping an unprivileged
        # install out of reach. Build first, then ask the plan.
        if not self._ctx.dry_run and plan_requires_elevation(plan):
            if not self._ctx.toolkit.is_elevated():
                raise ElevationRequiredError(self._ctx.toolkit.elevation_hint())

        outcomes: list[StepOutcome] = []
        for step in plan.walk():
            outcomes.append(step.apply(self._ctx))
        return InstallReport(outcomes)
