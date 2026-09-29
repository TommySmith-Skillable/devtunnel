"""Repository port for the persisted install journal."""

from __future__ import annotations

from typing import Protocol

from devtunnel.domain.journal import ChangeRecord


class JournalRepositoryPort(Protocol):
    def load(self) -> list[ChangeRecord]:
        """Return all records, oldest first. Empty list if none exist yet."""

    def append(self, record: ChangeRecord) -> None:
        """Persist a new record immediately (crash-safe: called before a
        mutation happens, with status PENDING)."""

    def update(self, record: ChangeRecord) -> None:
        """Persist an updated record, matched by ``record.id``."""

    def clear(self) -> None:
        """Remove the journal entirely. Called only once uninstall has
        successfully reverted every record."""

    def location(self) -> str:
        """A human-readable path/description, for ``status``/``doctor``."""
