"""One peer, one record, both halves.

A half-enrolled peer cannot log in; a half-revoked one still can. The second
is the one that matters, and every test here is about making it impossible to
reach quietly.
"""

import pytest

from devtunnel.application.allowlist import Allowlist
from devtunnel.application.steps import EnsurePeerStep
from devtunnel.domain.plan import StepStatus
from tests.fakes.context_builder import make_context
from tests.unit.application.conftest import make_bundle


def _allowlist(ctx):
    return Allowlist(ctx.filesystem, ctx.paths.allowlist_path)


def test_one_record_covers_both_halves_of_a_peer():
    ctx = make_context()
    bundle = make_bundle()

    outcome = EnsurePeerStep("peer", "Authorise", bundle).apply(ctx)

    assert _allowlist(ctx).contains(bundle.nodekey)
    assert bundle.sshkey.split()[1] in ctx.filesystem.read_text(ctx.paths.authorized_keys_path)
    assert len(ctx.journal.load()) == 1
    assert outcome.record.details["nodekey"] == bundle.nodekey


def test_revert_removes_both_halves():
    ctx = make_context()
    bundle = make_bundle()
    step = EnsurePeerStep("peer", "Authorise", bundle)

    outcome = step.apply(ctx)
    step.revert(ctx, outcome.record)

    assert not _allowlist(ctx).contains(bundle.nodekey)
    assert not ctx.filesystem.exists(ctx.paths.authorized_keys_path)


def test_revert_leaves_other_keys_in_authorized_keys_alone():
    ctx = make_context()
    other = "ssh-ed25519 AAAAsomeoneelse someone@else\n"
    ctx.filesystem.write_text(ctx.paths.authorized_keys_path, other)
    step = EnsurePeerStep("peer", "Authorise", make_bundle())

    outcome = step.apply(ctx)
    step.revert(ctx, outcome.record)

    assert ctx.filesystem.read_text(ctx.paths.authorized_keys_path) == other


def test_adding_the_same_peer_twice_is_idempotent():
    ctx = make_context()
    bundle = make_bundle()

    EnsurePeerStep("peer", "Authorise", bundle).apply(ctx)
    second = EnsurePeerStep("peer", "Authorise", bundle).apply(ctx)

    assert second.status is StepStatus.SKIPPED
    assert _allowlist(ctx).entries().count(bundle.nodekey) == 1


def test_a_failure_removing_the_second_half_is_reported_not_swallowed():
    ctx = make_context()
    # Seed the file so revert restores a backup rather than deleting -- the
    # restore is the half that can realistically fail on a locked filesystem.
    ctx.filesystem.write_text(ctx.paths.authorized_keys_path, "# prior\n")
    bundle = make_bundle()
    step = EnsurePeerStep("peer", "Authorise", bundle)
    outcome = step.apply(ctx)

    def explode(path, backup_path):
        raise OSError("disk is read-only")

    ctx.filesystem.restore_from_backup = explode

    revert = step.revert(ctx, outcome.record)

    assert revert.status is StepStatus.REVERT_FAILED
    assert "partially revoked" in revert.message
    assert "authorized_keys" in revert.message


def test_a_partial_revoke_names_which_half_survived():
    ctx = make_context()
    bundle = make_bundle()
    step = EnsurePeerStep("peer", "Authorise", bundle)
    outcome = step.apply(ctx)

    def explode(*_args, **_kwargs):
        raise OSError("allowlist is locked")

    ctx.filesystem.write_text = explode

    with pytest.raises(Exception) as exc_info:
        step.undo(ctx, outcome.record)

    assert "allowlist" in str(exc_info.value)


def test_two_peers_are_two_independent_records():
    ctx = make_context()
    a = make_bundle("a@x", seed="ab")
    b = make_bundle("b@y", seed="cd")

    EnsurePeerStep("peer-a", "Authorise", a).apply(ctx)
    EnsurePeerStep("peer-b", "Authorise", b).apply(ctx)

    targets = {r.target for r in ctx.journal.load()}
    assert targets == {"peer:a@x", "peer:b@y"}
    assert _allowlist(ctx).contains(a.nodekey)
    assert _allowlist(ctx).contains(b.nodekey)
