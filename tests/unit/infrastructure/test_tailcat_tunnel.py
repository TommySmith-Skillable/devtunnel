"""Unit tests for :mod:`devtunnel.infrastructure.tailcat.tailcat_tunnel`.

``build_argv`` is the single source of truth for the command line of a pre-1.0
dependency, so it gets one assertion per meaningful ``TunnelSpec`` permutation:
when upstream moves a flag, these are the tests that say exactly what changed.

The scripted ``ManagedProcess`` and key stub live here rather than in
``tests/fakes/fake_ports.py`` because they exist to drive *this* adapter's
timeout and early-exit paths.
"""

from __future__ import annotations

import pytest

from devtunnel.domain.models import TunnelSpec
from devtunnel.infrastructure.tailcat.tailcat_tunnel import TailcatTunnelProvider

BINARY = "/opt/devtunnel/bin/tailcat"
SAVED_ADDRESS = "tcQ3mKpL8vZr2sNf5tYbW9hJdX4cG7aE1uR6iO0pS"
NEW_ADDRESS = "tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu"

SAVED_KEY_BANNER = f'\U0001f408 Server listening with saved key "default": {SAVED_ADDRESS}'
NEW_ADDRESS_BANNER = f"\U0001f408 Server listening with new address: {NEW_ADDRESS}"


class ScriptedProcess:
    """A ``ManagedProcess`` whose stderr is a list and whose exit code is a dial."""

    def __init__(self, lines=(), exit_code=None) -> None:
        self._lines = list(lines)
        self._exit_code = exit_code
        self.stderr_reads = 0
        self.terminated = False
        self.waited = False

    @property
    def pid(self) -> int:
        return 4321

    def poll(self):
        return self._exit_code

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout=None) -> int:
        self.waited = True
        return 0

    def read_stderr_line(self, timeout=None):
        self.stderr_reads += 1
        if self._lines:
            return self._lines.pop(0)
        return None


class SpawningRunner:
    def __init__(self, process=None) -> None:
        self.process = process or ScriptedProcess()
        self.spawned: list[list[str]] = []

    def run(self, argv, *, check=True, input=None, env=None, timeout=None):
        raise AssertionError("the tunnel adapter delegates every one-shot command to the keys")

    def spawn(self, argv, *, env=None):
        self.spawned.append(list(argv))
        return self.process

    def which(self, executable):
        return f"/usr/bin/{executable}"


class StubKeys:
    def __init__(self, addresses=None, node_keys=None) -> None:
        self.addresses = addresses or {}
        self.node_keys = node_keys or {}
        self.deleted: list[str] = []
        self.generated: list[tuple] = []

    def has_key(self, name):
        return name in self.addresses

    def address_for(self, name):
        return self.addresses.get(name)

    def node_key(self, name):
        return self.node_keys.get(name)

    def generate(self, name, *, client=False, region=None, fixed_region=False):
        self.generated.append((name, client, region, fixed_region))
        return {"name": name, "existed": False, "client": client}

    def delete(self, name):
        self.deleted.append(name)


def make_provider(process=None, addresses=None, node_keys=None):
    runner = SpawningRunner(process)
    keys = StubKeys(addresses, node_keys)
    return TailcatTunnelProvider(runner, keys, binary_path=BINARY), runner, keys


# -- argv ------------------------------------------------------------------


def test_argv_for_the_default_ssh_spec():
    provider, _, _ = make_provider()

    assert provider.build_argv(TunnelSpec()) == [BINARY, "serve", "--key=default", "ssh"]


def test_argv_for_an_ephemeral_key_asks_tailcat_for_a_new_one():
    provider, _, _ = make_provider()

    argv = provider.build_argv(TunnelSpec(key_name=None))

    assert argv == [BINARY, "serve", "--key=new", "ssh"]


def test_argv_joins_multiple_allowed_node_keys_with_commas():
    provider, _, _ = make_provider()

    argv = provider.build_argv(TunnelSpec(allow=("nodekey:aaaa", "nodekey:bbbb")))

    assert argv == [
        BINARY,
        "serve",
        "--key=default",
        "--allow=nodekey:aaaa,nodekey:bbbb",
        "ssh",
    ]


def test_argv_joins_multiple_authorized_key_sources_with_commas():
    provider, _, _ = make_provider()

    argv = provider.build_argv(
        TunnelSpec(authorized_keys=("/home/tommy/.ssh/authorized_keys", "tommy@github"))
    )

    assert argv == [
        BINARY,
        "serve",
        "--key=default",
        "--ssh-authorized-keys=/home/tommy/.ssh/authorized_keys,tommy@github",
        "ssh",
    ]


def test_argv_omits_ssh_authorized_keys_when_forwarding_ports_instead_of_ssh():
    provider, _, _ = make_provider()

    argv = provider.build_argv(
        TunnelSpec(serve=("8080,8443",), authorized_keys=("~/.ssh/authorized_keys",))
    )

    assert argv == [BINARY, "serve", "--key=default", "8080,8443"]


def test_argv_omits_ssh_authorized_keys_for_no_auth_ssh():
    provider, _, _ = make_provider()

    argv = provider.build_argv(
        TunnelSpec(serve=("no-auth-ssh",), authorized_keys=("~/.ssh/authorized_keys",))
    )

    assert argv == [BINARY, "serve", "--key=default", "no-auth-ssh"]


def test_argv_carries_the_region():
    provider, _, _ = make_provider()

    argv = provider.build_argv(TunnelSpec(region="eu-west"))

    assert argv == [BINARY, "serve", "--key=default", "--region=eu-west", "ssh"]


def test_argv_carries_the_bind_address():
    provider, _, _ = make_provider()

    argv = provider.build_argv(TunnelSpec(bind="127.0.0.1:2222"))

    assert argv == [BINARY, "serve", "--key=default", "--bind=127.0.0.1:2222", "ssh"]


def test_argv_puts_a_forced_command_last_behind_a_double_dash():
    provider, _, _ = make_provider()

    argv = provider.build_argv(TunnelSpec(forced_command="/usr/bin/true"))

    assert argv == [BINARY, "serve", "--key=default", "ssh", "--", "/usr/bin/true"]


def test_argv_emits_every_flag_in_the_documented_order():
    provider, _, _ = make_provider()

    argv = provider.build_argv(
        TunnelSpec(
            serve=("ssh",),
            key_name="work",
            allow=("nodekey:aaaa",),
            authorized_keys=("~/.ssh/authorized_keys",),
            region="eu-west",
            bind="0.0.0.0:0",
            forced_command="htop",
        )
    )

    assert argv == [
        BINARY,
        "serve",
        "--key=work",
        "--allow=nodekey:aaaa",
        "--ssh-authorized-keys=~/.ssh/authorized_keys",
        "--region=eu-west",
        "--bind=0.0.0.0:0",
        "ssh",
        "--",
        "htop",
    ]


# -- start -----------------------------------------------------------------


def test_a_saved_key_address_is_used_without_reading_a_single_stderr_line():
    process = ScriptedProcess(lines=[NEW_ADDRESS_BANNER])
    provider, runner, _ = make_provider(process, addresses={"default": SAVED_ADDRESS})

    handle = provider.start(TunnelSpec())

    assert handle.address == SAVED_ADDRESS
    assert process.stderr_reads == 0, "the persistent path must not depend on banner wording"
    assert runner.spawned == [[BINARY, "serve", "--key=default", "ssh"]]


def test_a_saved_key_address_renders_the_connect_command():
    provider, _, _ = make_provider(addresses={"default": SAVED_ADDRESS})

    handle = provider.start(TunnelSpec())

    assert handle.connect_command == f"tailcat ssh {SAVED_ADDRESS}"


def test_an_ephemeral_key_parses_the_new_address_banner():
    process = ScriptedProcess(lines=["\U0001f408 starting up", NEW_ADDRESS_BANNER])
    provider, _, _ = make_provider(process)

    handle = provider.start(TunnelSpec(key_name=None), timeout=5.0)

    assert handle.address == NEW_ADDRESS


def test_an_ephemeral_key_parses_the_saved_key_banner_wording_too():
    process = ScriptedProcess(lines=[SAVED_KEY_BANNER])
    provider, _, _ = make_provider(process)

    handle = provider.start(TunnelSpec(key_name=None), timeout=5.0)

    assert handle.address == SAVED_ADDRESS


def test_a_named_key_with_no_saved_address_falls_back_to_the_banner():
    process = ScriptedProcess(lines=[SAVED_KEY_BANNER])
    provider, _, _ = make_provider(process, addresses={})

    handle = provider.start(TunnelSpec(key_name="default"), timeout=5.0)

    assert handle.address == SAVED_ADDRESS
    assert process.stderr_reads == 1


def test_an_address_on_a_line_that_is_not_the_listening_banner_is_ignored():
    process = ScriptedProcess(lines=[f"peer {NEW_ADDRESS} rejected", SAVED_KEY_BANNER])
    provider, _, _ = make_provider(process)

    handle = provider.start(TunnelSpec(key_name=None), timeout=5.0)

    assert handle.address == SAVED_ADDRESS


def test_an_early_exit_surfaces_tailcats_own_stderr_in_the_error():
    process = ScriptedProcess(
        lines=["\U0001f408 tailcat: bind: address already in use"], exit_code=1
    )
    provider, _, _ = make_provider(process)

    with pytest.raises(RuntimeError) as excinfo:
        provider.start(TunnelSpec(key_name=None), timeout=5.0)

    message = str(excinfo.value)
    assert "exited early with code 1" in message
    assert "address already in use" in message


def test_an_early_exit_with_no_output_says_so_rather_than_trailing_off():
    provider, _, _ = make_provider(ScriptedProcess(exit_code=2))

    with pytest.raises(RuntimeError) as excinfo:
        provider.start(TunnelSpec(key_name=None), timeout=5.0)

    assert "wrote nothing to stderr" in str(excinfo.value)


def test_a_timeout_terminates_the_process_and_attaches_the_stderr_it_saw():
    process = ScriptedProcess(lines=["\U0001f408 still negotiating", "\U0001f408 no peers yet"])
    provider, _, _ = make_provider(process)

    with pytest.raises(TimeoutError) as excinfo:
        provider.start(TunnelSpec(key_name=None), timeout=0.05)

    assert process.terminated is True
    assert "no peers yet" in str(excinfo.value)


# -- stop ------------------------------------------------------------------


def test_stop_terminates_then_waits():
    process = ScriptedProcess()
    provider, _, _ = make_provider(process, addresses={"default": SAVED_ADDRESS})
    handle = provider.start(TunnelSpec())

    provider.stop(handle)

    assert process.terminated is True
    assert process.waited is True


def test_stop_swallows_a_failure_to_reap_the_child():
    class UnreapableProcess(ScriptedProcess):
        def wait(self, timeout=None):
            raise OSError("no such process")

    process = UnreapableProcess()
    provider, _, _ = make_provider(process, addresses={"default": SAVED_ADDRESS})
    handle = provider.start(TunnelSpec())

    provider.stop(handle)

    assert process.terminated is True


# -- the key half of the port ----------------------------------------------


def test_generate_key_delegates_to_the_key_adapter():
    provider, _, keys = make_provider()

    provider.generate_key("client-default", client=True, region="eu", fixed_region=True)

    assert keys.generated == [("client-default", True, "eu", True)]


def test_remove_key_deletes_the_key_generate_key_created():
    provider, _, keys = make_provider()

    provider.remove_key({"name": "default", "existed": False})

    assert keys.deleted == ["default"]


def test_remove_key_leaves_a_key_that_was_already_there_alone():
    provider, _, keys = make_provider()

    provider.remove_key({"name": "default", "existed": True})

    assert keys.deleted == []


def test_node_key_fails_by_name_when_no_key_has_been_generated():
    provider, _, _ = make_provider()

    with pytest.raises(RuntimeError, match="no node key"):
        provider.node_key("client-default")


def test_address_for_and_has_key_delegate_to_the_key_adapter():
    provider, _, _ = make_provider(addresses={"default": SAVED_ADDRESS})

    assert provider.address_for("default") == SAVED_ADDRESS
    assert provider.has_key("default") is True
    assert provider.has_key("other") is False
