"""Factory Method: reconstruct the one Step that can revert a journal record.

Uninstall does not replay a freshly built ``Plan`` -- it walks the journal
backwards, so every :class:`~devtunnel.domain.journal.ChangeRecord` must be
able to reconstruct, from its own ``kind``/``target``/``details`` alone, the
:class:`~devtunnel.domain.plan.Step` that understands how to undo it. This is
the piece that makes the Command + Memento pair (see ``steps.py``) work in
reverse without keeping the original install ``Plan`` object alive.

Dispatch is by ``(kind, target prefix)`` rather than by kind alone. That is
what lets ``FILE_MODIFIED`` carry both the ``authorized_keys`` record and every
per-peer record without ambiguity -- two very different reverts behind one
kind, told apart by whether the target reads ``file:`` or ``peer:``.

Constructor arguments unused by ``undo()`` (e.g. the desired value a
``SetGitConfigStep`` would *set*) are passed as harmless placeholders --
``undo`` only ever reads ``record.prior_state`` and ``record.details``.
"""

from __future__ import annotations

from devtunnel.application import catalog
from devtunnel.application.pairing import PairingBundle
from devtunnel.application.steps import (
    AddToPathStep,
    BootstrapPackageManagerStep,
    EnsureAllowlistEntryStep,
    EnsureAuthorizedKeysStep,
    EnsurePackageStep,
    EnsurePeerStep,
    EnsureServiceStep,
    EnsureSshKeypairStep,
    GenerateTailcatKeyStep,
    InstallTailcatBinaryStep,
    SetGitConfigStep,
)
from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.domain.models import Scope
from devtunnel.domain.plan import Step


class NoReverterRegisteredError(LookupError):
    def __init__(self, record: ChangeRecord) -> None:
        self.record = record
        super().__init__(
            f"no reverter registered for change kind {record.kind!r} "
            f"(target={record.target!r})"
        )


class StepReverter:
    """Every reverter here is platform-agnostic by construction."""

    def for_record(self, record: ChangeRecord) -> Step:
        step = self._live_step(record)
        if step is None:
            raise NoReverterRegisteredError(record)
        return step

    # -- the live catalog of reverters -----------------------------------

    def _live_step(self, record: ChangeRecord) -> Step | None:
        step_id = f"revert:{record.id}"
        scope = self._scope_of(record)

        if record.kind is ChangeKind.PACKAGE_INSTALLED:
            key = record.target.removeprefix("package:")
            package = catalog.PACKAGES_BY_KEY.get(key)
            if package is None:
                return None  # not in the catalog: NoReverterRegisteredError
            return EnsurePackageStep(
                step_id, f"Uninstall {package.display_name}", package, scope=scope
            )

        if record.kind is ChangeKind.BINARY_INSTALLED:
            return InstallTailcatBinaryStep(
                step_id,
                "Remove tailcat",
                record.details.get("path", ""),
                scope=scope,
            )

        if record.kind is ChangeKind.KEY_GENERATED:
            name = record.target.removeprefix("tailcatkey:")
            return GenerateTailcatKeyStep(step_id, f"Remove tailcat key {name!r}", name)

        if record.kind is ChangeKind.SERVICE_STATE_CHANGED:
            key = record.target.removeprefix("service:")
            service = catalog.SERVICES_BY_KEY.get(key)
            if service is None:
                return None
            if scope is Scope.MACHINE:
                service = catalog.TUNNEL_SERVICE_SYSTEM
            return EnsureServiceStep(step_id, f"Restore {service.display_name} state", service)

        if record.kind is ChangeKind.PKGMGR_BOOTSTRAPPED:
            return BootstrapPackageManagerStep(step_id, "Remove Chocolatey", record.target)

        if record.kind is ChangeKind.FILE_CREATED and record.target == "file:ssh_identity":
            return EnsureSshKeypairStep(step_id, "Remove the generated SSH identity")

        if record.kind is ChangeKind.FILE_MODIFIED:
            if record.target == "file:authorized_keys":
                return EnsureAuthorizedKeysStep(step_id, "Restore authorized_keys", ())
            if record.target.startswith("peer:"):
                return EnsurePeerStep(step_id, "Revoke peer", self._bundle_from(record))
            if record.target.startswith("allow:"):
                return EnsureAllowlistEntryStep(
                    step_id, "Remove the allowlist entry", record.target.removeprefix("allow:")
                )

        if record.kind is ChangeKind.CONFIG_KEY_SET:
            if record.target.startswith("gitconfig:"):
                git_key = record.target.removeprefix("gitconfig:")
                return SetGitConfigStep(step_id, f"Restore git {git_key}", git_key, "")
            if record.target.startswith("path:"):
                return AddToPathStep(step_id, "Restore PATH", record.details.get("directory", ""))

        return None

    @staticmethod
    def _scope_of(record: ChangeRecord) -> Scope:
        """The scope the record was written with.

        Stamped into ``details`` by ``JournaledStep`` at apply time, so the
        reverter writes its outcome back to the same journal the record came
        out of rather than guessing from the step class's default.
        """

        try:
            return Scope(record.details.get("scope", Scope.USER.value))
        except ValueError:
            return Scope.USER

    @staticmethod
    def _bundle_from(record: ChangeRecord) -> PairingBundle:
        """Rebuild the peer from what the record itself holds.

        Only public material was ever journaled, which is exactly enough to
        revoke the peer -- the node key to drop from the allowlist and the SSH
        key to drop from ``authorized_keys``.
        """

        return PairingBundle(
            name=record.details.get("name", record.target.removeprefix("peer:")),
            nodekey=record.details.get("nodekey", ""),
            sshkey=record.details.get("sshkey", ""),
            created=record.details.get("created", ""),
        )
