"""Unit tests for :mod:`devtunnel.infrastructure.tailcat.tailcat_keys`.

No subprocess, no network, and no filesystem outside ``tmp_path``. The fake
filesystem here deliberately has **no** ``read_text``: if the adapter ever grows
code that opens ``<name>.private.json``, these tests fail with an
``AttributeError`` instead of quietly starting to handle private key material.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field

import pytest

from devtunnel.application.ports.process_runner import CompletedProcess
from devtunnel.infrastructure.tailcat.tailcat_keys import TailcatKeys

SERVER_ADDRESS = "tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu"
NODE_KEY = "nodekey:" + "cfb6bfa77a0654d7450947fd6acef17d2cd848da1d30b2540b13dac272ddfd16"


def config_home(root):
    """Where Go's ``os.UserConfigDir`` -- and so tailcat -- puts its keys.

    Spelled out per platform rather than reusing the adapter's own
    ``_config_home`` so that these tests still fail if that method starts
    answering something else; a test that computes the expected value the same
    way the code does cannot catch the code being wrong.
    """

    if os.name == "nt":
        return root / "AppData" / "Roaming"
    if sys.platform == "darwin":
        return root / "Library" / "Application Support"
    return root / ".config"



@dataclass
class Invocation:
    argv: list[str]
    env: dict[str, str] | None


@dataclass
class RecordingProcessRunner:
    stdout: str = ""
    listing: str = ""
    parse_output: str = "{}"
    returncode: int = 0
    raises: Exception | None = None
    invocations: list[Invocation] = field(default_factory=list)

    def run(self, argv, *, check=True, input=None, env=None, timeout=None):
        self.invocations.append(Invocation(list(argv), dict(env) if env is not None else None))
        if self.raises is not None:
            raise self.raises
        return CompletedProcess(tuple(argv), self.returncode, self._stdout_for(argv), "")

    def spawn(self, argv, *, env=None):
        raise AssertionError("key commands never spawn a long-running process")

    def which(self, executable):
        return f"/usr/bin/{executable}"

    def _stdout_for(self, argv) -> str:
        if "--list" in argv:
            return self.listing
        if "parse" in argv:
            return self.parse_output
        return self.stdout

    @property
    def last(self) -> Invocation:
        return self.invocations[-1]


class RecordingFileSystem:
    """Just enough of ``FileSystemPort`` for the key adapter -- and no more.

    ``read_text`` is omitted on purpose; see this module's docstring.
    """

    def __init__(self, home: str) -> None:
        self.home = home
        self.owned: list[str] = []

    def real_user_home(self) -> str:
        return self.home

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def take_ownership_for_real_user(self, path: str) -> None:
        self.owned.append(path)


def make_keys(tmp_path, **runner_kwargs):
    runner = RecordingProcessRunner(**runner_kwargs)
    filesystem = RecordingFileSystem(str(tmp_path))
    keys = TailcatKeys(runner, filesystem, binary_path="/opt/devtunnel/bin/tailcat")
    return keys, runner, filesystem


# -- argv ------------------------------------------------------------------


def test_generate_builds_the_server_genkey_argv(tmp_path):
    keys, runner, _ = make_keys(tmp_path)

    keys.generate("default")

    assert runner.last.argv == ["/opt/devtunnel/bin/tailcat", "genkey", "--key=default"]


def test_generate_builds_the_client_genkey_argv(tmp_path):
    keys, runner, _ = make_keys(tmp_path)

    keys.generate("client-default", client=True)

    assert runner.last.argv == [
        "/opt/devtunnel/bin/tailcat",
        "genkey",
        "--client",
        "--key=client-default",
    ]


def test_generate_passes_the_region_flag(tmp_path):
    keys, runner, _ = make_keys(tmp_path)

    keys.generate("default", region="eu-west")

    assert runner.last.argv == [
        "/opt/devtunnel/bin/tailcat",
        "genkey",
        "--key=default",
        "--region=eu-west",
    ]


def test_generate_passes_the_fixed_region_flag_after_the_region(tmp_path):
    keys, runner, _ = make_keys(tmp_path)

    keys.generate("default", region="eu-west", fixed_region=True)

    assert runner.last.argv == [
        "/opt/devtunnel/bin/tailcat",
        "genkey",
        "--key=default",
        "--region=eu-west",
        "--fixed-region",
    ]


def test_delete_builds_the_genkey_delete_argv(tmp_path):
    keys, runner, _ = make_keys(tmp_path)

    keys.delete("default")

    assert runner.last.argv == [
        "/opt/devtunnel/bin/tailcat",
        "genkey",
        "--delete",
        "--key=default",
    ]


def test_the_injected_binary_path_is_used_rather_than_the_name_on_path(tmp_path):
    keys, runner, _ = make_keys(tmp_path)

    keys.generate("default")

    assert runner.last.argv[0] == "/opt/devtunnel/bin/tailcat"


# -- captured output -------------------------------------------------------


def test_generate_captures_the_address_printed_on_stdout(tmp_path):
    keys, _, _ = make_keys(tmp_path, stdout=f"Created key \"default\"\nAddress: {SERVER_ADDRESS}\n")

    details = keys.generate("default")

    assert details["address"] == SERVER_ADDRESS
    assert details["node_key"] is None


def test_generate_captures_the_node_key_printed_on_stdout(tmp_path):
    keys, _, _ = make_keys(tmp_path, stdout=f"Created client key\n{NODE_KEY}\n")

    details = keys.generate("client-default", client=True)

    assert details["node_key"] == NODE_KEY
    assert details["client"] is True


def test_generate_captures_both_an_address_and_a_node_key_when_both_are_printed(tmp_path):
    stdout = f"Address: {SERVER_ADDRESS}\nNode key: {NODE_KEY}\n"
    keys, _, _ = make_keys(tmp_path, stdout=stdout)

    details = keys.generate("default")

    assert details["address"] == SERVER_ADDRESS
    assert details["node_key"] == NODE_KEY


def test_generate_records_an_existing_key_as_preexisting(tmp_path):
    keys, _, _ = make_keys(tmp_path)
    key_path = config_home(tmp_path) / "tailcat" / "keys" / "default.private.json"
    key_path.parent.mkdir(parents=True)
    key_path.write_text("{}", encoding="utf-8")

    details = keys.generate("default")

    assert details["existed"] is True
    assert details["key_path"] == str(key_path)


def test_generate_records_a_fresh_key_as_not_preexisting(tmp_path):
    keys, _, _ = make_keys(tmp_path)

    assert keys.generate("default")["existed"] is False


def test_no_private_key_material_reaches_the_returned_details(tmp_path):
    secret = "mPRIVATEkeyMATERIALmustNEVERbeJOURNALED="
    stdout = f'{{"PrivateKey": "{secret}"}}\nAddress: {SERVER_ADDRESS}\n'
    keys, _, _ = make_keys(tmp_path, stdout=stdout)

    details = keys.generate("default")

    assert secret not in repr(details)
    assert details["address"] == SERVER_ADDRESS


def test_has_key_tests_existence_without_opening_the_key_file(tmp_path):
    keys, _, _ = make_keys(tmp_path)
    key_path = config_home(tmp_path) / "tailcat" / "keys" / "default.private.json"
    key_path.parent.mkdir(parents=True)

    assert keys.has_key("default") is False

    key_path.write_text("{}", encoding="utf-8")

    assert keys.has_key("default") is True


# -- elevation -------------------------------------------------------------


def test_genkey_runs_with_home_pointed_at_the_real_user_home(tmp_path):
    """Under sudo, ``$HOME`` is root's -- the key must not land there."""

    real_user_home = tmp_path / "home" / "tommy"
    runner = RecordingProcessRunner()
    filesystem = RecordingFileSystem(str(real_user_home))
    keys = TailcatKeys(runner, filesystem)

    keys.generate("default")

    assert runner.last.env["HOME"] == str(real_user_home)
    assert runner.last.env["USERPROFILE"] == str(real_user_home)


def test_delete_and_list_also_run_with_the_redirected_home(tmp_path):
    real_user_home = tmp_path / "home" / "tommy"
    runner = RecordingProcessRunner()
    keys = TailcatKeys(runner, RecordingFileSystem(str(real_user_home)))

    keys.delete("default")
    keys.address_for("default")

    assert [i.env["HOME"] for i in runner.invocations] == [str(real_user_home)] * 2


def test_generate_hands_the_key_directory_back_to_the_real_user(tmp_path):
    keys, _, filesystem = make_keys(tmp_path)

    keys.generate("default")

    assert filesystem.owned == [str(config_home(tmp_path) / "tailcat" / "keys")]


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode bits do not exist on Windows")
def test_generate_tightens_the_key_file_and_directory_permissions(tmp_path):
    keys, _, _ = make_keys(tmp_path)
    keys_dir = config_home(tmp_path) / "tailcat" / "keys"
    keys_dir.mkdir(parents=True)
    key_file = keys_dir / "default.private.json"
    key_file.write_text("{}", encoding="utf-8")
    os.chmod(keys_dir, 0o755)
    os.chmod(key_file, 0o644)

    keys.generate("default")

    assert oct(key_file.stat().st_mode & 0o777) == oct(0o600)
    assert oct(keys_dir.stat().st_mode & 0o777) == oct(0o700)


# -- reading public values -------------------------------------------------


def test_address_for_reads_the_address_out_of_the_genkey_listing(tmp_path):
    listing = f"default\t{SERVER_ADDRESS}\n"
    keys, runner, _ = make_keys(tmp_path, listing=listing)

    assert keys.address_for("default") == SERVER_ADDRESS
    assert runner.last.argv == ["/opt/devtunnel/bin/tailcat", "genkey", "--list"]


def test_address_for_does_not_confuse_a_key_with_a_similarly_named_one(tmp_path):
    other = "tcZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    listing = f'client-default\t{other}\n"default"\t{SERVER_ADDRESS}\n'
    keys, _, _ = make_keys(tmp_path, listing=listing)

    assert keys.address_for("default") == SERVER_ADDRESS
    assert keys.address_for("client-default") == other


def test_node_key_reads_the_node_key_out_of_the_genkey_listing(tmp_path):
    keys, _, _ = make_keys(tmp_path, listing=f"client-default\t{NODE_KEY}\n")

    assert keys.node_key("client-default") == NODE_KEY


def test_address_for_returns_none_when_the_listing_does_not_mention_the_key(tmp_path):
    keys, _, _ = make_keys(tmp_path, listing=f"default\t{SERVER_ADDRESS}\n")

    assert keys.address_for("missing") is None


def test_address_for_returns_none_when_the_listing_command_fails(tmp_path):
    keys, _, _ = make_keys(tmp_path, listing=f"default\t{SERVER_ADDRESS}\n", returncode=1)

    assert keys.address_for("default") is None


def test_address_for_returns_none_when_the_binary_is_not_installed(tmp_path):
    """Popen raises OSError for a binary that is not there, and ``check=False``
    does not cover it: a reader command resolves the user-scope path even on a
    ``--system`` install, so "not there" is an ordinary outcome. It used to
    surface as a traceback out of the tail of ``pair add``, after the peer had
    already been authorised."""

    keys, runner, _ = make_keys(tmp_path)
    runner.raises = FileNotFoundError(2, "The system cannot find the file specified")

    assert keys.address_for("default") is None
    assert keys.node_key("default") is None


def test_parse_address_decodes_the_json_tailcat_prints(tmp_path):
    keys, runner, _ = make_keys(tmp_path, parse_output='{"ServerPublic": "nodekey:abc"}')

    parsed = keys.parse_address(SERVER_ADDRESS)

    assert parsed == {"ServerPublic": "nodekey:abc"}
    assert runner.last.argv == ["/opt/devtunnel/bin/tailcat", "parse", SERVER_ADDRESS]


# --------------------------------------------------------------------------
# Regression: the key directory is platform-specific, not always ~/.config
# --------------------------------------------------------------------------


def test_keys_dir_follows_the_platform_config_directory(tmp_path):
    """``~/.config`` is the Linux answer only.

    tailcat resolves its key directory with Go's ``os.UserConfigDir``, which is
    ``%AppData%`` on Windows and ``~/Library/Application Support`` on macOS.
    Hardcoding ``.config`` put :meth:`has_key` on a path nothing ever wrote to
    on those platforms, so an install that had just generated a key still saw
    no key -- and the re-run of ``genkey`` that followed exits 1 rather than
    clobbering one without ``--force``.
    """

    real_user_home = tmp_path / "home" / "tommy"
    keys = TailcatKeys(RecordingProcessRunner(), RecordingFileSystem(str(real_user_home)))

    assert keys.keys_dir() == str(config_home(real_user_home) / "tailcat" / "keys")


def test_genkey_pins_the_config_home_as_well_as_the_home(tmp_path):
    """Pinning ``HOME`` alone leaves the key directory ambient.

    The process runner merges these over ``os.environ``, and Go consults
    ``%AppData%``/``$XDG_CONFIG_HOME`` *before* falling back to the home
    directory. Left unset, an inherited value would send tailcat somewhere
    :meth:`keys_dir` is not looking, and the two would disagree silently.
    """

    real_user_home = tmp_path / "home" / "tommy"
    runner = RecordingProcessRunner()
    keys = TailcatKeys(runner, RecordingFileSystem(str(real_user_home)))

    keys.generate("default")

    expected = str(config_home(real_user_home))
    assert runner.last.env["APPDATA"] == expected
    assert runner.last.env["XDG_CONFIG_HOME"] == expected
    assert keys.keys_dir().startswith(expected)
