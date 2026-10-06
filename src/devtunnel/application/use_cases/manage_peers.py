"""Use case: enrol, list and revoke peers.

A peer is two public keys that must be installed and removed **together** --
see :class:`~devtunnel.application.steps.EnsurePeerStep` for why a half-applied
or half-revoked peer is the specific hazard this design exists to prevent.
This use case is the layer above that: it decodes the bundle, finds the record
a peer already has, and refuses an ambiguous revoke rather than guessing.

The journal is the source of truth for who is enrolled. The allowlist file is a
projection of it that tailcat can consume -- which means ``pair list`` reports
what was actually journaled, not what happens to be in a file someone may have
hand-edited.
"""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application.context import ExecutionContext
from devtunnel.application.pairing import PairingBundle, decode_bundle
from devtunnel.application.steps import EnsurePeerStep
from devtunnel.domain.errors import DevtunnelError
from devtunnel.domain.events import Phase
from devtunnel.domain.journal import ChangeKind, ChangeRecord, RecordStatus
from devtunnel.domain.plan import StepOutcome


@dataclass(frozen=True, slots=True)
class Peer:
    """An enrolled peer, as reconstructed from its journal record."""

    name: str
    nodekey: str
    sshkey: str
    fingerprint: str
    created: str
    record_id: str

    @classmethod
    def from_record(cls, record: ChangeRecord) -> Peer:
        details = record.details
        return cls(
            name=details.get("name", record.target.removeprefix("peer:")),
            nodekey=details.get("nodekey", ""),
            sshkey=details.get("sshkey", ""),
            fingerprint=details.get("fingerprint", ""),
            created=details.get("created", ""),
            record_id=record.id,
        )


class UnknownPeerError(DevtunnelError):
    """Raised when a name or node key matches no enrolled peer."""


class ManagePeersUseCase:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    # -- reading ---------------------------------------------------------

    def list_peers(self) -> list[Peer]:
        return [Peer.from_record(r) for r in self._peer_records()]

    def _peer_records(self) -> list[ChangeRecord]:
        return [
            r
            for r in self._ctx.journal.load()
            if r.kind is ChangeKind.FILE_MODIFIED
            and r.target.startswith("peer:")
            and r.status in (RecordStatus.PENDING, RecordStatus.APPLIED, RecordStatus.REVERT_FAILED)
        ]

    # -- enrolling -------------------------------------------------------

    def add(self, token: str) -> tuple[PairingBundle, StepOutcome]:
        """Decode and install one pairing bundle.

        Decoding happens before anything is touched, so a malformed or
        hostile bundle is rejected by name without having modified the
        allowlist or ``authorized_keys``.
        """

        bundle = decode_bundle(token)
        self._ctx.phase = Phase.INSTALL
        step = EnsurePeerStep(
            f"pair-add:{bundle.name}", f"Authorise peer {bundle.name!r}", bundle
        )
        return bundle, step.apply(self._ctx)

    # -- revoking --------------------------------------------------------

    def remove(self, identifier: str) -> StepOutcome:
        """Revoke a peer by display name or node key.

        Both halves go, or the revert raises -- a partial revoke that reported
        success would leave a working shell credential on the box for someone
        who was supposed to have lost access.
        """

        record = self._find(identifier)
        self._ctx.phase = Phase.UNINSTALL
        step = EnsurePeerStep(
            f"pair-remove:{record.id}", "Revoke peer", _bundle(record)
        )
        return step.revert(self._ctx, record)

    def _find(self, identifier: str) -> ChangeRecord:
        wanted = identifier.strip()
        matches = [
            r
            for r in self._peer_records()
            if r.details.get("name") == wanted
            or r.details.get("nodekey") == wanted
            or r.details.get("nodekey", "").removeprefix("nodekey:") == wanted
        ]
        if not matches:
            raise UnknownPeerError(f"no enrolled peer matches {identifier!r}")
        if len(matches) > 1:
            raise DevtunnelError(
                f"{identifier!r} matches {len(matches)} enrolled peers; "
                "name the node key instead"
            )
        return matches[0]


def _bundle(record: ChangeRecord) -> PairingBundle:
    details = record.details
    return PairingBundle(
        name=details.get("name", record.target.removeprefix("peer:")),
        nodekey=details.get("nodekey", ""),
        sshkey=details.get("sshkey", ""),
        created=details.get("created", ""),
    )


__all__ = ["ManagePeersUseCase", "Peer", "UnknownPeerError"]
