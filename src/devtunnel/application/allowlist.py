"""devtunnel's record of which peers may open a tunnel at all.

tailcat takes its allowlist as ``--allow=<k1>,<k2>`` on each ``serve``
invocation -- it keeps no allowlist of its own between runs. Something has to
remember the set across a foreground ``up``, a restarted service and a reboot,
and that something is this file.

It is deliberately the *dumbest possible* format: one ``nodekey:<hex>`` per
line, comments and blanks ignored. A peer's full identity (display name, SSH
key, when it was added) lives in the journal record that created it, not here
-- so there is exactly one source of truth for a peer, and this file stays a
derived projection that can be rebuilt from the journal if it is ever lost.
"""

from __future__ import annotations

from devtunnel.application.ports.filesystem import FileSystemPort


class Allowlist:
    """The persisted set of node keys permitted to connect."""

    def __init__(self, filesystem: FileSystemPort, path: str) -> None:
        self._filesystem = filesystem
        self._path = path

    @property
    def path(self) -> str:
        return self._path

    def entries(self) -> list[str]:
        content = self._filesystem.read_text(self._path)
        if content is None:
            return []
        return [
            line.strip()
            for line in content.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def contains(self, node_key: str) -> bool:
        return node_key in self.entries()

    def add(self, node_key: str) -> bool:
        """Add ``node_key``. Returns False if it was already present.

        Idempotent on purpose: adding the same peer twice must not produce a
        second entry that a single ``pair remove`` would then fail to clear.
        """

        current = self.entries()
        if node_key in current:
            return False
        self._write(current + [node_key])
        return True

    def remove(self, node_key: str) -> bool:
        """Remove ``node_key``. Returns False if it was not present."""

        current = self.entries()
        if node_key not in current:
            return False
        self._write([e for e in current if e != node_key])
        return True

    def _write(self, entries: list[str]) -> None:
        header = (
            "# devtunnel tailcat allowlist -- one nodekey:<hex> per line.\n"
            "# Managed by 'devtunnel pair add/remove' and 'devtunnel allow'.\n"
        )
        body = "".join(f"{e}\n" for e in entries)
        # 0600: this is not secret material, but it decides who may reach the
        # box, and a world-writable copy would let anyone add themselves.
        self._filesystem.write_text(self._path, header + body, mode=0o600)
