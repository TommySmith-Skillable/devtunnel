from devtunnel.application import catalog
from devtunnel.application.ports.process_runner import CompletedProcess
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.application.steps import (
    ConfigureNgrokAuthtokenStep,
    EnsurePackageStep,
    EnsureRepositoryStep,
    EnsureServiceStep,
    SetGitConfigStep,
)
from devtunnel.domain.journal import RecordStatus
from devtunnel.domain.plan import StepStatus
from tests.fakes.context_builder import make_context


def test_ensure_package_step_installs_when_absent():
    ctx = make_context()
    step = EnsurePackageStep("git", "Install git", catalog.GIT)

    outcome = step.apply(ctx)

    assert outcome.status is StepStatus.APPLIED
    assert catalog.GIT.key in ctx.toolkit.manager.installed
    assert ctx.journal.load()[0].status is RecordStatus.APPLIED


def test_ensure_package_step_skips_and_journals_when_already_present():
    ctx = make_context()
    ctx.toolkit.manager.installed.add(catalog.GIT.key)

    outcome = EnsurePackageStep("git", "Install git", catalog.GIT).apply(ctx)

    assert outcome.status is StepStatus.SKIPPED
    record = ctx.journal.load()[0]
    assert record.status is RecordStatus.SKIPPED_PREEXISTING
    assert not record.needs_revert


def test_ensure_package_step_revert_uninstalls_only_what_it_applied():
    ctx = make_context()
    step = EnsurePackageStep("git", "Install git", catalog.GIT)
    applied_outcome = step.apply(ctx)

    revert_outcome = step.revert(ctx, applied_outcome.record)

    assert revert_outcome.status is StepStatus.REVERTED
    assert catalog.GIT.key not in ctx.toolkit.manager.installed


def test_ensure_package_step_revert_leaves_preexisting_packages_untouched():
    ctx = make_context()
    ctx.toolkit.manager.installed.add(catalog.GIT.key)
    step = EnsurePackageStep("git", "Install git", catalog.GIT)
    skipped_outcome = step.apply(ctx)

    revert_outcome = step.revert(ctx, skipped_outcome.record)

    assert revert_outcome.status is StepStatus.SKIPPED
    assert catalog.GIT.key in ctx.toolkit.manager.installed  # never touched


def test_set_git_config_step_captures_absence_and_reverts_by_unsetting():
    ctx = make_context()
    step = SetGitConfigStep("git-name", "Set git user.name", "user.name", "Ada")

    outcome = step.apply(ctx)

    assert outcome.record.prior_state == {"had_value": False, "value": None}
    assert ["git", "config", "--global", "user.name", "Ada"] in ctx.process.calls

    step.revert(ctx, outcome.record)
    assert ["git", "config", "--global", "--unset", "user.name"] in ctx.process.calls


def test_set_git_config_step_restores_the_prior_value_it_captured():
    ctx = make_context()
    ctx.process.responses[("git", "config", "--global", "--get", "user.name")] = CompletedProcess(
        (), 0, "Old Name\n", ""
    )
    step = SetGitConfigStep("git-name", "Set git user.name", "user.name", "New Name")

    outcome = step.apply(ctx)
    assert outcome.record.prior_state == {"had_value": True, "value": "Old Name"}

    step.revert(ctx, outcome.record)
    assert ["git", "config", "--global", "user.name", "Old Name"] in ctx.process.calls


def test_ensure_service_step_restores_the_exact_prior_state_on_revert():
    ctx = make_context()
    step = EnsureServiceStep("sshd", "Enable sshd", catalog.SSHD)

    outcome = step.apply(ctx)
    assert outcome.record.prior_state == ServiceState(
        exists=False, running=False, startup_automatic=False
    ).to_dict()
    assert ctx.toolkit.service_manager.get_state(catalog.SSHD).running

    step.revert(ctx, outcome.record)
    state = ctx.toolkit.service_manager.get_state(catalog.SSHD)
    assert state.running is False
    assert state.startup_automatic is False


def test_ensure_repository_step_round_trip():
    ctx = make_context()
    step = EnsureRepositoryStep("ngrok-repo", "Register ngrok repo")

    outcome = step.apply(ctx)
    assert "ngrok" in ctx.toolkit.repository_provider.registered

    step.revert(ctx, outcome.record)
    assert "ngrok" not in ctx.toolkit.repository_provider.registered


def test_configure_ngrok_authtoken_step_round_trip():
    ctx = make_context()
    step = ConfigureNgrokAuthtokenStep("authtoken", "Configure ngrok", "tok_123")

    outcome = step.apply(ctx)
    assert ctx.tunnel_provider.configured is True

    step.revert(ctx, outcome.record)
    assert ctx.tunnel_provider.configured is False


def test_dry_run_never_mutates_and_never_journals():
    ctx = make_context(dry_run=True)
    step = EnsurePackageStep("git", "Install git", catalog.GIT)

    outcome = step.apply(ctx)

    assert outcome.status is StepStatus.DRY_RUN
    assert catalog.GIT.key not in ctx.toolkit.manager.installed
    assert ctx.journal.load() == []
