"""The guarantee the whole architecture exists to provide: install, then
uninstall, returns the (fake) machine to exactly its starting state -- and
anything that was already present before install is never touched.
"""

from devtunnel.application import catalog
from devtunnel.application.plan_builder import InstallSettings, PlanBuilder
from devtunnel.application.use_cases.install_environment import InstallEnvironmentUseCase
from devtunnel.application.use_cases.uninstall_environment import UninstallEnvironmentUseCase
from devtunnel.domain.models import GitIdentity
from tests.fakes.context_builder import make_context


def _run_install(ctx, **settings_overrides):
    settings = InstallSettings(
        git_identity=settings_overrides.pop("git_identity", GitIdentity()),
        ngrok_authtoken=settings_overrides.pop("ngrok_authtoken", "tok_123"),
        **settings_overrides,
    )
    return InstallEnvironmentUseCase(ctx, PlanBuilder(ctx.toolkit)).execute(settings)


def test_full_install_then_uninstall_returns_to_a_clean_slate():
    ctx = make_context()

    install_report = _run_install(ctx)
    assert not install_report.failed
    expected = {"git", "ngrok", "openssh-client", "openssh-server", "gpg"}
    assert ctx.toolkit.manager.installed == expected
    assert ctx.toolkit.service_manager.get_state(catalog.SSHD).running
    assert ctx.toolkit.repository_provider.registered == {"ngrok"}
    assert ctx.tunnel_provider.configured

    uninstall_report = UninstallEnvironmentUseCase(ctx).execute()

    assert uninstall_report.fully_reverted
    assert ctx.toolkit.manager.installed == set()
    assert not ctx.toolkit.service_manager.get_state(catalog.SSHD).exists
    assert ctx.toolkit.repository_provider.registered == set()
    assert not ctx.tunnel_provider.configured
    assert ctx.journal.load() == []  # journal is cleared once every record is reverted


def test_uninstall_never_touches_packages_that_predate_devtunnel():
    ctx = make_context()
    ctx.toolkit.manager.installed.add("git")  # git was already on the machine

    _run_install(ctx)
    assert "git" in ctx.toolkit.manager.installed

    UninstallEnvironmentUseCase(ctx).execute()

    assert "git" in ctx.toolkit.manager.installed  # still there: never devtunnel's to remove
    assert ctx.toolkit.manager.installed == {"git"}  # everything devtunnel added is gone


def test_uninstall_restores_a_service_that_was_already_running_before_install():
    ctx = make_context()
    ctx.toolkit.service_manager.ensure_running_and_enabled(catalog.SSHD)  # pre-existing state

    _run_install(ctx)
    uninstall_report = UninstallEnvironmentUseCase(ctx).execute()

    assert uninstall_report.fully_reverted
    state = ctx.toolkit.service_manager.get_state(catalog.SSHD)
    assert state.running and state.startup_automatic  # restored, not stopped


def test_git_identity_is_restored_to_its_prior_value_not_merely_unset():
    from devtunnel.application.ports.process_runner import CompletedProcess

    ctx = make_context()
    ctx.process.responses[("git", "config", "--global", "--get", "user.name")] = CompletedProcess(
        (), 0, "Prior Person\n", ""
    )

    _run_install(ctx, git_identity=GitIdentity(name="New Person"))
    UninstallEnvironmentUseCase(ctx).execute()

    assert ["git", "config", "--global", "user.name", "Prior Person"] in ctx.process.calls


def test_keep_flag_leaves_the_named_package_installed():
    ctx = make_context()
    _run_install(ctx)

    report = UninstallEnvironmentUseCase(ctx).execute(keep=frozenset({"git"}))

    assert "git" in ctx.toolkit.manager.installed
    assert "ngrok" not in ctx.toolkit.manager.installed
    assert report.fully_reverted  # keeping a package on request is not a failure

    # The git record is deliberately left un-reverted (not dropped), so a
    # later plain `uninstall` (without --keep) can still remove it.
    git_records = [r for r in ctx.journal.load() if r.target == "package:git"]
    assert len(git_records) == 1
    assert git_records[0].needs_revert


def test_repeated_uninstall_is_a_safe_no_op():
    ctx = make_context()
    _run_install(ctx)
    UninstallEnvironmentUseCase(ctx).execute()

    second_report = UninstallEnvironmentUseCase(ctx).execute()

    assert second_report.outcomes == []
    assert second_report.fully_reverted
