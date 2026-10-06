"""The pairing bundle: one token carrying both halves of a peer's identity.

A client is useless to a host until *two* public keys are installed there --
the tailcat node key (which gates the tunnel) and the SSH public key (which
gates the shell). Shipping them separately is how operators end up in the two
broken half-states described in the plan (allow-listed but not authorized, or
authorized but unreachable), so they travel together as a single
``dtp1:<base64url(json)>`` string that is produced, displayed and consumed as
one unit.

This module is deliberately *pure*: no files, no subprocesses, no ports. The
bundle is attacker-influenced input (plan 13.8/13.9 -- anyone who can get a
token into the operator's clipboard is asking for both tunnel and shell
access), and the only way to be confident about validation is to be able to
exercise every rejection path in a unit test with nothing mocked. Both the CLI
and the steps layer parse through this one module so there is exactly one
place where the rules live.

Parsing is strict in both directions: every rejection raises
:class:`InvalidBundleError` with a message naming the specific problem, because
"invalid bundle" tells an operator nothing about whether they pasted half a
token, got sent a future-version token, or are being probed.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import re
import struct
from dataclasses import dataclass
from datetime import UTC, datetime

from devtunnel.domain.errors import DevtunnelError

BUNDLE_PREFIX = "dtp1:"
BUNDLE_VERSION = 1

DEFAULT_NAME = "unnamed"
MAX_NAME_LENGTH = 64

ACCEPTED_KEY_TYPES: frozenset[str] = frozenset(
    {
        "ssh-ed25519",
        "ssh-rsa",
        "ecdsa-sha2-nistp256",
        "ecdsa-sha2-nistp384",
        "ecdsa-sha2-nistp521",
        "sk-ssh-ed25519@openssh.com",
        "sk-ecdsa-sha2-nistp256@openssh.com",
    }
)
"""Key types devtunnel is willing to write into ``authorized_keys``.

An allow-list rather than a deny-list: an unfamiliar key type is far more
likely to be a probe or a mangled paste than a key the host can actually use,
and refusing it costs the operator one clear error message.
"""

NODEKEY_PREFIX = "nodekey:"

_FIELDS: frozenset[str] = frozenset({"v", "name", "nodekey", "sshkey", "created"})
_NAME_RE = re.compile(r"[A-Za-z0-9._@-]+")
_HEX64_RE = re.compile(r"[0-9a-fA-F]{64}")
_BASE64URL_RE = re.compile(r"[A-Za-z0-9_-]+")
_CREATED_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z")
_VERSIONED_PREFIX_RE = re.compile(r"dtp(\d+):")

# Anything that can end a line -- or truncate a string on the way through a
# tool that reads the file -- must never reach authorized_keys. Checked on the
# raw strings, before any parse that might normalise it out of sight.
_LINE_BREAKERS = ("\n", "\r", "\x00")


class InvalidBundleError(DevtunnelError):
    """Raised when a pairing bundle cannot be trusted to mean what it says.

    The message always names the one specific problem. The host operator is the
    audience, and they need to know whether to ask for the token again, ask for
    a newer devtunnel, or treat the token as hostile.
    """


def _reject(reason: str) -> InvalidBundleError:
    return InvalidBundleError(f"invalid pairing bundle: {reason}")


def _require_no_line_breakers(field: str, value: str) -> None:
    for char in _LINE_BREAKERS:
        if char in value:
            raise _reject(
                f"{field} contains {char!r}, which could inject a second line "
                f"into authorized_keys"
            )


def normalise_nodekey(value: str) -> str:
    """Canonicalise a tailcat node key to ``nodekey:<64 lowercase hex>``.

    Bare hex is accepted because that is what people end up copying when the
    prefix wraps onto its own line in a terminal or chat client. Storing one
    canonical form means the allowlist entry, the journal target and the
    fingerprint input are byte-identical no matter which spelling arrived --
    which is what lets a later ``pair remove`` find the entry ``pair add``
    wrote instead of leaving half a peer behind (plan 13.10).
    """

    if not isinstance(value, str):
        raise _reject(f"nodekey must be a string, got {type(value).__name__}")
    candidate = value.strip()
    _require_no_line_breakers("nodekey", candidate)
    if candidate.lower().startswith(NODEKEY_PREFIX):
        candidate = candidate[len(NODEKEY_PREFIX) :]
    if not candidate:
        raise _reject("nodekey is empty")
    if not _HEX64_RE.fullmatch(candidate):
        raise _reject(
            f"nodekey must be 64 hexadecimal characters, optionally prefixed "
            f"{NODEKEY_PREFIX!r}; got {len(candidate)} character(s): {candidate!r}"
        )
    return f"{NODEKEY_PREFIX}{candidate.lower()}"


def _normalise_sshkey(value: str) -> str:
    """Canonicalise and *verify* an OpenSSH public key line.

    The cheap check is that the line splits into ``<type> <blob> [comment]``.
    The check that earns its keep is that the key type encoded inside the
    base64 blob matches the declared type: an OpenSSH blob opens with a 4-byte
    big-endian length followed by the type string, so a line claiming
    ``ssh-ed25519`` in front of an RSA blob -- or in front of junk -- is either
    a corrupted paste or someone measuring what this parser will swallow.
    sshd would reject it eventually; rejecting it here means the operator finds
    out before the key is on disk rather than after a tunnel that mysteriously
    will not log in.
    """

    if not isinstance(value, str):
        raise _reject(f"sshkey must be a string, got {type(value).__name__}")
    _require_no_line_breakers("sshkey", value)
    parts = value.split()
    if len(parts) < 2:
        raise _reject(
            "sshkey is not an OpenSSH public key line "
            "(expected '<type> <base64> [comment]')"
        )
    key_type, blob_text, *comment_parts = parts
    if key_type not in ACCEPTED_KEY_TYPES:
        raise _reject(
            f"sshkey type {key_type!r} is not one devtunnel accepts "
            f"({', '.join(sorted(ACCEPTED_KEY_TYPES))})"
        )
    try:
        blob = base64.b64decode(blob_text, validate=True)
    except (binascii.Error, ValueError):
        raise _reject(f"sshkey body for {key_type} is not valid base64") from None
    if len(blob) < 4:
        raise _reject(f"sshkey body for {key_type} is too short to contain a key type")
    (declared_length,) = struct.unpack(">I", blob[:4])
    if declared_length > len(blob) - 4:
        raise _reject(
            f"sshkey body for {key_type} is truncated: it claims a "
            f"{declared_length}-byte type field but only {len(blob) - 4} byte(s) follow"
        )
    embedded = blob[4 : 4 + declared_length].decode("ascii", errors="replace")
    if embedded != key_type:
        raise _reject(f"sshkey declares type {key_type!r} but its body encodes {embedded!r}")
    comment = " ".join(comment_parts)
    return f"{key_type} {blob_text} {comment}".rstrip()


def _normalise_name(value: object) -> str:
    """Accept only characters that are safe inside an ``authorized_keys`` comment.

    This is the security-critical validation in the module. ``name`` is echoed
    into the comment field of the line appended to ``authorized_keys``, so a
    newline smuggled through it would append a *second*, unreviewed key line
    that the operator never saw in the confirmation prompt -- a complete tunnel
    and shell grant hidden behind a label. The whole class of attack disappears
    once the character set is an allow-list.

    It rejects rather than silently strips. A name that arrives with a newline
    in it is exactly the information the operator needs: the confirmation
    prompt exists to show them what they were actually sent, not a laundered
    version of it.
    """

    if value is None or value == "":
        return DEFAULT_NAME
    if not isinstance(value, str):
        raise _reject(f"name must be a string, got {type(value).__name__}")
    # Character set before length, deliberately: a name carrying a newline is a
    # smuggled second authorized_keys line, and an injected line is usually long
    # enough to trip the cap too. Reporting "too long" for it would describe the
    # symptom and hide the attack from the operator reading the error.
    if not _NAME_RE.fullmatch(value):
        offenders = sorted({char for char in value if not _NAME_RE.fullmatch(char)})
        raise _reject(
            f"name contains character(s) outside [A-Za-z0-9._@-]: "
            f"{', '.join(repr(char) for char in offenders)}"
        )
    if len(value) > MAX_NAME_LENGTH:
        raise _reject(
            f"name is {len(value)} characters, longer than the "
            f"{MAX_NAME_LENGTH}-character limit"
        )
    return value


def _normalise_created(value: object) -> str:
    """Validate the timestamp while never letting it gate a decision.

    ``created`` is informational -- it tells an operator how stale the token
    sitting in their inbox is. Nothing authorises on it, so an absent one
    defaults to the empty string rather than failing an otherwise sound decode.
    A *malformed* one is still refused, because a timestamp that cannot be read
    is worse than no timestamp at all: it invites being misread.
    """

    if value is None or value == "":
        return ""
    if not isinstance(value, str):
        raise _reject(f"created must be a string, got {type(value).__name__}")
    if not _CREATED_RE.fullmatch(value):
        raise _reject(
            f"created must be an RFC3339 UTC timestamp like "
            f"'2026-10-02T14:21:07Z', got {value!r}"
        )
    return value


def _utcnow_rfc3339() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def fingerprint_of(nodekey: str, sshkey: str) -> str:
    """A short digest over *both* keys, for out-of-band confirmation.

    Both ends must print the same string or the confirmation step in ``pair
    add`` is theatre, so the digest is taken over the canonical forms and never
    over whatever spelling was typed -- a bare-hex node key and a prefixed one
    are the same peer and must fingerprint identically.

    The two inputs are joined with an explicit ``\\n`` separator. Both canonical
    forms are guaranteed newline-free by the validation above, so the separator
    cannot occur inside either value; without it, a crafted pair could shift
    bytes across the nodekey/sshkey boundary and hash identically to a
    different pair of keys, which would let a confirmed fingerprint stand in
    for keys the operator never approved.
    """

    canonical_nodekey = normalise_nodekey(nodekey)
    canonical_sshkey = _normalise_sshkey(sshkey)
    digest = hashlib.sha256(f"{canonical_nodekey}\n{canonical_sshkey}".encode()).hexdigest()
    return "-".join(digest[i : i + 4] for i in range(0, 16, 4))


@dataclass(frozen=True, slots=True)
class PairingBundle:
    """A peer's two public keys plus the label they will be filed under.

    Frozen because a bundle is displayed, confirmed by fingerprint and only
    then written: nothing between the prompt and the write may alter what the
    operator approved. Build one through :func:`build_bundle` or
    :func:`decode_bundle` -- those are the paths that validate, and the fields
    here are assumed already canonical.
    """

    name: str
    nodekey: str
    sshkey: str
    created: str

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.nodekey, self.sshkey)

    @property
    def nodekey_short(self) -> str:
        """An abbreviation for display only -- never for comparison.

        Verification happens against :attr:`fingerprint`, which covers both
        keys at once. Truncated key material in a terminal is for recognising
        a line, not for deciding to trust it.
        """

        return f"{self.nodekey[: len(NODEKEY_PREFIX) + 8]}..."

    @property
    def sshkey_short(self) -> str:
        key_type, blob, *_ = self.sshkey.split()
        return f"{key_type} {blob[:8]}..."

    def encode(self) -> str:
        """Render the token the client reads out and the host pastes in.

        Compact separators and unpadded base64url keep it to one pasteable
        blob, and dropping the ``=`` padding avoids the character chat clients
        and URL detectors are most likely to mangle. Field order is fixed so
        the same bundle always produces the same token: a token that changes
        shape between runs looks tampered with to the person holding it.
        """

        payload = {
            "v": BUNDLE_VERSION,
            "name": self.name,
            "nodekey": self.nodekey,
            "sshkey": self.sshkey,
            "created": self.created,
        }
        raw = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        encoded = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        return f"{BUNDLE_PREFIX}{encoded}"

    def authorized_keys_line(self) -> str:
        """The exact single line to append to the host's ``authorized_keys``.

        Newline-terminated, because the file is line-oriented and a missing
        terminator silently welds this key onto whatever is appended next. The
        ``devtunnel-peer:`` comment prefix is what ``pair remove`` and uninstall
        match on to take the line back out, so every line devtunnel writes must
        carry it or the peer can only be half-revoked (plan 13.10).

        The line is injection-free by construction rather than by escaping: the
        key type comes from a fixed allow-list, the blob is validated base64,
        and the name is restricted to ``[A-Za-z0-9._@-]``. None of the three can
        contain whitespace or a line break.
        """

        key_type, blob, *_ = self.sshkey.split()
        return f"{key_type} {blob} devtunnel-peer:{self.name}\n"


def build_bundle(
    *,
    name: str,
    nodekey: str,
    sshkey: str,
    created: str | None = None,
) -> PairingBundle:
    """Validate and canonicalise this machine's own values into a bundle.

    The export side validates with exactly the same rules as the import side.
    A client able to mint a token its own host-side parser would refuse pushes
    discovery of the problem to the far end of a chat window, where nobody can
    debug it.
    """

    return PairingBundle(
        name=_normalise_name(name),
        nodekey=normalise_nodekey(nodekey),
        sshkey=_normalise_sshkey(sshkey),
        created=_normalise_created(created) if created is not None else _utcnow_rfc3339(),
    )


def decode_bundle(token: str) -> PairingBundle:
    """Parse a ``dtp1:`` token, refusing anything it does not fully understand.

    Every failure mode gets its own message: an operator who pasted half a
    token needs different advice from one who was sent a newer devtunnel's
    token, and neither is helped by a ``binascii`` or ``json`` traceback. The
    underlying decoder exceptions are suppressed (``from None``) rather than
    chained, so no attacker-supplied byte string reaches the terminal by way of
    a nested exception message.
    """

    if not isinstance(token, str):
        raise _reject(f"expected a string token, got {type(token).__name__}")
    text = token.strip()
    if not text:
        raise _reject("token is empty")

    if not text.startswith(BUNDLE_PREFIX):
        versioned = _VERSIONED_PREFIX_RE.match(text)
        if versioned:
            raise _reject(
                f"bundle format version {versioned.group(1)} is not supported by this "
                f"devtunnel, which understands version {BUNDLE_VERSION} "
                f"({BUNDLE_PREFIX!r}); upgrade devtunnel on this machine"
            )
        raise _reject(
            f"token does not start with the {BUNDLE_PREFIX!r} prefix -- "
            f"it is not a devtunnel pairing bundle"
        )

    body = text[len(BUNDLE_PREFIX) :]
    if not body or not _BASE64URL_RE.fullmatch(body.rstrip("=")):
        raise _reject("payload is not valid base64url; the token may have been truncated")
    padded = body + "=" * (-len(body) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError):
        raise _reject(
            "payload is not valid base64url; the token may have been truncated"
        ) from None

    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise _reject("payload does not decode to JSON") from None

    if not isinstance(data, dict):
        raise _reject(f"payload is a JSON {type(data).__name__}, not an object")

    if "v" not in data:
        raise _reject("payload is missing the required 'v' version field")
    version = data["v"]
    if isinstance(version, bool) or not isinstance(version, int) or version != BUNDLE_VERSION:
        raise _reject(
            f"bundle version {version!r} is not supported by this devtunnel, "
            f"which understands version {BUNDLE_VERSION}"
        )

    unknown = sorted(set(data) - _FIELDS)
    if unknown:
        # Strict by choice: a field this version does not implement means the
        # sender expected behaviour this host will not perform. Ignoring it is
        # how an unenforced restriction ships as though it were enforced.
        raise _reject(
            f"payload contains field(s) this version does not understand: "
            f"{', '.join(repr(key) for key in unknown)}"
        )

    for required in ("nodekey", "sshkey"):
        if required not in data:
            raise _reject(f"payload is missing the required {required!r} field")

    return PairingBundle(
        name=_normalise_name(data.get("name")),
        nodekey=normalise_nodekey(data["nodekey"]),
        sshkey=_normalise_sshkey(data["sshkey"]),
        created=_normalise_created(data.get("created")),
    )
