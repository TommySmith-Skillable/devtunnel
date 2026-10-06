"""tailcat key mechanics: generate, list, delete, and decode an address.

This is the half of :class:`~devtunnel.application.ports.tunnel_provider.TunnelProviderPort`
that owns key material;
:class:`~devtunnel.infrastructure.tailcat.tailcat_tunnel.TailcatTunnelProvider`
composes it with process lifecycle to satisfy the whole port.

**Elevation and the real user's home.** When the installer runs elevated --
``--with-git``, ``--system``, or a plain ``sudo devtunnel`` -- ``$HOME`` and
``~`` resolve to *root's* home, so a key written naively lands in
``/root/.config/...`` and the real user's tool never finds it afterwards.
tailcat resolves its key directory from the environment, so every key command
here is invoked with ``HOME``/``USERPROFILE`` pointed at
:meth:`~devtunnel.application.ports.filesystem.FileSystemPort.real_user_home`,
and the resulting directory is chowned back to the real user.

**Private key material is never read, copied or journaled** (plan section 13.6).
``<config-dir>/tailcat/keys/<name>.private.json`` holds a WireGuard private
key (see :meth:`TailcatKeys._config_home` -- the directory is platform-specific,
not ``~/.config`` everywhere).
This module tests that file for *existence* and fixes its *mode*; it never opens
it. The address and the ``nodekey:<hex>`` are public material and safe to put in
a journal record, and both are obtained from the CLI's own output -- which is
also why :meth:`TailcatKeys.address_for` reads ``genkey --list`` rather than
parsing the key file for its public fields. A parser pointed at that file is one
careless change away from journaling the private half next to the public one.
"""

from __future__ import annotations

import json
import os
import re
import sys

from devtunnel.application import catalog
from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort

_ADDRESS_RE = re.compile(r"\btc[A-Za-z0-9_-]{16,}\b")
"""tailcat addresses are one opaque ``tc``-prefixed blob. Deliberately loose on
length because the encoding is upstream's business and pre-1.0."""

_NODE_KEY_RE = re.compile(r"nodekey:[0-9a-f]{64}")

_KEY_FILE_MODE = 0o600
_KEY_DIR_MODE = 0o700


class TailcatKeys:
    """Wraps ``tailcat genkey`` and ``tailcat parse``.

    ``binary_path`` is injected rather than assumed: devtunnel installs tailcat
    into a user-scope directory that is deliberately *not* added to ``PATH``
    (``--add-to-path`` is opt-in and separately journaled), and always invokes
    it by the absolute path recorded in the journal record's ``details["path"]``.
    The default exists only so tests and a hand-run checkout can say "whatever
    is on PATH".
    """

    def __init__(
        self,
        process: ProcessRunnerPort,
        filesystem: FileSystemPort,
        *,
        binary_path: str = catalog.TAILCAT_BINARY_NAME,
    ) -> None:
        self._process = process
        self._filesystem = filesystem
        self._binary_path = binary_path

    # -- locations -------------------------------------------------------

    def keys_dir(self) -> str:
        """The directory tailcat saves keys in, resolved against the *real*
        user's home so an elevated run still points at the right place."""

        return os.path.join(self._config_home(), "tailcat", "keys")

    def _config_home(self) -> str:
        """Mirror of Go's ``os.UserConfigDir``, which is how tailcat picks the
        parent of its key directory.

        This is **not** ``~/.config`` everywhere -- that is the Linux answer
        only. Go returns ``%AppData%`` on Windows and
        ``~/Library/Application Support`` on macOS, so hardcoding ``.config``
        made :meth:`has_key` answer ``False`` on two of the three platforms
        even with the key sitting on disk: the install step then re-ran
        ``genkey``, which refuses to clobber an existing key without
        ``--force`` and exits 1, so a second ``devtunnel install`` could never
        succeed.

        Every branch is derived from
        :meth:`~devtunnel.application.ports.filesystem.FileSystemPort.real_user_home`
        rather than from ``%AppData%``/``$XDG_CONFIG_HOME`` directly, for the
        same reason the rest of this module pins ``HOME``: under an elevated
        run those variables hold the *elevating* user's paths, which is the
        exact bug the real-user indirection exists to prevent. The pinning in
        :meth:`_home_env` is what keeps tailcat's own answer in step with this
        one, so the two cannot drift.
        """

        home = self._filesystem.real_user_home()
        if os.name == "nt":
            return os.path.join(home, "AppData", "Roaming")
        if sys.platform == "darwin":
            return os.path.join(home, "Library", "Application Support")
        return os.path.join(home, ".config")

    def key_path(self, name: str) -> str:
        return os.path.join(self.keys_dir(), f"{name}.private.json")

    def has_key(self, name: str) -> bool:
        """Whether a saved key of this name exists.

        Existence only -- the file is never opened. That is enough for the
        ``already_satisfied -> skip`` contract and keeps the private half of the
        keypair out of this process entirely.
        """

        return self._filesystem.exists(self.key_path(name))

    # -- mutations -------------------------------------------------------

    def generate(
        self,
        name: str,
        *,
        client: bool = False,
        region: str | None = None,
        fixed_region: bool = False,
    ) -> dict:
        """Run ``tailcat genkey`` and return prior-state details for the journal.

        ``existed`` is captured *before* the command runs: it is what stops
        :meth:`TailcatTunnelProvider.remove_key` from deleting a key the user
        already had, the same ``SKIPPED_PREEXISTING`` promise devtunnel makes
        for packages.

        ``genkey`` prints the address (server keys) or the ``nodekey:<hex>``
        (client keys) on stdout. Both are captured here so that ``up``,
        ``serve`` and ``pair export`` never have to re-run a key command -- and
        so the persistent path can print a connection command before the tunnel
        process has said anything at all.
        """

        existed = self.has_key(name)

        argv = [self._binary_path, "genkey"]
        if client:
            argv.append("--client")
        argv.append(f"--key={name}")
        if region:
            argv.append(f"--region={region}")
        if fixed_region:
            argv.append("--fixed-region")

        result = self._process.run(argv, env=self._home_env())

        self._filesystem.take_ownership_for_real_user(self.keys_dir())
        self._secure_key_files(name)

        address_match = _ADDRESS_RE.search(result.stdout)
        node_key_match = _NODE_KEY_RE.search(result.stdout)

        return {
            "name": name,
            "key_path": self.key_path(name),
            "client": client,
            "existed": existed,
            "address": address_match.group(0) if address_match else None,
            "node_key": node_key_match.group(0) if node_key_match else None,
            "region": region,
            "fixed_region": fixed_region,
        }

    def delete(self, name: str) -> None:
        """Run ``tailcat genkey --delete``.

        Deletion goes through tailcat rather than ``rm`` on the key file so
        that devtunnel never has to know the on-disk layout, and cannot leave
        an index or sidecar file behind by not knowing about it.
        """

        self._process.run(
            [self._binary_path, "genkey", "--delete", f"--key={name}"],
            env=self._home_env(),
        )

    # -- public values ---------------------------------------------------

    def address_for(self, name: str) -> str | None:
        """The stable ``tc...`` address of a saved key, or ``None``.

        Read from ``genkey --list``, never by re-running ``genkey`` (which would
        mint a new key) and never by opening ``<name>.private.json``. If
        ``--list`` does not report the value this returns ``None``, and the
        caller falls back to the address cached in the journal record's
        ``details`` at generation time.

        **As of tailcat 0.7.0 that fallback is the only path that resolves.**
        ``genkey --list`` prints one key name per line and no public material
        at all, so the pattern below cannot match and this method always
        answers ``None``. It is kept rather than deleted because the journal
        cache is the *authoritative* source either way -- see
        :func:`~devtunnel.cli.app._cached_public_value` -- and because a later
        tailcat that enriches the listing should start cross-checking it
        without a code change. What callers must not do is treat ``None`` here
        as "no such key": it is the normal answer.
        """

        return self._match_in_listing(name, _ADDRESS_RE)

    def node_key(self, name: str) -> str | None:
        """This machine's ``nodekey:<hex>`` for ``name``, for a peer's allowlist.

        Same sourcing and same fallback as :meth:`address_for`, including the
        0.7.0 caveat: the listing carries no node key, so this returns ``None``
        on every healthy install and the journal cache is what actually answers.
        A node key is public material -- it is the thing a peer puts in
        ``--allow`` -- so reporting it is safe; the private half it derives
        from is not read.
        """

        return self._match_in_listing(name, _NODE_KEY_RE)

    def parse_address(self, address: str) -> dict:
        """Decode an address with ``tailcat parse``, which emits JSON on stdout.

        Used by ``devtunnel connect --expect`` to compare the host's advertised
        ``ServerPublic`` against a pinned node key before connecting. Decoding
        through tailcat rather than reimplementing the blob format keeps a
        pre-1.0 encoding entirely upstream's problem.
        """

        result = self._process.run([self._binary_path, "parse", address])
        return json.loads(result.stdout)

    # -- internals -------------------------------------------------------

    def _match_in_listing(self, name: str, pattern: re.Pattern[str]) -> str | None:
        result = self._process.run(
            [self._binary_path, "genkey", "--list"],
            check=False,
            env=self._home_env(),
        )
        if not result.ok:
            return None

        # Match the key name as a whole token so that a listing containing both
        # "default" and "client-default" never resolves the wrong row: the
        # character before "default" in "client-default" is excluded by the
        # lookbehind. Quoting in the listing ("default") is tolerated.
        name_re = re.compile(rf"(?<![A-Za-z0-9_.-]){re.escape(name)}(?![A-Za-z0-9_.-])")
        for line in result.stdout.splitlines():
            if not name_re.search(line):
                continue
            match = pattern.search(line)
            if match:
                return match.group(0)
        return None

    def _home_env(self) -> dict[str, str]:
        """Environment overrides that pin tailcat to the real user's home.

        Both variables are always set, with no ``os.name`` branch: tailcat is a
        Go program, and ``os.UserHomeDir`` reads ``HOME`` on POSIX and
        ``USERPROFILE`` on Windows. Setting both means the elevated-install fix
        is exercised by the same code path -- and the same test -- on either
        platform, instead of living in a branch only one CI runner ever reaches.
        The unset variable is simply ignored by the other platform.
        """

        home = self._filesystem.real_user_home()
        config_home = self._config_home()
        # HOME/USERPROFILE alone do not settle it. ProcessRunner *merges* these
        # over os.environ, so an ambient %AppData% or $XDG_CONFIG_HOME -- which
        # Go consults ahead of the home directory on Windows and Linux -- would
        # otherwise survive and send tailcat somewhere keys_dir() is not
        # looking. Pinning both makes keys_dir() authoritative rather than a
        # guess about upstream's resolution order. The variables that do not
        # apply to the running platform are simply ignored by it.
        return {
            "HOME": home,
            "USERPROFILE": home,
            "APPDATA": config_home,
            "XDG_CONFIG_HOME": config_home,
        }

    def _secure_key_files(self, name: str) -> None:
        """Force ``0700`` on the key directory and ``0600`` on the key file.

        Plan section 13.5: this file is a WireGuard private key, and an elevated
        run creates it under root's umask before ownership is handed back. A
        world-readable private key is the kind of residue this project exists to
        prevent, so the mode is asserted rather than assumed.

        ``os.chmod`` is called directly -- the one direct filesystem call in this
        adapter -- because :class:`~devtunnel.application.ports.filesystem.FileSystemPort`
        exposes ``mode`` only on the *write* operations, and devtunnel never
        writes this file; tailcat does. Adding a ``chmod`` to the port for a
        single POSIX-only caller would widen a port shared by every adapter.
        The ``os.name`` guard is what keeps it honest: NTFS ACLs do not model
        POSIX mode bits, and ``os.chmod`` on Windows would only toggle the
        read-only flag, which is not the control being applied here.
        """

        if os.name == "nt":
            return
        for path, mode in ((self.keys_dir(), _KEY_DIR_MODE), (self.key_path(name), _KEY_FILE_MODE)):
            if self._filesystem.exists(path):
                os.chmod(path, mode)
