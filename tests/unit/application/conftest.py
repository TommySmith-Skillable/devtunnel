"""Shared fixtures for the application-layer tests.

The SSH keys here are built byte by byte rather than shelled out for: a unit
test that calls ``ssh-keygen`` is no longer a unit test, and a hardcoded real
public key in a repository is a thing nobody should have to think about again.
"""

from __future__ import annotations

import base64
import struct

from devtunnel.application.pairing import PairingBundle, build_bundle


def make_sshkey(key_type: str = "ssh-ed25519", seed: bytes = b"\x11") -> str:
    """A syntactically valid OpenSSH public key of ``key_type``.

    The blob must carry its own type in the first length-prefixed field --
    that agreement is exactly what ``pairing`` validates, so a helper that
    faked it would test nothing.
    """

    type_bytes = key_type.encode()
    blob = struct.pack(">I", len(type_bytes)) + type_bytes
    blob += struct.pack(">I", 32) + (seed * 32)[:32]
    return f"{key_type} {base64.b64encode(blob).decode()} test@example"


def make_nodekey(seed: str = "ab") -> str:
    return "nodekey:" + (seed * 64)[:64]


def make_bundle(name: str = "tester@laptop", seed: str = "ab") -> PairingBundle:
    return build_bundle(
        name=name,
        nodekey=make_nodekey(seed),
        sshkey=make_sshkey(seed=seed.encode()[:1] or b"\x11"),
        created="2026-10-02T14:21:07Z",
    )
