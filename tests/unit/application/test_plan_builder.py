"""The four plan shapes, and the property that makes an unprivileged install
possible: a default plan containing no machine-scope step at all."""

import pytest

from devtunnel.application.pairing import InvalidBundleError
from devtunnel.application.plan_builder import (
    InstallSettings,
    PlanBuilder,
    plan_requires_elevation,
)
from devtunnel.domain.models import GitIdentity, PlatformId, Role
from tests.fakes.fake_ports import FakeProcessRunner, FakeToolkit, fake_paths
from tests.unit.application.conftest import make_bundle


def _builder(platform=PlatformId.DEBIAN, *, missing=()):
    return PlanBuilder(
        FakeToolkit(platform=platform), fake_paths(), FakeProcessRunner(missing=missing)
    )


def _ids(plan) -> list[str]:
    return [s.id for s in plan.walk()]


# -- the default (server) shape --------------------------------------------


def test_default_server_plan_installs_the_binary_and_generates_a_key():
    ids = _ids(_builder().build_install_plan(InstallSettings()))

    assert ids == ["install-tailcat-binary", "generate-tailcat-key"]


def test_default_server_plan_needs_no_elevation():
    plan = _builder().build_install_plan(InstallSettings())

    assert not plan_requires_elevation(plan)


def test_default_plan_never_installs_git():
    ids = _ids(_builder().build_install_plan(InstallSettings()))

    assert "git" not in ids


def test_authorized_keys_step_is_added_only_when_keys_are_supplied():
    without = _ids(_builder().build_install_plan(InstallSettings()))
    with_keys = _ids(
        _builder().build_install_plan(
            InstallSettings(authorized_keys=("~/.ssh/id_ed25519.pub",))
        )
    )

    assert "ensure-authorized-keys" not in without
    assert "ensure-authorized-keys" in with_keys


def test_each_peer_bundle_becomes_one_step():
    settings = InstallSettings(peers=(make_bundle("a@x").encode(), make_bundle("b@y").encode()))

    ids = _ids(_builder().build_install_plan(settings))

    assert "ensure-peer-0" in ids
    assert "ensure-peer-1" in ids


def test_a_malformed_peer_bundle_fails_while_the_plan_is_still_being_built():
    # Not half way through applying it, and not silently during a --dry-run
    # that would otherwise look clean.
    with pytest.raises(InvalidBundleError):
        _builder().build_install_plan(InstallSettings(peers=("dtp1:not-base64!!",)))


# -- the client shape ------------------------------------------------------


def test_client_plan_generates_a_client_key_and_an_ssh_identity():
    ids = _ids(_builder().build_install_plan(InstallSettings(role=Role.CLIENT)))

    assert ids == ["install-tailcat-binary", "generate-tailcat-key", "ensure-ssh-keypair"]


def test_client_plan_marks_the_key_as_a_client_key():
    plan = _builder().build_install_plan(InstallSettings(role=Role.CLIENT))
    step = next(s for s in plan.walk() if s.id == "generate-tailcat-key")

    assert step.client is True


def test_openssh_client_is_planned_only_when_ssh_keygen_is_missing():
    present = _ids(_builder().build_install_plan(InstallSettings(role=Role.CLIENT)))
    absent = _ids(
        _builder(missing=("ssh-keygen",)).build_install_plan(InstallSettings(role=Role.CLIENT))
    )

    assert "openssh-client" not in present
    assert absent[0] == "openssh-client"


def test_client_plan_needs_no_elevation_when_openssh_is_already_present():
    plan = _builder().build_install_plan(InstallSettings(role=Role.CLIENT))

    assert not plan_requires_elevation(plan)


# -- the --with-git shape --------------------------------------------------


def test_with_git_adds_the_git_steps_and_requires_elevation():
    plan = _builder().build_install_plan(InstallSettings(with_git=True))

    assert "git" in _ids(plan)
    assert plan_requires_elevation(plan)


def test_with_git_on_windows_prepends_the_chocolatey_bootstrap():
    ids = _ids(
        _builder(PlatformId.WINDOWS).build_install_plan(InstallSettings(with_git=True))
    )

    assert ids[0] == "bootstrap-package-manager"


def test_windows_without_git_never_bootstraps_chocolatey():
    ids = _ids(_builder(PlatformId.WINDOWS).build_install_plan(InstallSettings()))

    assert "bootstrap-package-manager" not in ids


def test_git_identity_steps_are_only_added_when_supplied():
    ids = _ids(
        _builder().build_install_plan(
            InstallSettings(with_git=True, git_identity=GitIdentity(name="Ada"))
        )
    )

    assert "git-user-name" in ids
    assert "git-user-email" not in ids


def test_git_identity_is_ignored_without_with_git():
    ids = _ids(
        _builder().build_install_plan(InstallSettings(git_identity=GitIdentity(name="Ada")))
    )

    assert "git-user-name" not in ids


# -- the --system shape ----------------------------------------------------


def test_system_scope_makes_the_binary_step_machine_scope():
    plan = _builder().build_install_plan(InstallSettings(system_scope=True))
    step = next(s for s in plan.walk() if s.id == "install-tailcat-binary")

    assert step.scope.value == "machine"
    assert plan_requires_elevation(plan)


# -- flags -----------------------------------------------------------------


def test_add_to_path_is_opt_in():
    assert "add-to-path" not in _ids(_builder().build_install_plan(InstallSettings()))
    assert "add-to-path" in _ids(
        _builder().build_install_plan(InstallSettings(add_to_path=True))
    )


def test_skip_packages_removes_the_matching_step():
    ids = _ids(
        _builder().build_install_plan(
            InstallSettings(with_git=True, skip_packages=frozenset({"git"}))
        )
    )

    assert "git" not in ids
    assert "install-tailcat-binary" in ids
