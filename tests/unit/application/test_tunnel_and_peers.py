"""The ``--open`` gate and peer management.

Both are places where the safe behaviour has to be the one you get by
default, because the unsafe one is indistinguishable from it until someone
connects.
"""

import pytest

from devtunnel.application.allowlist import Allowlist
from devtunnel.application.use_cases.manage_peers import ManagePeersUseCase, UnknownPeerError
from devtunnel.application.use_cases.start_tunnel import (
    OpenTunnelRefusedError,
    StartTunnelUseCase,
)
from devtunnel.domain.errors import DevtunnelError
from devtunnel.domain.models import TunnelSpec
from tests.fakes.context_builder import make_context
from tests.unit.application.conftest import make_bundle

# -- the --open gate (plan section 13.2) -----------------------------------


def test_an_empty_allowlist_is_refused_rather_than_silently_opening_the_tunnel():
    ctx = make_context()

    with pytest.raises(OpenTunnelRefusedError, match="no peers are allow-listed"):
        StartTunnelUseCase(ctx).resolve_spec(TunnelSpec())


def test_an_empty_allowlist_is_allowed_when_open_is_passed_explicitly():
    ctx = make_context()

    spec = StartTunnelUseCase(ctx).resolve_spec(TunnelSpec(), open_tunnel=True)

    assert spec.is_open


def test_no_auth_ssh_requires_open_even_when_peers_are_allow_listed():
    # Two innocuous-looking flags must not combine into a shell for anyone.
    ctx = make_context()
    Allowlist(ctx.filesystem, ctx.paths.allowlist_path).add("nodekey:" + "ab" * 32)

    with pytest.raises(OpenTunnelRefusedError, match="no-auth-ssh"):
        StartTunnelUseCase(ctx).resolve_spec(TunnelSpec(serve=("no-auth-ssh",)))


def test_the_saved_allowlist_is_used_when_the_caller_supplies_none():
    ctx = make_context()
    node_key = "nodekey:" + "cd" * 32
    Allowlist(ctx.filesystem, ctx.paths.allowlist_path).add(node_key)

    spec = StartTunnelUseCase(ctx).resolve_spec(TunnelSpec())

    assert spec.allow == (node_key,)
    assert not spec.is_open


def test_an_explicit_allowlist_takes_precedence_over_the_saved_one():
    ctx = make_context()
    Allowlist(ctx.filesystem, ctx.paths.allowlist_path).add("nodekey:" + "ab" * 32)
    explicit = ("nodekey:" + "ef" * 32,)

    spec = StartTunnelUseCase(ctx).resolve_spec(TunnelSpec(allow=explicit))

    assert spec.allow == explicit


def test_starting_without_a_generated_key_fails_with_a_useful_message():
    ctx = make_context()

    with pytest.raises(DevtunnelError, match="run 'devtunnel install' first"):
        StartTunnelUseCase(ctx).start(TunnelSpec())


# -- peers -----------------------------------------------------------------


def test_add_then_list_reports_the_peer_from_the_journal():
    ctx = make_context()
    bundle = make_bundle()

    ManagePeersUseCase(ctx).add(bundle.encode())
    peers = ManagePeersUseCase(ctx).list_peers()

    assert [p.name for p in peers] == ["tester@laptop"]
    assert peers[0].fingerprint == bundle.fingerprint


def test_a_malformed_bundle_is_rejected_before_anything_is_touched():
    ctx = make_context()

    with pytest.raises(DevtunnelError):
        ManagePeersUseCase(ctx).add("dtp1:not-valid")

    assert ctx.journal.load() == []
    assert not ctx.filesystem.exists(ctx.paths.authorized_keys_path)


def test_remove_by_name_revokes_both_halves():
    ctx = make_context()
    bundle = make_bundle()
    use_case = ManagePeersUseCase(ctx)
    use_case.add(bundle.encode())

    use_case.remove("tester@laptop")

    assert not Allowlist(ctx.filesystem, ctx.paths.allowlist_path).contains(bundle.nodekey)
    assert use_case.list_peers() == []


def test_remove_by_bare_node_key_works_too():
    ctx = make_context()
    bundle = make_bundle()
    use_case = ManagePeersUseCase(ctx)
    use_case.add(bundle.encode())

    use_case.remove(bundle.nodekey.removeprefix("nodekey:"))

    assert use_case.list_peers() == []


def test_removing_an_unknown_peer_fails_by_name():
    ctx = make_context()

    with pytest.raises(UnknownPeerError, match="nobody@nowhere"):
        ManagePeersUseCase(ctx).remove("nobody@nowhere")


def test_a_revoked_peer_no_longer_appears_in_the_list():
    ctx = make_context()
    use_case = ManagePeersUseCase(ctx)
    use_case.add(make_bundle("a@x", seed="ab").encode())
    use_case.add(make_bundle("b@y", seed="cd").encode())

    use_case.remove("a@x")

    assert [p.name for p in use_case.list_peers()] == ["b@y"]


def test_github_sources_reach_the_serve_flags_rather_than_being_journaled_and_forgotten():
    # tailcat fetches github.com/<user>.keys itself, so the entry must survive
    # all the way to the serve argv -- being recorded is not the same as
    # being used.
    from devtunnel.application.steps import EnsureAuthorizedKeysStep

    ctx = make_context()
    EnsureAuthorizedKeysStep("auth", "Authorize", ("tommy@github",)).apply(ctx)
    Allowlist(ctx.filesystem, ctx.paths.allowlist_path).add("nodekey:" + "ab" * 32)

    spec = StartTunnelUseCase(ctx).resolve_spec(TunnelSpec())

    assert "tommy@github" in spec.authorized_keys


def test_a_revoked_github_source_no_longer_reaches_the_serve_flags():
    from devtunnel.application.steps import EnsureAuthorizedKeysStep

    ctx = make_context()
    step = EnsureAuthorizedKeysStep("auth", "Authorize", ("tommy@github",))
    outcome = step.apply(ctx)
    Allowlist(ctx.filesystem, ctx.paths.allowlist_path).add("nodekey:" + "ab" * 32)
    step.revert(ctx, outcome.record)

    spec = StartTunnelUseCase(ctx).resolve_spec(TunnelSpec())

    assert "tommy@github" not in spec.authorized_keys


def test_a_bare_allow_entry_is_journaled_and_reverted_like_everything_else():
    # An allowlist entry devtunnel added but cannot name is one uninstall
    # would leave behind.
    from devtunnel.application.plan_builder import InstallSettings, PlanBuilder
    from devtunnel.application.use_cases.install_environment import InstallEnvironmentUseCase
    from devtunnel.application.use_cases.uninstall_environment import (
        UninstallEnvironmentUseCase,
    )
    from tests.fakes.fake_ports import FakeProcessRunner

    node_key = "nodekey:" + "ab" * 32
    ctx = make_context(process=FakeProcessRunner(missing=("tailcat",)))
    settings = InstallSettings(authorized_keys=("ssh-ed25519 AAAAt u@h",), allow=(node_key,))

    InstallEnvironmentUseCase(ctx, PlanBuilder(ctx.toolkit, ctx.paths, ctx.process)).execute(
        settings
    )
    allowlist = Allowlist(ctx.filesystem, ctx.paths.allowlist_path)
    assert allowlist.contains(node_key)
    assert any(r.target == f"allow:{node_key}" for r in ctx.journal.load())

    UninstallEnvironmentUseCase(ctx).execute()

    assert not allowlist.contains(node_key)
