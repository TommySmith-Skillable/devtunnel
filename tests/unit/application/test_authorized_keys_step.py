"""``authorized_keys`` is the entire authentication boundary under D1.

These tests exist because every one of them describes a way to accidentally
widen or silently revoke access to a machine.
"""

import pytest

from devtunnel.application.steps import EnsureAuthorizedKeysStep
from devtunnel.domain.plan import StepStatus
from tests.fakes.context_builder import make_context
from tests.unit.application.conftest import make_sshkey

KEY_A = make_sshkey(seed=b"\x01")
KEY_B = make_sshkey(seed=b"\x02")


def test_appends_rather_than_overwriting_an_existing_file():
    ctx = make_context()
    ctx.filesystem.write_text(ctx.paths.authorized_keys_path, f"{KEY_A}\n")

    EnsureAuthorizedKeysStep("auth", "Authorize", (KEY_B,)).apply(ctx)

    content = ctx.filesystem.read_text(ctx.paths.authorized_keys_path)
    assert KEY_A in content  # the key the user put there by hand survives
    assert KEY_B in content


def test_does_not_duplicate_a_key_that_is_already_present():
    ctx = make_context()
    ctx.filesystem.write_text(ctx.paths.authorized_keys_path, f"{KEY_A}\n")

    outcome = EnsureAuthorizedKeysStep("auth", "Authorize", (KEY_A,)).apply(ctx)

    assert outcome.status is StepStatus.SKIPPED
    assert ctx.filesystem.read_text(ctx.paths.authorized_keys_path).count(KEY_A) == 1


def test_sets_0600_on_the_file():
    ctx = make_context()

    EnsureAuthorizedKeysStep("auth", "Authorize", (KEY_A,)).apply(ctx)

    assert ctx.filesystem.modes[ctx.paths.authorized_keys_path] == 0o600


def test_creates_the_ssh_directory_when_it_is_missing():
    ctx = make_context()

    EnsureAuthorizedKeysStep("auth", "Authorize", (KEY_A,)).apply(ctx)

    assert ctx.paths.ssh_dir in ctx.filesystem.dirs


def test_revert_restores_the_prior_file_byte_for_byte():
    ctx = make_context()
    original = f"{KEY_A}\n# a comment the user wrote\n"
    ctx.filesystem.write_text(ctx.paths.authorized_keys_path, original)
    step = EnsureAuthorizedKeysStep("auth", "Authorize", (KEY_B,))

    outcome = step.apply(ctx)
    step.revert(ctx, outcome.record)

    assert ctx.filesystem.read_text(ctx.paths.authorized_keys_path) == original


def test_revert_deletes_the_file_when_devtunnel_created_it():
    ctx = make_context()
    step = EnsureAuthorizedKeysStep("auth", "Authorize", (KEY_A,))

    outcome = step.apply(ctx)
    assert ctx.filesystem.exists(ctx.paths.authorized_keys_path)

    step.revert(ctx, outcome.record)
    assert not ctx.filesystem.exists(ctx.paths.authorized_keys_path)


def test_reads_keys_out_of_a_path_entry():
    ctx = make_context()
    ctx.filesystem.write_text("/tmp/mykey.pub", f"{KEY_A}\n")

    EnsureAuthorizedKeysStep("auth", "Authorize", ("/tmp/mykey.pub",)).apply(ctx)

    assert KEY_A in ctx.filesystem.read_text(ctx.paths.authorized_keys_path)


def test_a_github_entry_is_delegated_to_tailcat_rather_than_materialised():
    # Copying github.com/<user>.keys into the file would freeze a set the
    # user expects to stay live.
    ctx = make_context()

    outcome = EnsureAuthorizedKeysStep("auth", "Authorize", ("tommy@github",)).apply(ctx)

    assert outcome.record.details["delegated"] == ["tommy@github"]
    assert not ctx.filesystem.exists(ctx.paths.authorized_keys_path)


def test_a_missing_source_file_fails_loudly_rather_than_authorising_nothing():
    ctx = make_context()

    with pytest.raises(Exception, match="authorized-keys source not found"):
        EnsureAuthorizedKeysStep("auth", "Authorize", ("/nope/missing.pub",)).apply(ctx)
