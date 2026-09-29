"""Port for filesystem mutations that need to be reversible.

Kept distinct from :class:`~devtunnel.application.ports.process_runner.ProcessRunnerPort`
because file operations carry their own Memento concern (backing up a file
before modifying it) and their own sudo-awareness (a token written while
running under ``sudo`` must land in the invoking user's home directory, not
root's -- see :meth:`FileSystemPort.real_user_home`).
"""

from __future__ import annotations

from typing import Protocol


class FileSystemPort(Protocol):
    def exists(self, path: str) -> bool: ...

    def read_text(self, path: str) -> str | None:
        """Return the file's content, or ``None`` if it does not exist."""

    def write_text(self, path: str, content: str, *, mode: int | None = None) -> None: ...

    def write_bytes(self, path: str, content: bytes, *, mode: int | None = None) -> None: ...

    def remove_file(self, path: str) -> None:
        """Delete the file if present; a no-op if it is already gone."""

    def ensure_dir(self, path: str) -> bool:
        """Create ``path`` (and parents) if missing. Returns True iff it was
        created (i.e. it did not already exist)."""

    def remove_dir_if_empty(self, path: str) -> None:
        """Delete ``path`` only if it exists and is empty; a no-op otherwise."""

    def backup(self, path: str) -> str | None:
        """Copy ``path`` to a backup location and return that location, or
        ``None`` if ``path`` does not exist (nothing to back up)."""

    def restore_from_backup(self, path: str, backup_path: str | None) -> None:
        """Restore ``path`` from ``backup_path``, or delete ``path`` if
        ``backup_path`` is ``None`` (meaning it did not exist before)."""

    def real_user_home(self) -> str:
        """The invoking user's home directory, correct even under sudo/UAC
        elevation (resolves ``SUDO_USER`` on Debian rather than returning
        root's home)."""

    def take_ownership_for_real_user(self, path: str) -> None:
        """Chown ``path`` (recursively if a directory) to the real user.
        A no-op on platforms without a POSIX ownership model."""
