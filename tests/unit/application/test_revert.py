import pytest

from devtunnel.application.revert import NoReverterRegisteredError, StepReverter
from devtunnel.application.steps import (
    BootstrapPackageManagerStep,
    ConfigureNgrokAuthtokenStep,
    EnsurePackageStep,
    EnsureRepositoryStep,
    EnsureServiceStep,
    SetGitConfigStep,
)
from devtunnel.domain.journal import ChangeKind, ChangeRecord


def _record(kind: ChangeKind, target: str) -> ChangeRecord:
    return ChangeRecord.pending(kind, target).applied()


@pytest.mark.parametrize(
    ("kind", "target", "expected_type"),
    [
        (ChangeKind.PACKAGE_INSTALLED, "package:git", EnsurePackageStep),
        (ChangeKind.SERVICE_STATE_CHANGED, "service:sshd", EnsureServiceStep),
        (ChangeKind.APT_REPO_ADDED, "repository:ngrok", EnsureRepositoryStep),
        (ChangeKind.PKGMGR_BOOTSTRAPPED, "chocolatey", BootstrapPackageManagerStep),
        (ChangeKind.CONFIG_KEY_SET, "gitconfig:user.name", SetGitConfigStep),
        (ChangeKind.CONFIG_KEY_SET, "ngrok:authtoken", ConfigureNgrokAuthtokenStep),
    ],
)
def test_reverter_reconstructs_the_matching_step_type(kind, target, expected_type):
    record = _record(kind, target)

    step = StepReverter().for_record(record)

    assert isinstance(step, expected_type)


def test_reverter_raises_for_an_unrecognised_target():
    record = _record(ChangeKind.CONFIG_KEY_SET, "something-unexpected")

    with pytest.raises(NoReverterRegisteredError):
        StepReverter().for_record(record)
