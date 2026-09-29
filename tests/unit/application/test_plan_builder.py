from devtunnel.application.plan_builder import InstallSettings, PlanBuilder
from devtunnel.domain.models import GitIdentity, PlatformId
from tests.fakes.fake_ports import FakeToolkit


def _ids(plan) -> list[str]:
    return [s.id for s in plan.walk()]


def test_debian_plan_includes_gpg_and_apt_repository_steps():
    toolkit = FakeToolkit(platform=PlatformId.DEBIAN, has_repository=True)
    settings = InstallSettings(git_identity=GitIdentity(), ngrok_authtoken=None)

    plan = PlanBuilder(toolkit).build_install_plan(settings)

    ids = _ids(plan)
    assert "ngrok-repo-gpg" in ids
    assert "ngrok-apt-repo" in ids
    assert "apt-update-after-ngrok-repo" in ids
    assert "bootstrap-package-manager" not in ids  # Debian ships apt already


def test_windows_plan_bootstraps_chocolatey_and_skips_repository_steps():
    toolkit = FakeToolkit(platform=PlatformId.WINDOWS, has_repository=False)
    settings = InstallSettings(git_identity=GitIdentity(), ngrok_authtoken=None)

    plan = PlanBuilder(toolkit).build_install_plan(settings)

    ids = _ids(plan)
    assert ids[0] == "bootstrap-package-manager"
    assert "ngrok-apt-repo" not in ids
    assert "ngrok-repo-gpg" not in ids


def test_plan_always_ends_with_the_sshd_step():
    toolkit = FakeToolkit()
    settings = InstallSettings(git_identity=GitIdentity(), ngrok_authtoken=None)

    plan = PlanBuilder(toolkit).build_install_plan(settings)

    assert _ids(plan)[-1] == "sshd-enable"


def test_git_identity_steps_are_only_added_when_supplied():
    toolkit = FakeToolkit()
    settings = InstallSettings(git_identity=GitIdentity(name="Ada"), ngrok_authtoken=None)

    ids = _ids(PlanBuilder(toolkit).build_install_plan(settings))

    assert "git-user-name" in ids
    assert "git-user-email" not in ids


def test_no_git_identity_means_no_config_steps_at_all():
    toolkit = FakeToolkit()
    settings = InstallSettings(git_identity=GitIdentity(), ngrok_authtoken=None)

    ids = _ids(PlanBuilder(toolkit).build_install_plan(settings))

    assert "git-user-name" not in ids
    assert "git-user-email" not in ids


def test_authtoken_step_only_added_when_a_token_is_supplied():
    toolkit = FakeToolkit()
    without = _ids(
        PlanBuilder(toolkit).build_install_plan(
            InstallSettings(git_identity=GitIdentity(), ngrok_authtoken=None)
        )
    )
    with_token = _ids(
        PlanBuilder(toolkit).build_install_plan(
            InstallSettings(git_identity=GitIdentity(), ngrok_authtoken="tok_123")
        )
    )

    assert "ngrok-authtoken" not in without
    assert "ngrok-authtoken" in with_token


def test_skip_packages_removes_the_matching_step():
    toolkit = FakeToolkit()
    settings = InstallSettings(
        git_identity=GitIdentity(), ngrok_authtoken=None, skip_packages=frozenset({"git"})
    )

    ids = _ids(PlanBuilder(toolkit).build_install_plan(settings))

    assert "git" not in ids
    assert "ngrok" in ids
