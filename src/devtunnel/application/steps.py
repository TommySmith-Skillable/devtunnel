"""Concrete, reusable :class:`~devtunnel.domain.plan.Step` implementations.

:class:`JournaledStep` is the Template Method that fixes the
``detect -> skip-or-apply -> journal`` skeleton: a concrete step supplies only
``already_satisfied``, ``perform`` and ``undo``; the base class guarantees
every mutation is written to the journal as ``PENDING`` *before* it happens
and flipped to ``APPLIED``/``SKIPPED_PREEXISTING`` afterwards. No subclass can
forget to journal, because the journal calls live in the base class, not in
the override points.

The rest of the module is a small library of generic steps (packages,
services, files, directories, shell commands, git config, the ngrok
repository and authtoken) that :class:`~devtunnel.application.plan_builder.PlanBuilder`
composes per platform. Reuse is deliberate: the same ``EnsurePackageStep``
installs git and ngrok via Chocolatey/apt *and* OpenSSH via the Windows
capability manager, because all three are just "ensure this
:class:`~devtunnel.application.ports.package_manager.PackageManagerPort` entry
exists" from the step's point of view.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import replace

from devtunnel.application.catalog import (
    NGROK_APT_REPOSITORY_NAME,
)
from devtunnel.application.context import ExecutionContext
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.errors import StepFailedError
from devtunnel.domain.events import StepEvent
from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.domain.models import PackageSpec, ServiceSpec
from devtunnel.domain.plan import Step, StepOutcome, StepStatus


class JournaledStep(Step):
    """Template Method base: every subclass gets journaling for free."""

    kind: ChangeKind

    def __init__(self, step_id: str, description: str, target: str) -> None:
        super().__init__(step_id, description)
        self.target = target

    # -- override points -------------------------------------------------

    @abstractmethod
    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        """True if the desired state already holds and nothing should change."""

    @abstractmethod
    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        """Apply the change. Returns ``(prior_state, details)`` to journal."""

    @abstractmethod
    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        """Reverse the change described by ``record``."""

    # -- template ----------------------------------------------------------

    def apply(self, ctx: ExecutionContext) -> StepOutcome:
        self._notify(ctx, "started")

        if ctx.dry_run:
            dry_run_message = self.describe_dry_run()
            self._notify(ctx, "dry_run", self._detail_or_blank(dry_run_message))
            return StepOutcome(StepStatus.DRY_RUN, dry_run_message)

        if self.already_satisfied(ctx):
            record = ChangeRecord.pending(self.kind, self.target).skipped_preexisting()
            ctx.journal.append(record)
            self._notify(ctx, "skipped", "already present")
            return StepOutcome(StepStatus.SKIPPED, "already present", record)

        record = ChangeRecord.pending(self.kind, self.target)
        ctx.journal.append(record)  # PENDING before the mutation: crash-safe
        try:
            prior_state, details = self.perform(ctx)
        except Exception as exc:  # noqa: BLE001 - deliberately broad; re-raised typed
            self._notify(ctx, "failed", str(exc))
            raise StepFailedError(self.description, str(exc)) from exc

        applied = replace(record, prior_state=prior_state).applied(details=details)
        ctx.journal.update(applied)
        self._notify(ctx, "applied")
        return StepOutcome(StepStatus.APPLIED, "", applied)

    def revert(self, ctx: ExecutionContext, record: ChangeRecord) -> StepOutcome:
        if not record.needs_revert:
            return StepOutcome(StepStatus.SKIPPED, "nothing to revert")

        if ctx.dry_run:
            msg = f"would revert {record.target}"
            self._notify(ctx, "dry_run", self._detail_or_blank(msg))
            return StepOutcome(StepStatus.DRY_RUN, msg)

        self._notify(ctx, "started")
        try:
            self.undo(ctx, record)
        except Exception as exc:  # noqa: BLE001
            failed = record.revert_failed(reason=str(exc))
            ctx.journal.update(failed)
            self._notify(ctx, "revert_failed", str(exc))
            return StepOutcome(StepStatus.REVERT_FAILED, str(exc), failed)

        reverted = record.reverted()
        ctx.journal.update(reverted)
        self._notify(ctx, "reverted")
        return StepOutcome(StepStatus.REVERTED, "", reverted)

    def _notify(self, ctx: ExecutionContext, status: str, detail: str = "") -> None:
        ctx.events.publish(
            StepEvent(ctx.phase, self.id, self.description, status, detail)
        )

    def _detail_or_blank(self, detail: str) -> str:
        """Avoid presenting a detail that just repeats the description."""
        return "" if detail == self.description else detail


# --------------------------------------------------------------------------
# Concrete steps
# --------------------------------------------------------------------------


class BootstrapPackageManagerStep(JournaledStep):
    """Installs the platform's package manager itself (Chocolatey on
    Windows; a no-op on Debian, which ships with apt)."""

    kind = ChangeKind.PKGMGR_BOOTSTRAPPED

    def __init__(self, step_id: str, description: str, target: str) -> None:
        super().__init__(step_id, description, target)

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return ctx.toolkit.package_bootstrap.is_bootstrapped()

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        details = ctx.toolkit.package_bootstrap.bootstrap()
        return {}, details

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        ctx.toolkit.package_bootstrap.teardown(record.details)


class EnsurePackageStep(JournaledStep):
    """Ensures a single named package/capability is present."""

    kind = ChangeKind.PACKAGE_INSTALLED

    def __init__(self, step_id: str, description: str, package: PackageSpec) -> None:
        super().__init__(step_id, description, target=f"package:{package.key}")
        self.package = package

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        manager = ctx.toolkit.manager_for(self.package.key)
        return manager.is_installed(self.package)

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        manager = ctx.toolkit.manager_for(self.package.key)
        manager.install(self.package)
        return {}, {"package_key": self.package.key}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        manager = ctx.toolkit.manager_for(self.package.key)
        manager.uninstall(self.package)


class EnsureRepositoryStep(JournaledStep):
    """Ensures a third-party package repository (currently: ngrok's apt repo)
    is registered. Only ever added to the plan on platforms that need one."""

    kind = ChangeKind.APT_REPO_ADDED

    def __init__(
        self, step_id: str, description: str, name: str = NGROK_APT_REPOSITORY_NAME
    ) -> None:
        super().__init__(step_id, description, target=f"repository:{name}")
        self.name = name

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        provider = ctx.toolkit.repository_provider
        return provider is not None and provider.is_registered(self.name)

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        provider = ctx.toolkit.repository_provider
        assert provider is not None, "no repository_provider on this toolkit"
        details = provider.register(self.name)
        return {}, details

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        provider = ctx.toolkit.repository_provider
        assert provider is not None
        provider.unregister(self.name, record.details)


class EnsureServiceStep(JournaledStep):
    """Starts a service and sets it to launch automatically, capturing
    whatever state it had before so uninstall restores exactly that."""

    kind = ChangeKind.SERVICE_STATE_CHANGED

    def __init__(self, step_id: str, description: str, service: ServiceSpec) -> None:
        super().__init__(step_id, description, target=f"service:{service.key}")
        self.service = service

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        state = ctx.toolkit.service_manager.get_state(self.service)
        return state.exists and state.running and state.startup_automatic

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        prior = ctx.toolkit.service_manager.get_state(self.service)
        ctx.toolkit.service_manager.ensure_running_and_enabled(self.service)
        return prior.to_dict(), {}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        prior = ServiceState.from_dict(record.prior_state)
        ctx.toolkit.service_manager.apply_state(self.service, prior)


class SetGitConfigStep(JournaledStep):
    """Sets one ``git config --global`` key, capturing its prior value (or
    absence) so revert restores exactly that -- never a blind overwrite of
    the user's existing git identity."""

    kind = ChangeKind.CONFIG_KEY_SET

    def __init__(self, step_id: str, description: str, git_key: str, value: str) -> None:
        super().__init__(step_id, description, target=f"gitconfig:{git_key}")
        self.git_key = git_key
        self.value = value

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        current = self._read(ctx)
        return current == self.value

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        prior = self._read(ctx)
        ctx.process.run(["git", "config", "--global", self.git_key, self.value])
        return {"had_value": prior is not None, "value": prior}, {}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        if record.prior_state.get("had_value"):
            prior_value = record.prior_state["value"]
            ctx.process.run(["git", "config", "--global", self.git_key, prior_value])
        else:
            ctx.process.run(
                ["git", "config", "--global", "--unset", self.git_key], check=False
            )

    def _read(self, ctx: ExecutionContext) -> str | None:
        result = ctx.process.run(
            ["git", "config", "--global", "--get", self.git_key], check=False
        )
        value = result.stdout.strip()
        return value if result.ok and value else None


class ConfigureNgrokAuthtokenStep(JournaledStep):
    """Writes the resolved ngrok authtoken to ngrok's own config file."""

    kind = ChangeKind.CONFIG_KEY_SET

    def __init__(self, step_id: str, description: str, token: str) -> None:
        super().__init__(step_id, description, target="ngrok:authtoken")
        self.token = token

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return ctx.tunnel_provider.is_authtoken_configured()

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        prior_state = ctx.tunnel_provider.configure_authtoken(self.token)
        return prior_state, {}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        ctx.tunnel_provider.remove_authtoken(record.prior_state)


class RunCommandStep(Step):
    """A plain, non-journaled command (e.g. ``apt-get update``).

    Not every action is a reversible *change* -- refreshing a package index
    has nothing meaningful to undo. This step deliberately does not extend
    ``JournaledStep``: it produces no :class:`ChangeRecord`, and the base
    ``Step.revert`` no-op is exactly correct for it.
    """

    def __init__(
        self, step_id: str, description: str, argv: list[str], *, check: bool = True
    ) -> None:
        super().__init__(step_id, description)
        self.argv = argv
        self.check = check

    def apply(self, ctx: ExecutionContext) -> StepOutcome:
        ctx.events.publish(StepEvent(ctx.phase, self.id, self.description, "started"))
        if ctx.dry_run:
            msg = f"would run: {' '.join(self.argv)}"
            ctx.events.publish(StepEvent(ctx.phase, self.id, self.description, "dry_run", msg))
            return StepOutcome(StepStatus.DRY_RUN, msg)
        try:
            ctx.process.run(self.argv, check=self.check)
        except Exception as exc:  # noqa: BLE001
            ctx.events.publish(StepEvent(ctx.phase, self.id, self.description, "failed", str(exc)))
            raise StepFailedError(self.description, str(exc)) from exc
        ctx.events.publish(StepEvent(ctx.phase, self.id, self.description, "applied"))
        return StepOutcome(StepStatus.APPLIED)


__all__ = [
    "JournaledStep",
    "BootstrapPackageManagerStep",
    "EnsurePackageStep",
    "EnsureRepositoryStep",
    "EnsureServiceStep",
    "SetGitConfigStep",
    "ConfigureNgrokAuthtokenStep",
    "RunCommandStep",
]
