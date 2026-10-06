"""The guarantee the whole architecture exists to provide: install, then
uninstall, returns the (fake) machine to exactly its starting state -- and
anything that was already present before install is never touched.

Plan section 14 calls this the single most valuable test in the suite and
requires it to hold for **all four plan shapes**: server, client, ``--with-git``
and ``--system``.
"""

import os

import pytest

from devtunnel.application.allowlist import Allowlist
from devtunnel.application.plan_builder import InstallSettings, PlanBuilder
from devtunnel.application.use_cases.install_environment import InstallEnvironmentUseCase
from devtunnel.application.use_cases.uninstall_environment import UninstallEnvironmentUseCase
from devtunnel.domain.errors import ElevationRequiredError
from devtunnel.domain.models import GitIdentity, Role, Scope
from tests.fakes.context_builder import make_context
from tests.fakes.fake_ports import FakeProcessRunner
from tests.unit.application.conftest import make_bundle

SHAPES = {
    "server": InstallSettings(authorized_keys=("ssh-ed25519 AAAAtest user@host",)),
    "client": InstallSettings(role=Role.CLIENT),
    "with-git": InstallSettings(with_git=True, git_identity=GitIdentity(name="Ada")),
    "system": InstallSettings(system_scope=True),
}


def _context(**overrides):
    # No tailcat on PATH: otherwise the binary step adopts a pre-existing one
    # and there is nothing to round-trip.
    overrides.setdefault("process", FakeProcessRunner(missing=("tailcat",)))
    return make_context(**overrides)


def _install(ctx, settings):
    builder = PlanBuilder(ctx.toolkit, ctx.paths, ctx.process)
    return InstallEnvironmentUseCase(ctx, builder).execute(settings)


@pytest.mark.parametrize("shape", list(SHAPES))
def test_install_then_uninstall_returns_to_a_clean_slate(shape):
    settings = SHAPES[shape]
    ctx = _context()

    assert not _install(ctx, settings).failed
    assert ctx.journal.load()  # something was actually recorded

    report = UninstallEnvironmentUseCase(ctx).execute()

    assert report.fully_reverted
    assert ctx.toolkit.manager.installed == set()
    assert ctx.tunnel_provider.keys == {}
    assert not ctx.binary_installer.installed
    assert ctx.journal.load() == []  # cleared once every record is reverted


def test_the_default_server_install_never_writes_to_the_machine_journal():
    ctx = _context()

    _install(ctx, SHAPES["server"])

    assert ctx.journals[Scope.USER].load()
    assert ctx.journals[Scope.MACHINE].load() == []


def test_with_git_writes_git_to_the_machine_journal_and_tailcat_to_the_user_one():
    ctx = _context()

    _install(ctx, SHAPES["with-git"])

    machine_targets = {r.target for r in ctx.journals[Scope.MACHINE].load()}
    user_targets = {r.target for r in ctx.journals[Scope.USER].load()}
    assert "package:git" in machine_targets
    assert "binary:tailcat" in user_targets


def test_an_unprivileged_default_install_is_allowed():
    ctx = _context(toolkit=_unelevated_toolkit())

    report = _install(ctx, SHAPES["server"])

    assert not report.failed


def test_an_unprivileged_with_git_install_is_refused():
    ctx = _context(toolkit=_unelevated_toolkit())

    with pytest.raises(ElevationRequiredError):
        _install(ctx, SHAPES["with-git"])


def test_an_unprivileged_uninstall_of_a_user_scope_install_is_allowed():
    ctx = _context()
    _install(ctx, SHAPES["server"])
    ctx.toolkit.elevated = False

    report = UninstallEnvironmentUseCase(ctx).execute()

    assert report.fully_reverted


def test_an_unprivileged_uninstall_is_refused_when_machine_records_exist():
    ctx = _context()
    _install(ctx, SHAPES["with-git"])
    ctx.toolkit.elevated = False

    with pytest.raises(ElevationRequiredError):
        UninstallEnvironmentUseCase(ctx).execute()


def test_uninstall_never_touches_packages_that_predate_devtunnel():
    ctx = _context()
    ctx.toolkit.manager.installed.add("git")  # git was already on the machine

    _install(ctx, SHAPES["with-git"])
    UninstallEnvironmentUseCase(ctx).execute()

    assert ctx.toolkit.manager.installed == {"git"}


def test_uninstall_never_deletes_an_adopted_ssh_identity():
    ctx = _context()
    identity = os.path.join(ctx.paths.ssh_dir, "id_ed25519")
    ctx.filesystem.write_text(identity, "PRE-EXISTING PRIVATE KEY")

    _install(ctx, SHAPES["client"])
    UninstallEnvironmentUseCase(ctx).execute()

    assert ctx.filesystem.read_text(identity) == "PRE-EXISTING PRIVATE KEY"


def test_uninstall_revokes_both_halves_of_every_peer():
    ctx = _context()
    bundle = make_bundle()
    settings = InstallSettings(
        authorized_keys=("ssh-ed25519 AAAAtest user@host",), peers=(bundle.encode(),)
    )

    _install(ctx, settings)
    allowlist = Allowlist(ctx.filesystem, ctx.paths.allowlist_path)
    assert allowlist.contains(bundle.nodekey)

    report = UninstallEnvironmentUseCase(ctx).execute()

    assert report.fully_reverted
    assert not allowlist.contains(bundle.nodekey)
    assert not ctx.filesystem.exists(ctx.paths.authorized_keys_path)


def test_peers_are_revoked_before_the_machinery_that_enforces_them_is_removed():
    # Reverse-chronological replay gives this for free, and it matters: if an
    # uninstall is interrupted, access should already be gone.
    ctx = _context()
    settings = InstallSettings(
        authorized_keys=("ssh-ed25519 AAAAtest user@host",), peers=(make_bundle().encode(),)
    )
    _install(ctx, settings)

    reverted_targets = []
    for outcome in UninstallEnvironmentUseCase(ctx).execute().outcomes:
        if outcome.record is not None:
            reverted_targets.append(outcome.record.target)

    assert reverted_targets.index("peer:tester@laptop") < reverted_targets.index("binary:tailcat")


def test_git_identity_is_restored_to_its_prior_value_not_merely_unset():
    from devtunnel.application.ports.process_runner import CompletedProcess

    ctx = _context()
    ctx.process.responses[("git", "config", "--global", "--get", "user.name")] = CompletedProcess(
        (), 0, "Prior Person\n", ""
    )

    _install(ctx, SHAPES["with-git"])
    UninstallEnvironmentUseCase(ctx).execute()

    assert ["git", "config", "--global", "user.name", "Prior Person"] in ctx.process.calls


def test_keep_flag_leaves_the_named_package_installed():
    ctx = _context()
    _install(ctx, SHAPES["with-git"])

    report = UninstallEnvironmentUseCase(ctx).execute(keep=frozenset({"git"}))

    assert "git" in ctx.toolkit.manager.installed
    assert report.fully_reverted  # keeping a package on request is not a failure

    git_records = [r for r in ctx.journal.load() if r.target == "package:git"]
    assert len(git_records) == 1
    assert git_records[0].needs_revert


def test_repeated_uninstall_is_a_safe_no_op():
    ctx = _context()
    _install(ctx, SHAPES["server"])
    UninstallEnvironmentUseCase(ctx).execute()

    second = UninstallEnvironmentUseCase(ctx).execute()

    assert second.outcomes == []
    assert second.fully_reverted


def _unelevated_toolkit():
    from tests.fakes.fake_ports import FakeToolkit

    return FakeToolkit(elevated=False)
