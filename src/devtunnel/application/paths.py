"""Every filesystem location devtunnel owns, resolved once and injected.

Steps need to know where the tailcat binary goes, where the allowlist lives and
which ``authorized_keys`` is the authentication boundary -- but a step that
computes those itself becomes untestable without a real home directory, and
starts disagreeing with the next step that computes them slightly differently.
So they are resolved once in the composition root and handed down as data.

Two rules drive the layout:

* **Scope decides the root.** A ``USER`` install writes only under the user's
  own data directory and needs no elevation; ``--system`` moves the binary and
  the service to machine-owned paths and does. Nothing else about a step
  changes between the two.
* **Home is the *real* user's home.** Resolution goes through
  ``FileSystemPort.real_user_home()``, never ``os.path.expanduser``, because an
  elevated run (``--with-git``, ``--system``) makes ``~`` resolve to root's home
  and silently strands keys and config where the invoking user will never look
  for them. Nothing about that depends on which tunnel provider is underneath,
  so it is enforced here, once, for every path devtunnel owns.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.domain.models import PlatformId, Scope


@dataclass(frozen=True, slots=True)
class DevtunnelPaths:
    """Resolved locations for one (platform, scope) pair."""

    home: str
    user_state_dir: str
    machine_state_dir: str
    binary_path: str
    """Absolute path devtunnel invokes tailcat by. Recorded in the journal, so
    no ``PATH`` mutation is ever required for devtunnel itself to work --
    ``--add-to-path`` is a separate, opt-in convenience for the human."""

    allowlist_path: str
    authorized_keys_path: str
    ssh_dir: str
    cache_dir: str
    tailcat_keys_dir: str

    def state_dir(self, scope: Scope) -> str:
        return self.user_state_dir if scope is Scope.USER else self.machine_state_dir

    def default_ssh_identity(self) -> str:
        return os.path.join(self.ssh_dir, "id_ed25519")


_CANDIDATE_SSH_IDENTITIES = ("id_ed25519", "id_ecdsa", "id_rsa")
"""Checked in this order when deciding whether the client already has a usable
identity to adopt. ed25519 first because it is what devtunnel would generate."""


def resolve_paths(
    platform: PlatformId,
    filesystem: FileSystemPort,
    *,
    scope: Scope = Scope.USER,
) -> DevtunnelPaths:
    home = filesystem.real_user_home()

    if platform is PlatformId.WINDOWS:
        local_app_data = os.environ.get("LOCALAPPDATA") or os.path.join(
            home, "AppData", "Local"
        )
        program_data = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        program_files = os.environ.get("PROGRAMFILES", r"C:\Program Files")
        user_state = os.path.join(local_app_data, "devtunnel")
        machine_state = os.path.join(program_data, "devtunnel")
        binary = (
            os.path.join(user_state, "bin", "tailcat.exe")
            if scope is Scope.USER
            else os.path.join(program_files, "devtunnel", "tailcat.exe")
        )
    else:
        user_state = os.path.join(home, ".local", "share", "devtunnel")
        machine_state = "/var/lib/devtunnel"
        binary = (
            os.path.join(user_state, "bin", "tailcat")
            if scope is Scope.USER
            else "/usr/local/bin/tailcat"
        )

    return DevtunnelPaths(
        home=home,
        user_state_dir=user_state,
        machine_state_dir=machine_state,
        binary_path=binary,
        allowlist_path=os.path.join(user_state, "allow.list"),
        authorized_keys_path=os.path.join(home, ".ssh", "authorized_keys"),
        ssh_dir=os.path.join(home, ".ssh"),
        cache_dir=os.path.join(user_state, "cache"),
        # tailcat's own location, not devtunnel's to choose -- devtunnel only
        # needs to know it so it can verify the 0600/0700 permissions on a
        # private key after an elevated run.
        tailcat_keys_dir=os.path.join(home, ".config", "tailcat", "keys"),
    )


def journal_path(platform: PlatformId, scope: Scope, paths: DevtunnelPaths) -> str:
    return os.path.join(paths.state_dir(scope), "journal.json")
