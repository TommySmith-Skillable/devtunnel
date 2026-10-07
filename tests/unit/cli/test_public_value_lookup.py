"""The pairing bundle's node key comes from the journal, not the key listing.

``tailcat genkey --list`` prints key *names* and nothing else, so the provider
can never report an address or a node key from it. Both values are public, both
are printed once by ``genkey`` at generation time, and
``KeyGenerationStep`` copies them into the journal record for exactly this
reason -- the private half of the keypair is never opened to recover them.

Before this, ``install --client`` ended by asking the provider, getting
``None``, and raising a bare ``RuntimeError`` through a handler that only
catches :class:`DevtunnelError` -- a traceback on a successful install, over a
value already sitting in the journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from devtunnel.cli.app import _cached_public_value, _resolve_address, _resolve_node_key
from devtunnel.domain.errors import DevtunnelError
from devtunnel.domain.journal import ChangeKind, ChangeRecord, RecordStatus

NODE_KEY = "nodekey:" + "9971d3f9a6aab1ca6eafcad3f67ce6eee36ea0849cb795c2c65d32589f8c4d20"
OTHER_NODE_KEY = "nodekey:" + "1111111111111111111111111111111111111111111111111111111111111111"
ADDRESS = "tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu"


def key_record(name: str, *, details=None, prior_state=None) -> ChangeRecord:
    return ChangeRecord(
        id=f"id-{name}-{len(details or {})}-{len(prior_state or {})}",
        kind=ChangeKind.KEY_GENERATED,
        target=f"tailcatkey:{name}",
        status=RecordStatus.APPLIED,
        prior_state=prior_state or {},
        details=details or {},
    )


@dataclass
class FakeJournal:
    records: list[ChangeRecord] = field(default_factory=list)

    def load(self) -> list[ChangeRecord]:
        return list(self.records)


@dataclass
class FakeProvider:
    """Stands in for the real adapter, whose listing carries only names."""

    node_key_value: str | None = None
    address_value: str | None = None
    calls: list[str] = field(default_factory=list)

    def node_key(self, name: str) -> str:
        self.calls.append(name)
        if self.node_key_value is None:
            raise RuntimeError(f"tailcat reported no node key for {name!r}")
        return self.node_key_value

    def address_for(self, name: str) -> str | None:
        self.calls.append(name)
        return self.address_value


@dataclass
class FakeCtx:
    journal: FakeJournal
    tunnel_provider: FakeProvider


def ctx_with(records, **provider):
    return FakeCtx(FakeJournal(records), FakeProvider(**provider))


def test_node_key_is_recovered_from_the_journal_when_the_listing_cannot_supply_it():
    ctx = ctx_with([key_record("client-default", details={"node_key": NODE_KEY})])

    assert _resolve_node_key(ctx, "client-default") == NODE_KEY


def test_the_journal_is_consulted_before_the_provider():
    """Not a rescue path: asking the provider first fails on a healthy install."""

    ctx = ctx_with(
        [key_record("client-default", details={"node_key": NODE_KEY})],
        node_key_value=OTHER_NODE_KEY,
    )

    assert _resolve_node_key(ctx, "client-default") == NODE_KEY
    assert ctx.tunnel_provider.calls == []


def test_prior_state_carries_the_node_key_when_details_does_not():
    ctx = ctx_with([key_record("client-default", prior_state={"node_key": NODE_KEY})])

    assert _resolve_node_key(ctx, "client-default") == NODE_KEY


def test_the_newest_record_for_the_key_wins():
    """A regenerated key must not resolve to the superseded one's node key."""

    ctx = ctx_with(
        [
            key_record("client-default", details={"node_key": OTHER_NODE_KEY}),
            key_record("client-default", details={"node_key": NODE_KEY}),
        ]
    )

    assert _resolve_node_key(ctx, "client-default") == NODE_KEY


def test_a_record_for_a_different_key_is_never_used():
    ctx = ctx_with([key_record("default", details={"node_key": OTHER_NODE_KEY})])

    with pytest.raises(DevtunnelError):
        _resolve_node_key(ctx, "client-default")


def test_a_non_key_record_is_ignored():
    ctx = ctx_with(
        [
            ChangeRecord(
                id="bin",
                kind=ChangeKind.BINARY_INSTALLED,
                target="tailcatkey:client-default",
                status=RecordStatus.APPLIED,
                details={"node_key": OTHER_NODE_KEY},
            )
        ]
    )

    with pytest.raises(DevtunnelError):
        _resolve_node_key(ctx, "client-default")


def test_the_provider_still_answers_when_the_journal_has_nothing():
    """A key generated outside the journal, or a richer future listing."""

    ctx = ctx_with([], node_key_value=NODE_KEY)

    assert _resolve_node_key(ctx, "client-default") == NODE_KEY


def test_a_genuine_miss_is_a_devtunnel_error_not_a_runtime_error():
    """``_print_pairing_bundle`` catches DevtunnelError; a RuntimeError escaped
    it as a traceback."""

    ctx = ctx_with([])

    with pytest.raises(DevtunnelError):
        _resolve_node_key(ctx, "client-default")


def test_the_address_is_recovered_from_the_journal_too():
    ctx = ctx_with([key_record("default", details={"address": ADDRESS})])

    assert _cached_public_value(ctx, "default", "address") == ADDRESS


def test_a_missing_field_falls_through_rather_than_returning_empty():
    """An older record with no cached node key must not shadow the provider."""

    ctx = ctx_with(
        [key_record("client-default", details={"address": ADDRESS})],
        node_key_value=NODE_KEY,
    )

    assert _resolve_node_key(ctx, "client-default") == NODE_KEY


def test_the_address_resolver_prefers_the_journal_over_the_provider():
    """``pair add`` prints "Send them back: devtunnel connect <address>" at the
    end. Asking the provider for that address shells out to the tailcat binary
    by the path the *current* context resolved -- user scope, even on a host
    installed with ``--system`` -- and the listing could not supply the value
    anyway. The journal has it, so the subprocess never needs to run."""

    ctx = ctx_with(
        [key_record("default", details={"address": ADDRESS})],
        address_value="tcSOMETHINGELSE0000000000000000000000",
    )

    assert _resolve_address(ctx, "default") == ADDRESS
    assert ctx.tunnel_provider.calls == []


def test_the_address_resolver_falls_back_to_the_provider():
    ctx = ctx_with([], address_value=ADDRESS)

    assert _resolve_address(ctx, "default") == ADDRESS


def test_the_address_resolver_answers_none_rather_than_raising():
    """A host with neither a cached nor a listed address still finishes the
    command; the caller simply omits the "send them back" line."""

    ctx = ctx_with([])

    assert _resolve_address(ctx, "default") is None
