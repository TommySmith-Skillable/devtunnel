import base64
import json
import struct

import pytest

from devtunnel.application.pairing import (
    BUNDLE_PREFIX,
    BUNDLE_VERSION,
    DEFAULT_NAME,
    InvalidBundleError,
    PairingBundle,
    build_bundle,
    decode_bundle,
    fingerprint_of,
    normalise_nodekey,
)
from devtunnel.domain.errors import DevtunnelError

NODEKEY_HEX = "cfb6bfa77a0654d7450947fd6acef17d2cd848da1d30b2540b13dac272ddfd16"
CANONICAL_NODEKEY = f"nodekey:{NODEKEY_HEX}"


def make_blob(encoded_type: str, body: bytes = b"\x11" * 32) -> str:
    """Build a real OpenSSH wire-format blob: length-prefixed type, then body.

    Constructed here rather than shelled out to ``ssh-keygen`` so the tests
    stay hermetic, and so a blob whose encoded type disagrees with the declared
    one -- the case the parser actually has to catch -- can be produced at all.
    """

    raw = (
        struct.pack(">I", len(encoded_type))
        + encoded_type.encode()
        + struct.pack(">I", len(body))
        + body
    )
    return base64.b64encode(raw).decode("ascii")


def make_sshkey(
    key_type: str = "ssh-ed25519",
    *,
    encoded_type: str | None = None,
    comment: str = "devtunnel tommy@laptop",
) -> str:
    blob = make_blob(encoded_type if encoded_type is not None else key_type)
    line = f"{key_type} {blob}"
    return f"{line} {comment}" if comment else line


SSHKEY = make_sshkey()


def make_token(**overrides: object) -> str:
    """Encode an arbitrary payload as a dtp1 token, bypassing build_bundle.

    Decoding has to be tested against payloads a well-behaved client would
    never produce, which is exactly the input ``pair add`` is exposed to.
    """

    payload: dict = {
        "v": BUNDLE_VERSION,
        "name": "tommy@laptop",
        "nodekey": CANONICAL_NODEKEY,
        "sshkey": SSHKEY,
        "created": "2026-10-02T14:21:07Z",
    }
    for key, value in overrides.items():
        if value is None:
            payload.pop(key, None)
        else:
            payload[key] = value
    return encode_payload(payload)


def encode_payload(payload: object) -> str:
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return BUNDLE_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def valid_bundle(**overrides: object) -> PairingBundle:
    kwargs: dict = {
        "name": "tommy@laptop",
        "nodekey": CANONICAL_NODEKEY,
        "sshkey": SSHKEY,
        "created": "2026-10-02T14:21:07Z",
    }
    kwargs.update(overrides)
    return build_bundle(**kwargs)


def test_bundle_round_trips_from_export_to_add():
    original = valid_bundle()

    decoded = decode_bundle(original.encode())

    assert decoded == original


def test_encoded_token_carries_the_dtp1_prefix():
    assert valid_bundle().encode().startswith("dtp1:")


def test_decode_rejects_a_token_without_the_dtp1_prefix_by_name():
    token = valid_bundle().encode().removeprefix(BUNDLE_PREFIX)

    with pytest.raises(InvalidBundleError, match="does not start with the 'dtp1:' prefix"):
        decode_bundle(token)


def test_decode_rejects_a_future_prefix_as_an_unsupported_version_not_a_parse_error():
    token = "dtp2:" + valid_bundle().encode().removeprefix(BUNDLE_PREFIX)

    with pytest.raises(InvalidBundleError, match="version 2 is not supported"):
        decode_bundle(token)


def test_decode_rejects_an_unknown_version_field_by_name():
    with pytest.raises(InvalidBundleError, match="version 2 is not supported"):
        decode_bundle(make_token(v=2))


def test_decode_rejects_a_payload_with_no_version_field_by_name():
    with pytest.raises(InvalidBundleError, match="missing the required 'v' version field"):
        decode_bundle(make_token(v=None))


def test_decode_rejects_malformed_base64_without_leaking_a_binascii_error():
    with pytest.raises(InvalidBundleError, match="not valid base64url") as excinfo:
        decode_bundle("dtp1:not base64 at all!!")

    assert excinfo.value.__cause__ is None
    assert excinfo.value.__context__ is None


def test_decode_rejects_a_payload_that_is_not_json_without_leaking_a_json_error():
    body = base64.urlsafe_b64encode(b"this is not json").decode().rstrip("=")

    with pytest.raises(InvalidBundleError, match="does not decode to JSON") as excinfo:
        decode_bundle(BUNDLE_PREFIX + body)

    assert excinfo.value.__cause__ is None


def test_decode_rejects_a_payload_that_is_not_a_json_object():
    with pytest.raises(InvalidBundleError, match="not an object"):
        decode_bundle(encode_payload(["v", 1]))


def test_decode_rejects_a_missing_nodekey_by_name():
    with pytest.raises(InvalidBundleError, match="missing the required 'nodekey' field"):
        decode_bundle(make_token(nodekey=None))


def test_decode_rejects_a_missing_sshkey_by_name():
    with pytest.raises(InvalidBundleError, match="missing the required 'sshkey' field"):
        decode_bundle(make_token(sshkey=None))


def test_decode_rejects_an_unknown_top_level_field_and_names_it():
    with pytest.raises(InvalidBundleError, match="'expires'"):
        decode_bundle(make_token(expires="2027-01-01T00:00:00Z"))


def test_decode_accepts_a_bare_hex_nodekey_and_canonicalises_it_with_the_prefix():
    decoded = decode_bundle(make_token(nodekey=NODEKEY_HEX))

    assert decoded.nodekey == CANONICAL_NODEKEY


def test_nodekey_is_lowercased_so_both_ends_agree_on_the_canonical_form():
    assert normalise_nodekey(NODEKEY_HEX.upper()) == CANONICAL_NODEKEY
    assert normalise_nodekey(f"NODEKEY:{NODEKEY_HEX.upper()}") == CANONICAL_NODEKEY


def test_nodekey_of_the_wrong_length_is_rejected_by_name():
    with pytest.raises(InvalidBundleError, match="64 hexadecimal characters"):
        normalise_nodekey(NODEKEY_HEX[:-1])


def test_nodekey_with_non_hex_characters_is_rejected_by_name():
    with pytest.raises(InvalidBundleError, match="64 hexadecimal characters"):
        normalise_nodekey("z" + NODEKEY_HEX[1:])


def test_malformed_sshkey_with_bad_base64_is_rejected_by_name():
    with pytest.raises(InvalidBundleError, match="not valid base64"):
        build_bundle(
            name="tommy",
            nodekey=NODEKEY_HEX,
            sshkey="ssh-ed25519 AAAA***not base64***",
        )


def test_sshkey_whose_blob_encodes_a_different_type_is_rejected_by_name():
    forged = make_sshkey("ssh-ed25519", encoded_type="ssh-rsa")

    with pytest.raises(InvalidBundleError, match="but its body encodes 'ssh-rsa'"):
        build_bundle(name="tommy", nodekey=NODEKEY_HEX, sshkey=forged)


def test_sshkey_of_an_unsupported_type_is_rejected_by_name():
    exotic = make_sshkey("ssh-dss")

    with pytest.raises(InvalidBundleError, match="is not one devtunnel accepts"):
        build_bundle(name="tommy", nodekey=NODEKEY_HEX, sshkey=exotic)


def test_sshkey_without_a_blob_is_rejected_by_name():
    with pytest.raises(InvalidBundleError, match="not an OpenSSH public key line"):
        build_bundle(name="tommy", nodekey=NODEKEY_HEX, sshkey="ssh-ed25519")


def test_sshkey_whose_blob_claims_an_impossible_type_length_is_rejected_by_name():
    truncated = base64.b64encode(struct.pack(">I", 4096) + b"ssh-ed25519").decode()

    with pytest.raises(InvalidBundleError, match="truncated"):
        build_bundle(
            name="tommy",
            nodekey=NODEKEY_HEX,
            sshkey=f"ssh-ed25519 {truncated}",
        )


def test_every_accepted_key_type_round_trips():
    for key_type in (
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    ):
        bundle = valid_bundle(sshkey=make_sshkey(key_type))

        assert decode_bundle(bundle.encode()).sshkey.startswith(f"{key_type} ")


def test_a_newline_smuggled_through_name_is_rejected_when_building():
    injected = f"tommy\n{SSHKEY}"

    with pytest.raises(InvalidBundleError, match=r"outside \[A-Za-z0-9\._@-\]"):
        build_bundle(name=injected, nodekey=NODEKEY_HEX, sshkey=SSHKEY)


def test_a_newline_smuggled_through_name_never_reaches_an_authorized_keys_line():
    token = make_token(name=f"tommy\nssh-ed25519 {make_blob('ssh-ed25519')} attacker")

    with pytest.raises(InvalidBundleError) as excinfo:
        decode_bundle(token).authorized_keys_line()

    assert "outside [A-Za-z0-9._@-]" in str(excinfo.value)
    assert "'\\n'" in str(excinfo.value)


def test_a_carriage_return_in_name_is_rejected_too():
    with pytest.raises(InvalidBundleError, match=r"outside \[A-Za-z0-9\._@-\]"):
        build_bundle(name="tommy\rlaptop", nodekey=NODEKEY_HEX, sshkey=SSHKEY)


def test_name_with_a_space_or_shell_metacharacter_is_rejected_rather_than_stripped():
    with pytest.raises(InvalidBundleError, match="outside"):
        build_bundle(name="tommy laptop", nodekey=NODEKEY_HEX, sshkey=SSHKEY)
    with pytest.raises(InvalidBundleError, match="outside"):
        build_bundle(name="tommy;rm", nodekey=NODEKEY_HEX, sshkey=SSHKEY)


def test_name_longer_than_the_cap_is_rejected_by_name():
    with pytest.raises(InvalidBundleError, match="longer than the 64-character limit"):
        build_bundle(name="a" * 65, nodekey=NODEKEY_HEX, sshkey=SSHKEY)


def test_an_absent_or_empty_name_defaults_to_unnamed():
    assert decode_bundle(make_token(name=None)).name == DEFAULT_NAME
    assert build_bundle(name="", nodekey=NODEKEY_HEX, sshkey=SSHKEY).name == DEFAULT_NAME


def test_a_newline_anywhere_in_the_sshkey_is_rejected_before_it_is_parsed():
    with pytest.raises(InvalidBundleError, match="could inject a second line"):
        build_bundle(
            name="tommy",
            nodekey=NODEKEY_HEX,
            sshkey=f"{SSHKEY}\nssh-ed25519 {make_blob('ssh-ed25519')} attacker",
        )


def test_a_nul_byte_in_the_sshkey_comment_is_rejected():
    with pytest.raises(InvalidBundleError, match="could inject a second line"):
        build_bundle(name="tommy", nodekey=NODEKEY_HEX, sshkey=f"{SSHKEY}\x00")


def test_authorized_keys_line_is_exactly_one_newline_terminated_line():
    line = valid_bundle().authorized_keys_line()

    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert len(line.splitlines()) == 1


def test_authorized_keys_line_carries_the_peer_comment_used_for_revocation():
    line = valid_bundle().authorized_keys_line()
    key_type, blob, comment = line.rstrip("\n").split(" ")

    assert key_type == "ssh-ed25519"
    assert blob == SSHKEY.split()[1]
    assert comment == "devtunnel-peer:tommy@laptop"


def test_fingerprints_match_across_both_ends_of_the_exchange():
    client_side = valid_bundle()
    host_side = decode_bundle(client_side.encode())

    assert host_side.fingerprint == client_side.fingerprint
    assert host_side.fingerprint == fingerprint_of(NODEKEY_HEX, SSHKEY)


def test_fingerprint_is_four_groups_of_four_lowercase_hex_digits():
    fingerprint = valid_bundle().fingerprint

    groups = fingerprint.split("-")
    assert len(groups) == 4
    assert all(len(group) == 4 for group in groups)
    assert fingerprint == fingerprint.lower()
    assert all(char in "0123456789abcdef-" for char in fingerprint)


def test_fingerprint_ignores_the_spelling_of_the_nodekey_but_not_its_value():
    assert fingerprint_of(NODEKEY_HEX, SSHKEY) == fingerprint_of(CANONICAL_NODEKEY, SSHKEY)
    other = "a" * 64
    assert fingerprint_of(other, SSHKEY) != fingerprint_of(NODEKEY_HEX, SSHKEY)


def test_fingerprint_separates_the_two_key_domains_so_the_boundary_cannot_shift():
    # Same concatenated characters, different split point: the explicit
    # separator is what keeps these two from hashing to the same value.
    first = fingerprint_of(NODEKEY_HEX, make_sshkey(comment="ab"))
    second = fingerprint_of(NODEKEY_HEX, make_sshkey(comment="a b"))

    assert first != second


def test_short_display_forms_abbreviate_both_keys():
    bundle = valid_bundle()

    assert bundle.nodekey_short == "nodekey:cfb6bfa7..."
    assert bundle.sshkey_short == f"ssh-ed25519 {SSHKEY.split()[1][:8]}..."


def test_created_defaults_to_an_rfc3339_utc_stamp_when_not_supplied():
    bundle = build_bundle(name="tommy", nodekey=NODEKEY_HEX, sshkey=SSHKEY)

    assert bundle.created.endswith("Z")
    assert decode_bundle(bundle.encode()).created == bundle.created


def test_a_malformed_created_timestamp_is_rejected_by_name():
    with pytest.raises(InvalidBundleError, match="RFC3339 UTC timestamp"):
        decode_bundle(make_token(created="yesterday"))


def test_an_absent_created_timestamp_is_tolerated_because_nothing_authorises_on_it():
    assert decode_bundle(make_token(created=None)).created == ""


def test_decode_tolerates_whitespace_and_padding_around_a_pasted_token():
    token = valid_bundle().encode()
    padded = token + "=" * (-len(token.removeprefix(BUNDLE_PREFIX)) % 4)

    assert decode_bundle(f"  {token}\n") == decode_bundle(padded)


def test_decode_rejects_a_non_string_token_by_name():
    with pytest.raises(InvalidBundleError, match="expected a string token"):
        decode_bundle(None)


def test_invalid_bundle_error_is_catchable_as_a_devtunnel_error():
    with pytest.raises(DevtunnelError):
        decode_bundle("nonsense")


def test_bundle_is_frozen_so_a_confirmed_fingerprint_cannot_be_swapped_out():
    bundle = valid_bundle()

    with pytest.raises(AttributeError):
        bundle.name = "someone-else"
