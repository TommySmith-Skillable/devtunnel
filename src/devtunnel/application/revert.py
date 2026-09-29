"""Factory Method: reconstruct the one Step that can revert a journal record.

Uninstall does not replay a freshly built ``Plan`` -- it walks the journal
backwards, so every :class:`~devtunnel.domain.journal.ChangeRecord` must be
able to reconstruct, from its own ``kind``/``target``/``details`` alone, the
:class:`~devtunnel.domain.plan.Step` that understands how to undo it. The
catalog (:mod:`devtunnel.application.catalog`) is what lets a bare string like
``"package:git"`` resolve back into a full ``PackageSpec``. This is the
piece that makes the Command + Memento pair (see ``steps.py``) work in
reverse without keeping the original install ``Plan`` object alive.

Constructor arguments unused by ``undo()`` (e.g. the desired value a
``SetGitConfigStep`` would *set*) are passed as harmless placeholders --
``undo`` only ever reads ``record.prior_state``.
"""

from __future__ import annotations

from devtunnel.application import catalog
from devtunnel.application.steps import (
    BootstrapPackageManagerStep,
    ConfigureNgrokAuthtokenStep,
    EnsurePackageStep,
    EnsureRepositoryStep,
    EnsureServiceStep,
    SetGitConfigStep,
)
from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.domain.plan import Step


class NoReverterRegisteredError(LookupError):
    def __init__(self, record: ChangeRecord) -> None:
        self.record = record
        super().__init__(
            f"no reverter registered for change kind {record.kind!r} "
            f"(target={record.target!r})"
        )


class StepReverter:
    def for_record(self, record: ChangeRecord) -> Step:
        step_id = f"revert:{record.id}"

        if record.kind is ChangeKind.PACKAGE_INSTALLED:
            key = record.target.removeprefix("package:")
            package = catalog.PACKAGES_BY_KEY[key]
            return EnsurePackageStep(step_id, f"Uninstall {package.display_name}", package)

        if record.kind is ChangeKind.SERVICE_STATE_CHANGED:
            key = record.target.removeprefix("service:")
            service = catalog.SERVICES_BY_KEY[key]
            return EnsureServiceStep(step_id, f"Restore {service.display_name} state", service)

        if record.kind is ChangeKind.APT_REPO_ADDED:
            name = record.target.removeprefix("repository:")
            return EnsureRepositoryStep(step_id, f"Remove {name} apt repository", name)

        if record.kind is ChangeKind.PKGMGR_BOOTSTRAPPED:
            return BootstrapPackageManagerStep(step_id, "Remove Chocolatey", record.target)

        if record.kind is ChangeKind.CONFIG_KEY_SET:
            if record.target.startswith("gitconfig:"):
                git_key = record.target.removeprefix("gitconfig:")
                return SetGitConfigStep(step_id, f"Restore git {git_key}", git_key, "")
            if record.target == "ngrok:authtoken":
                return ConfigureNgrokAuthtokenStep(step_id, "Remove ngrok authtoken", "")

        raise NoReverterRegisteredError(record)
