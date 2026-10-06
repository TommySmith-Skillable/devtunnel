"""Reverters for journals written by the ngrok-era devtunnel.

This module exists for one reason: a journal written by the previous version
may contain ``apt_repo_added`` and ``ngrok:authtoken`` records, and loading one
with no reverter registered would raise ``NoReverterRegisteredError`` *in the
middle of* an uninstall -- after some records had already been reverted and
before the rest were. The user would be left with a half-removed install and an
apt repository they cannot get rid of with the tool that added it.

Roughly sixty lines of retained code is cheaper than stranding anyone in that
state, so the ngrok apt-repository and authtoken reverters are kept here, in
one clearly-labelled file, rather than left scattered through ``steps.py``.

Nothing in the live plan can produce these records. ``devtunnel doctor``
detects a legacy journal and says so. **Delete this module, the two deprecated
``ChangeKind`` members it reads, and its wiring in ``StepReverter`` in the
release after next** -- see section 16 of docs/tailcat-migration-plan.md.
"""

from __future__ import annotations

import os

from devtunnel.application.context import ExecutionContext
from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.domain.models import PackageSpec, PlatformId, Scope, ServiceSpec
from devtunnel.domain.plan import Step, StepOutcome, StepStatus

LEGACY_KINDS = frozenset({ChangeKind.APT_REPO_ADDED, ChangeKind.WINDOWS_CAPABILITY_ADDED})
LEGACY_TARGETS = frozenset({"ngrok:authtoken"})


def is_legacy(record: ChangeRecord) -> bool:
    """Whether this record came from an ngrok-era install."""

    return (
        record.kind in LEGACY_KINDS
        or record.target in LEGACY_TARGETS
        or record.target.startswith("package:ngrok")
        or record.target.startswith("package:openssh-server")
        or record.target.startswith("package:gpg")
        or record.target == "service:sshd"
    )


class _LegacyStep(Step):
    """Base for reverters that only ever run backwards.

    These steps are never planned, only reconstructed from a record, so
    ``apply`` is unreachable by construction and says so rather than
    pretending to be installable.
    """

    scope = Scope.MACHINE  # everything the ngrok era wrote needed elevation

    def apply(self, ctx: ExecutionContext) -> StepOutcome:
        raise NotImplementedError("legacy steps are revert-only")

    def _finish(self, ctx: ExecutionContext, record: ChangeRecord) -> StepOutcome:
        reverted = record.reverted()
        ctx.journal_for(self.scope).update(reverted)
        return StepOutcome(StepStatus.REVERTED, "", reverted)


class RemoveNgrokAptRepositoryStep(_LegacyStep):
    """Removes the apt keyring and sources list the ngrok era registered."""

    def revert(self, ctx: ExecutionContext, record: ChangeRecord) -> StepOutcome:
        if not record.needs_revert:
            return StepOutcome(StepStatus.SKIPPED, "nothing to revert")
        if ctx.dry_run:
            return StepOutcome(StepStatus.DRY_RUN, f"would revert {record.target}")

        details = record.details
        for key in ("sources_list_path", "keyring_path"):
            path = details.get(key)
            if path:
                ctx.filesystem.remove_file(path)
        if details.get("created_dir") and details.get("dir_path"):
            ctx.filesystem.remove_dir_if_empty(details["dir_path"])
        ctx.process.run(["apt-get", "update"], check=False)
        return self._finish(ctx, record)


class RemoveNgrokAuthtokenStep(_LegacyStep):
    """Restores (or deletes) the ngrok config file the authtoken was written to."""

    def revert(self, ctx: ExecutionContext, record: ChangeRecord) -> StepOutcome:
        if not record.needs_revert:
            return StepOutcome(StepStatus.SKIPPED, "nothing to revert")
        if ctx.dry_run:
            return StepOutcome(StepStatus.DRY_RUN, f"would revert {record.target}")

        prior = record.prior_state
        path = prior.get("config_path")
        if path:
            if prior.get("existed"):
                ctx.filesystem.restore_from_backup(path, prior.get("backup_path"))
            else:
                ctx.filesystem.remove_file(path)
                ctx.filesystem.remove_dir_if_empty(os.path.dirname(path))
        return self._finish(ctx, record)


class RemoveWindowsCapabilityStep(_LegacyStep):
    """Removes a Windows optional feature an ngrok-era install added.

    Only ever OpenSSH Server in practice -- the client is still in the live
    catalog and is reverted by the ordinary package step.
    """

    def __init__(self, step_id: str, description: str, capability_id: str) -> None:
        super().__init__(step_id, description)
        self.capability_id = capability_id

    def revert(self, ctx: ExecutionContext, record: ChangeRecord) -> StepOutcome:
        if not record.needs_revert:
            return StepOutcome(StepStatus.SKIPPED, "nothing to revert")
        if ctx.dry_run:
            return StepOutcome(StepStatus.DRY_RUN, f"would revert {record.target}")

        ctx.process.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"Remove-WindowsCapability -Online -Name '{self.capability_id}'",
            ],
            check=False,
        )
        return self._finish(ctx, record)


# --------------------------------------------------------------------------
# Catalog entries the live catalog no longer carries
# --------------------------------------------------------------------------
# An ngrok-era journal names packages and a service that D1/D2/D4 deleted from
# ``catalog.py``. They are kept here, not there, so the live catalog stays an
# honest description of what devtunnel installs today while an old journal can
# still be read back into a revertable step.

LEGACY_PACKAGES: dict[str, PackageSpec] = {
    "ngrok": PackageSpec(key="ngrok", display_name="ngrok", choco_id="ngrok", apt_id="ngrok"),
    "gpg": PackageSpec(key="gpg", display_name="GNU Privacy Guard", apt_id="gpg"),
    "openssh-server": PackageSpec(
        key="openssh-server",
        display_name="OpenSSH Server",
        windows_capability_id="OpenSSH.Server~~~~0.0.1.0",
        apt_id="openssh-server",
    ),
}

LEGACY_SERVICES: dict[str, ServiceSpec] = {
    "sshd": ServiceSpec(
        key="sshd",
        display_name="OpenSSH Server (sshd)",
        scope=Scope.MACHINE,
        windows_service_name="sshd",
        systemd_unit="ssh",
    ),
}


def legacy_step_for(record: ChangeRecord, platform: PlatformId) -> Step | None:
    """The reverter for an ngrok-era record, or ``None`` if it is not one.

    ``StepReverter`` consults this only after the live reverters have declined
    the record, so nothing here can shadow current behaviour.
    """

    from devtunnel.application.steps import EnsurePackageStep, EnsureServiceStep

    step_id = f"revert-legacy:{record.id}"

    if record.kind is ChangeKind.APT_REPO_ADDED:
        name = record.target.removeprefix("repository:")
        return RemoveNgrokAptRepositoryStep(step_id, f"Remove the {name} apt repository")

    if record.kind is ChangeKind.CONFIG_KEY_SET and record.target == "ngrok:authtoken":
        return RemoveNgrokAuthtokenStep(step_id, "Remove the ngrok authtoken")

    if record.kind is ChangeKind.WINDOWS_CAPABILITY_ADDED:
        cap_id = record.details.get("capability_id", "OpenSSH.Server~~~~0.0.1.0")
        return RemoveWindowsCapabilityStep(step_id, "Remove a Windows capability", cap_id)

    if record.kind is ChangeKind.PACKAGE_INSTALLED:
        key = record.target.removeprefix("package:")
        package = LEGACY_PACKAGES.get(key)
        if package is None:
            return None
        # OpenSSH Server is a Windows *capability*, and the live toolkit no
        # longer routes it to the capability manager -- the plan deliberately
        # shrank that mapping to the client alone. Reach for the capability
        # path explicitly here rather than letting Chocolatey be handed a
        # package id it has never heard of.
        if key == "openssh-server" and platform is PlatformId.WINDOWS:
            return RemoveWindowsCapabilityStep(
                step_id,
                "Remove the OpenSSH Server capability",
                package.windows_capability_id or "OpenSSH.Server~~~~0.0.1.0",
            )
        return EnsurePackageStep(
            step_id, f"Uninstall {package.display_name}", package, scope=Scope.MACHINE
        )

    if record.kind is ChangeKind.SERVICE_STATE_CHANGED:
        key = record.target.removeprefix("service:")
        service = LEGACY_SERVICES.get(key)
        if service is not None:
            return EnsureServiceStep(step_id, f"Restore {service.display_name} state", service)

    return None
