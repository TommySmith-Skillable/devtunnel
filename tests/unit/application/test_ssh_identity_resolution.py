"""The identity a reader finds is the identity the install step adopted.

``EnsureSshKeypairStep`` adopts an existing ``id_ecdsa``/``id_rsa`` rather than
clobbering it, but every reader asked ``default_ssh_identity()``, which is the
fixed name devtunnel *generates* (``id_ed25519``). On a machine whose only key
was ``id_rsa``, install succeeded by adopting it and then ``pair export`` --
and the tail of ``install --client`` itself -- reported "no SSH public key ...
run 'devtunnel install --client' first".
"""

from __future__ import annotations

import os

from devtunnel.application.paths import CANDIDATE_SSH_IDENTITIES
from devtunnel.application.steps import _ADOPTABLE_IDENTITIES
from tests.fakes.fake_ports import FakeFileSystem, fake_paths

HOME = "/home/fake"


def paths_and_fs(*present: str):
    paths = fake_paths(HOME)
    filesystem = FakeFileSystem()
    for name in present:
        filesystem.files[os.path.join(paths.ssh_dir, name)] = "private"
    return paths, filesystem


def test_an_adopted_rsa_key_is_what_resolves():
    paths, filesystem = paths_and_fs("id_rsa")

    assert paths.resolve_ssh_identity(filesystem) == os.path.join(paths.ssh_dir, "id_rsa")


def test_an_adopted_ecdsa_key_is_what_resolves():
    paths, filesystem = paths_and_fs("id_ecdsa")

    assert paths.resolve_ssh_identity(filesystem) == os.path.join(paths.ssh_dir, "id_ecdsa")


def test_ed25519_wins_when_several_identities_exist():
    """Same precedence the adopt step applies, so the two agree."""

    paths, filesystem = paths_and_fs("id_rsa", "id_ecdsa", "id_ed25519")

    assert paths.resolve_ssh_identity(filesystem) == os.path.join(paths.ssh_dir, "id_ed25519")


def test_resolution_falls_back_to_the_generated_name_when_nothing_exists():
    """So a missing-key message still names the file a user expects."""

    paths, filesystem = paths_and_fs()

    assert paths.resolve_ssh_identity(filesystem) == paths.default_ssh_identity()


def test_default_identity_remains_the_fixed_generated_name():
    """It answers "what should I create?", and must not become a lookup."""

    paths, _ = paths_and_fs("id_rsa")

    assert paths.default_ssh_identity() == os.path.join(paths.ssh_dir, "id_ed25519")


def test_the_step_and_the_resolver_share_one_candidate_order():
    """Two copies of this tuple are how the reader and the step drifted apart."""

    assert _ADOPTABLE_IDENTITIES is CANDIDATE_SSH_IDENTITIES
