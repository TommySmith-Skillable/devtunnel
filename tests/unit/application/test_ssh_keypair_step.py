"""Adopt, don't clobber.

An SSH private key is the one piece of state where getting the revert wrong is
unrecoverable, so the rule is absolute: a key devtunnel did not create is never
journaled and never deleted.
"""

import os

from devtunnel.application.steps import EnsureSshKeypairStep
from devtunnel.domain.journal import RecordStatus
from devtunnel.domain.plan import StepStatus
from tests.fakes.context_builder import make_context
from tests.unit.application.conftest import make_sshkey


def _identity(ctx, name="id_ed25519"):
    return os.path.join(ctx.paths.ssh_dir, name)


def test_adopts_an_existing_ed25519_identity_and_journals_nothing_revertable():
    ctx = make_context()
    ctx.filesystem.write_text(_identity(ctx), "PRIVATE")

    outcome = EnsureSshKeypairStep("ssh", "Create identity").apply(ctx)

    assert outcome.status is StepStatus.SKIPPED
    assert outcome.record.status is RecordStatus.SKIPPED_PREEXISTING
    assert not outcome.record.needs_revert
    assert ["ssh-keygen"] not in [c[:1] for c in ctx.process.calls]


def test_adopts_an_ecdsa_identity_when_there_is_no_ed25519_one():
    ctx = make_context()
    ctx.filesystem.write_text(_identity(ctx, "id_ecdsa"), "PRIVATE")

    outcome = EnsureSshKeypairStep("ssh", "Create identity").apply(ctx)

    assert outcome.status is StepStatus.SKIPPED


def test_generates_with_the_exact_ssh_keygen_argv_when_none_exists():
    ctx = make_context()

    EnsureSshKeypairStep("ssh", "Create identity").apply(ctx)

    call = next(c for c in ctx.process.calls if c[0] == "ssh-keygen")
    assert call[:6] == ["ssh-keygen", "-t", "ed25519", "-f", _identity(ctx), "-N"]
    assert call[6] == ""  # empty passphrase, so unattended connects work
    assert call[7] == "-C"
    assert call[8].startswith("devtunnel ")


def test_journals_the_public_half_and_the_path_but_never_the_private_key():
    ctx = make_context()
    public = make_sshkey()
    ctx.filesystem.write_text(f"{_identity(ctx)}.pub", public + "\n")

    outcome = EnsureSshKeypairStep("ssh", "Create identity").apply(ctx)

    details = outcome.record.details
    assert details["generated"] is True
    assert details["path"] == _identity(ctx)
    assert details["public_key"] == public
    assert "private" not in json_dumps(details).lower()


def test_revert_deletes_a_generated_pair():
    ctx = make_context()
    step = EnsureSshKeypairStep("ssh", "Create identity")
    outcome = step.apply(ctx)
    ctx.filesystem.write_text(_identity(ctx), "PRIVATE")
    ctx.filesystem.write_text(f"{_identity(ctx)}.pub", "PUBLIC")

    step.revert(ctx, outcome.record)

    assert not ctx.filesystem.exists(_identity(ctx))
    assert not ctx.filesystem.exists(f"{_identity(ctx)}.pub")


def test_revert_never_deletes_an_adopted_pair():
    ctx = make_context()
    ctx.filesystem.write_text(_identity(ctx), "PRIVATE")
    step = EnsureSshKeypairStep("ssh", "Create identity")

    outcome = step.apply(ctx)
    step.revert(ctx, outcome.record)

    assert ctx.filesystem.read_text(_identity(ctx)) == "PRIVATE"


def test_an_explicit_identity_path_is_used_instead_of_the_default():
    ctx = make_context()

    EnsureSshKeypairStep("ssh", "Create identity", identity_path="/keys/custom").apply(ctx)

    call = next(c for c in ctx.process.calls if c[0] == "ssh-keygen")
    assert "/keys/custom" in call


def json_dumps(value) -> str:
    import json

    return json.dumps(value)
