"""Which tailcat binary a command resolves, given what is actually installed.

``--system`` is an install-time flag; ``pair add``, ``up`` and ``connect`` take
no scope at all and so resolve user scope. On a machine-wide install that left
them invoking a path nothing had written -- a ``FileNotFoundError`` out of
Popen, which ``check=False`` does not catch.
"""

from __future__ import annotations

import os

import pytest

from devtunnel.application.paths import resolve_paths
from devtunnel.domain.models import PlatformId, Scope


class FakeFileSystem:
    def __init__(self, home: str, present: set[str] | None = None) -> None:
        self.home = home
        self.present = present or set()

    def real_user_home(self) -> str:
        return self.home

    def exists(self, path: str) -> bool:
        return path in self.present


@pytest.fixture
def home(tmp_path):
    return str(tmp_path)


def user_binary(home: str, platform: PlatformId) -> str:
    if platform is PlatformId.WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.join(home, "AppData", "Local")
        return os.path.join(base, "devtunnel", "bin", "tailcat.exe")
    return os.path.join(home, ".local", "share", "devtunnel", "bin", "tailcat")


def machine_binary(platform: PlatformId) -> str:
    if platform is PlatformId.WINDOWS:
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        return os.path.join(program_files, "devtunnel", "tailcat.exe")
    return "/usr/local/bin/tailcat"


PLATFORMS = (PlatformId.WINDOWS, PlatformId.DEBIAN)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_user_scope_resolves_the_user_binary_when_it_exists(home, platform):
    expected = user_binary(home, platform)
    filesystem = FakeFileSystem(home, {expected, machine_binary(platform)})

    paths = resolve_paths(platform, filesystem, scope=Scope.USER)

    assert paths.binary_path == expected


@pytest.mark.parametrize("platform", PLATFORMS)
def test_user_scope_falls_back_to_a_system_install(home, platform):
    """What ``scripts/install-on-azure-vm.ps1`` leaves behind: the binary is in
    the machine location and no user-scope copy was ever written."""

    filesystem = FakeFileSystem(home, {machine_binary(platform)})

    paths = resolve_paths(platform, filesystem, scope=Scope.USER)

    assert paths.binary_path == machine_binary(platform)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_nothing_installed_still_names_the_user_path(home, platform):
    """So a "not installed" message names the file a user-scope run would make."""

    paths = resolve_paths(platform, FakeFileSystem(home), scope=Scope.USER)

    assert paths.binary_path == user_binary(home, platform)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_system_scope_never_borrows_the_user_binary(home, platform):
    filesystem = FakeFileSystem(home, {user_binary(home, platform)})

    paths = resolve_paths(platform, filesystem, scope=Scope.MACHINE)

    assert paths.binary_path == machine_binary(platform)


@pytest.mark.parametrize("platform", PLATFORMS)
def test_only_the_binary_is_probed_not_the_state_directories(home, platform):
    """State directories are where devtunnel *writes*; a write belongs to its
    scope whatever happens to exist."""

    filesystem = FakeFileSystem(home, {machine_binary(platform)})

    user = resolve_paths(platform, filesystem, scope=Scope.USER)
    bare = resolve_paths(platform, FakeFileSystem(home), scope=Scope.USER)

    assert user.user_state_dir == bare.user_state_dir
    assert user.machine_state_dir == bare.machine_state_dir
