import os

from devtunnel.application import catalog
from devtunnel.application.ports.process_runner import CompletedProcess
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.application.steps import (
    AddToPathStep,
    EnsurePackageStep,
    EnsureServiceStep,
    GenerateTailcatKeyStep,
    InstallTailcatBinaryStep,
    SetGitConfigStep,
)
from devtunnel.domain.journal import RecordStatus
from devtunnel.domain.models import Scope
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
    applied = step.apply(ctx)

    revert = step.revert(ctx, applied.record)

    assert revert.status is StepStatus.REVERTED
    assert catalog.GIT.key not in ctx.toolkit.manager.installed


def test_ensure_package_step_revert_leaves_preexisting_packages_untouched():
    ctx = make_context()
    ctx.toolkit.manager.installed.add(catalog.GIT.key)
    step = EnsurePackageStep("git", "Install git", catalog.GIT)
    skipped = step.apply(ctx)

    revert = step.revert(ctx, skipped.record)

    assert revert.status is StepStatus.SKIPPED
    assert catalog.GIT.key in ctx.toolkit.manager.installed


# -- the scope split (D6) --------------------------------------------------


def test_a_machine_scope_step_writes_to_the_machine_journal():
    ctx = make_context()

    EnsurePackageStep("git", "Install git", catalog.GIT).apply(ctx)

    assert ctx.journals[Scope.MACHINE].load()
    assert ctx.journals[Scope.USER].load() == []


def test_a_user_scope_step_writes_to_the_user_journal():
    ctx = make_context()

    GenerateTailcatKeyStep("key", "Generate key", "default").apply(ctx)

    assert ctx.journals[Scope.USER].load()
    assert ctx.journals[Scope.MACHINE].load() == []


def test_every_record_is_stamped_with_the_scope_that_wrote_it():
    ctx = make_context()

    outcome = GenerateTailcatKeyStep("key", "Generate key", "default").apply(ctx)

    assert outcome.record.details["scope"] == "user"


# -- the tailcat binary ----------------------------------------------------


def test_install_binary_step_records_the_path_it_installed():
    ctx = make_context(process=_runner_without("tailcat"))
    step = InstallTailcatBinaryStep("bin", "Install tailcat", ctx.paths.binary_path)

    outcome = step.apply(ctx)

    assert outcome.status is StepStatus.APPLIED
    assert outcome.record.details["path"] == ctx.paths.binary_path


def test_install_binary_step_revert_removes_the_recorded_path():
    ctx = make_context(process=_runner_without("tailcat"))
    step = InstallTailcatBinaryStep("bin", "Install tailcat", ctx.paths.binary_path)
    outcome = step.apply(ctx)

    step.revert(ctx, outcome.record)

    assert not ctx.binary_installer.is_installed(ctx.paths.binary_path)


def test_a_tailcat_already_on_path_is_adopted_and_never_removed():
    ctx = make_context()  # the default fake runner finds every executable
    step = InstallTailcatBinaryStep("bin", "Install tailcat", ctx.paths.binary_path)

    outcome = step.apply(ctx)

    assert outcome.status is StepStatus.SKIPPED
    assert not outcome.record.needs_revert


# -- tailcat keys ----------------------------------------------------------


def test_generate_key_step_caches_the_public_address_in_the_journal():
    ctx = make_context()

    outcome = GenerateTailcatKeyStep("key", "Generate key", "default").apply(ctx)

    assert outcome.record.details["address"].startswith("tc")
    assert outcome.record.details["key_name"] == "default"


def test_generate_key_step_revert_deletes_the_key_it_created():
    ctx = make_context()
    step = GenerateTailcatKeyStep("key", "Generate key", "default")
    outcome = step.apply(ctx)
    assert ctx.tunnel_provider.has_key("default")

    step.revert(ctx, outcome.record)

    assert not ctx.tunnel_provider.has_key("default")


def test_generate_key_step_never_deletes_a_key_that_already_existed():
    ctx = make_context()
    ctx.tunnel_provider.generate_key("default")  # the user made it themselves
    step = GenerateTailcatKeyStep("key", "Generate key", "default")
    outcome = step.apply(ctx)

    assert outcome.status is StepStatus.SKIPPED
    step.revert(ctx, outcome.record)
    assert ctx.tunnel_provider.has_key("default")


# -- git -------------------------------------------------------------------


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


# -- services --------------------------------------------------------------


def test_ensure_service_step_uses_the_manager_matching_the_specs_scope():
    ctx = make_context()
    step = EnsureServiceStep("tunnel", "Register tunnel", catalog.TUNNEL_SERVICE)

    step.apply(ctx)

    assert ctx.toolkit.user_service.get_state(catalog.TUNNEL_SERVICE).running
    assert not ctx.toolkit.system_service.get_state(catalog.TUNNEL_SERVICE).exists


def test_system_scope_service_uses_the_system_manager():
    ctx = make_context()
    step = EnsureServiceStep("tunnel", "Register tunnel", catalog.TUNNEL_SERVICE_SYSTEM)

    step.apply(ctx)

    assert ctx.toolkit.system_service.get_state(catalog.TUNNEL_SERVICE_SYSTEM).running


def test_ensure_service_step_restores_the_exact_prior_state_on_revert():
    ctx = make_context()
    step = EnsureServiceStep("tunnel", "Register tunnel", catalog.TUNNEL_SERVICE)

    outcome = step.apply(ctx)
    assert outcome.record.prior_state == ServiceState(
        exists=False, running=False, startup_automatic=False
    ).to_dict()

    step.revert(ctx, outcome.record)
    state = ctx.toolkit.user_service.get_state(catalog.TUNNEL_SERVICE)
    assert state.running is False
    assert state.startup_automatic is False


# -- PATH ------------------------------------------------------------------


def test_add_to_path_step_appends_to_profile_and_restores_it_on_revert():
    ctx = make_context()
    profile = os.path.join(ctx.paths.home, ".profile")
    ctx.filesystem.write_text(profile, "# existing\n")
    step = AddToPathStep("path", "Add to PATH", "/opt/bin")

    outcome = step.apply(ctx)
    assert "/opt/bin" in ctx.filesystem.read_text(profile)

    step.revert(ctx, outcome.record)
    assert ctx.filesystem.read_text(profile) == "# existing\n"


# -- dry run ---------------------------------------------------------------


def test_dry_run_never_mutates_and_never_journals():
    ctx = make_context(dry_run=True)
    step = EnsurePackageStep("git", "Install git", catalog.GIT)

    outcome = step.apply(ctx)

    assert outcome.status is StepStatus.DRY_RUN
    assert catalog.GIT.key not in ctx.toolkit.manager.installed
    assert ctx.journal.load() == []


def _runner_without(*executables):
    from tests.fakes.fake_ports import FakeProcessRunner

    return FakeProcessRunner(missing=executables)
