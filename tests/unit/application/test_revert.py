import pytest

from devtunnel.application.legacy_revert import (
    RemoveNgrokAptRepositoryStep,
    RemoveNgrokAuthtokenStep,
)
from devtunnel.application.revert import NoReverterRegisteredError, StepReverter
from devtunnel.application.steps import (
    AddToPathStep,
    BootstrapPackageManagerStep,
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
from devtunnel.domain.models import PlatformId, Scope


def _record(kind: ChangeKind, target: str, **details) -> ChangeRecord:
    return ChangeRecord.pending(kind, target, details=details).applied()


@pytest.mark.parametrize(
    ("kind", "target", "expected_type"),
    [
        (ChangeKind.PACKAGE_INSTALLED, "package:git", EnsurePackageStep),
        (ChangeKind.PACKAGE_INSTALLED, "package:openssh-client", EnsurePackageStep),
        (ChangeKind.BINARY_INSTALLED, "binary:tailcat", InstallTailcatBinaryStep),
        (ChangeKind.KEY_GENERATED, "tailcatkey:default", GenerateTailcatKeyStep),
        (ChangeKind.SERVICE_STATE_CHANGED, "service:tunnel", EnsureServiceStep),
        (ChangeKind.PKGMGR_BOOTSTRAPPED, "chocolatey", BootstrapPackageManagerStep),
        (ChangeKind.FILE_CREATED, "file:ssh_identity", EnsureSshKeypairStep),
        (ChangeKind.FILE_MODIFIED, "file:authorized_keys", EnsureAuthorizedKeysStep),
        (ChangeKind.FILE_MODIFIED, "peer:tommy@laptop", EnsurePeerStep),
        (ChangeKind.CONFIG_KEY_SET, "gitconfig:user.name", SetGitConfigStep),
        (ChangeKind.CONFIG_KEY_SET, "path:tailcat", AddToPathStep),
    ],
)
def test_reverter_reconstructs_the_matching_step_type(kind, target, expected_type):
    assert isinstance(StepReverter().for_record(_record(kind, target)), expected_type)


def test_file_modified_is_dispatched_by_target_prefix_not_kind_alone():
    # The one case that would be ambiguous on kind alone: authorized_keys and
    # a peer are both FILE_MODIFIED and revert very differently.
    authorized = StepReverter().for_record(
        _record(ChangeKind.FILE_MODIFIED, "file:authorized_keys")
    )
    peer = StepReverter().for_record(_record(ChangeKind.FILE_MODIFIED, "peer:a@b"))

    assert type(authorized) is not type(peer)


def test_peer_reverter_rebuilds_the_bundle_from_the_record_alone():
    record = _record(
        ChangeKind.FILE_MODIFIED,
        "peer:tommy@laptop",
        name="tommy@laptop",
        nodekey="nodekey:" + "ab" * 32,
        sshkey="ssh-ed25519 AAAA tommy",
    )

    step = StepReverter().for_record(record)

    assert step.bundle.name == "tommy@laptop"
    assert step.bundle.nodekey.endswith("ab")


def test_reverter_routes_its_writes_back_to_the_scope_that_wrote_the_record():
    record = _record(ChangeKind.PACKAGE_INSTALLED, "package:git", scope="machine")

    step = StepReverter().for_record(record)

    assert step.scope is Scope.MACHINE


def test_reverter_raises_for_an_unrecognised_target():
    with pytest.raises(NoReverterRegisteredError):
        StepReverter().for_record(_record(ChangeKind.CONFIG_KEY_SET, "something-unexpected"))


# -- ngrok-era journals (plan section 16) ----------------------------------


def test_a_legacy_apt_repository_record_still_has_a_reverter():
    step = StepReverter().for_record(_record(ChangeKind.APT_REPO_ADDED, "repository:ngrok"))

    assert isinstance(step, RemoveNgrokAptRepositoryStep)


def test_a_legacy_authtoken_record_still_has_a_reverter():
    step = StepReverter().for_record(_record(ChangeKind.CONFIG_KEY_SET, "ngrok:authtoken"))

    assert isinstance(step, RemoveNgrokAuthtokenStep)


def test_a_legacy_ngrok_package_record_still_has_a_reverter():
    step = StepReverter().for_record(_record(ChangeKind.PACKAGE_INSTALLED, "package:ngrok"))

    assert isinstance(step, EnsurePackageStep)
    assert step.package.key == "ngrok"


def test_a_legacy_openssh_server_record_uses_the_capability_path_on_windows():
    # The live toolkit no longer routes openssh-server to the capability
    # manager, so handing it to Chocolatey would fail confusingly.
    reverter = StepReverter(PlatformId.WINDOWS)

    step = reverter.for_record(_record(ChangeKind.PACKAGE_INSTALLED, "package:openssh-server"))

    assert type(step).__name__ == "RemoveWindowsCapabilityStep"


def test_a_legacy_sshd_service_record_still_has_a_reverter():
    step = StepReverter().for_record(_record(ChangeKind.SERVICE_STATE_CHANGED, "service:sshd"))

    assert isinstance(step, EnsureServiceStep)
    assert step.service.key == "sshd"
