"""The install journal: an append-mostly log of every change devtunnel makes.

This module is the heart of reversibility. A :class:`ChangeRecord` is a
Memento -- it captures ``prior_state`` (the world *before* the change) at the
moment the change is made, so uninstall can restore that exact prior state
rather than guessing at one or deleting indiscriminately.

The lifecycle of a record is always:

    PENDING -> APPLIED -> (REVERTED | REVERT_FAILED)
    PENDING -> SKIPPED_PREEXISTING          # nothing was changed; never reverted

A record is written as ``PENDING`` *before* the underlying mutation happens.
If the process dies mid-step, the ``PENDING`` record survives in the journal
and a later ``uninstall`` still knows to attempt cleanup for it.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum


class ChangeKind(StrEnum):
    """The category of a single reversible mutation."""

    PKGMGR_BOOTSTRAPPED = "pkgmgr_bootstrapped"
    PACKAGE_INSTALLED = "package_installed"
    APT_REPO_ADDED = "apt_repo_added"
    WINDOWS_CAPABILITY_ADDED = "windows_capability_added"
    SERVICE_STATE_CHANGED = "service_state_changed"
    FILE_CREATED = "file_created"
    FILE_MODIFIED = "file_modified"
    DIR_CREATED = "dir_created"
    CONFIG_KEY_SET = "config_key_set"


class RecordStatus(StrEnum):
    PENDING = "pending"
    APPLIED = "applied"
    SKIPPED_PREEXISTING = "skipped_preexisting"
    REVERTED = "reverted"
    REVERT_FAILED = "revert_failed"


def _new_id() -> str:
    """A short, roughly time-ordered id.

    Ordering for replay does *not* depend on this being lexically sortable --
    the journal repository preserves insertion order -- but a time-prefixed id
    makes the on-disk journal readable by a human during troubleshooting.
    """

    millis = int(time.time() * 1000)
    return f"{millis:013d}-{uuid.uuid4().hex[:8]}"


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ChangeRecord:
    """One journaled, reversible mutation.

    ``prior_state`` and ``details`` are plain ``dict``s (not further typed
    value objects) deliberately: each :class:`ChangeKind` shapes them
    differently, and the journal must be able to (de)serialise anything a
    step chooses to record without the domain layer knowing about every
    adapter's internals.
    """

    id: str
    kind: ChangeKind
    target: str
    status: RecordStatus
    prior_state: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    performed_at: datetime = field(default_factory=_utcnow)
    reverted_at: datetime | None = None

    @classmethod
    def pending(
        cls,
        kind: ChangeKind,
        target: str,
        *,
        prior_state: dict | None = None,
        details: dict | None = None,
    ) -> ChangeRecord:
        return cls(
            id=_new_id(),
            kind=kind,
            target=target,
            status=RecordStatus.PENDING,
            prior_state=prior_state or {},
            details=details or {},
        )

    def applied(self, *, details: dict | None = None) -> ChangeRecord:
        merged = {**self.details, **(details or {})}
        return replace(self, status=RecordStatus.APPLIED, details=merged)

    def skipped_preexisting(self, *, details: dict | None = None) -> ChangeRecord:
        merged = {**self.details, **(details or {})}
        return replace(self, status=RecordStatus.SKIPPED_PREEXISTING, details=merged)

    def reverted(self) -> ChangeRecord:
        return replace(self, status=RecordStatus.REVERTED, reverted_at=_utcnow())

    def revert_failed(self, *, reason: str) -> ChangeRecord:
        return replace(
            self,
            status=RecordStatus.REVERT_FAILED,
            details={**self.details, "revert_failure_reason": reason},
        )

    @property
    def needs_revert(self) -> bool:
        """Whether uninstall should attempt to reverse this record.

        Pre-existing state (``SKIPPED_PREEXISTING``) is never touched -- that
        is the guarantee that uninstall never removes something devtunnel did
        not itself introduce. Already-reverted records are also left alone so
        a repeated ``uninstall`` run is a safe no-op.
        """

        return self.status in (
            RecordStatus.PENDING,
            RecordStatus.APPLIED,
            RecordStatus.REVERT_FAILED,
        )
