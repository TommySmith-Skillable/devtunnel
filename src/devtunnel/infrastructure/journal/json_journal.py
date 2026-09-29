"""The real :class:`JournalRepositoryPort` adapter: a JSON file on disk.

Writes are atomic (write to a temp file, then ``os.replace``) so a crash
mid-write never corrupts the journal -- worst case, the most recent
``append``/``update`` is lost, and the record it was capturing was already
written as ``PENDING`` in a prior call.
"""

from __future__ import annotations

import json
import os
from datetime import datetime

from devtunnel.application.ports.journal_repo import JournalRepositoryPort
from devtunnel.domain.journal import ChangeKind, ChangeRecord, RecordStatus


class JsonJournalRepository(JournalRepositoryPort):
    def __init__(self, path: str) -> None:
        self._path = path

    def load(self) -> list[ChangeRecord]:
        if not os.path.exists(self._path):
            return []
        with open(self._path, encoding="utf-8") as f:
            raw = json.load(f)
        return [self._from_dict(d) for d in raw]

    def append(self, record: ChangeRecord) -> None:
        records = self.load()
        records.append(record)
        self._save(records)

    def update(self, record: ChangeRecord) -> None:
        records = self.load()
        for i, existing in enumerate(records):
            if existing.id == record.id:
                records[i] = record
                break
        else:
            records.append(record)
        self._save(records)

    def clear(self) -> None:
        if os.path.exists(self._path):
            os.remove(self._path)

    def location(self) -> str:
        return self._path

    def _save(self, records: list[ChangeRecord]) -> None:
        directory = os.path.dirname(self._path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp_path = f"{self._path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump([self._to_dict(r) for r in records], f, indent=2)
        os.replace(tmp_path, self._path)

    @staticmethod
    def _to_dict(record: ChangeRecord) -> dict:
        return {
            "id": record.id,
            "kind": record.kind.value,
            "target": record.target,
            "status": record.status.value,
            "prior_state": record.prior_state,
            "details": record.details,
            "performed_at": record.performed_at.isoformat(),
            "reverted_at": record.reverted_at.isoformat() if record.reverted_at else None,
        }

    @staticmethod
    def _from_dict(data: dict) -> ChangeRecord:
        reverted_at = data.get("reverted_at")
        return ChangeRecord(
            id=data["id"],
            kind=ChangeKind(data["kind"]),
            target=data["target"],
            status=RecordStatus(data["status"]),
            prior_state=data.get("prior_state", {}),
            details=data.get("details", {}),
            performed_at=datetime.fromisoformat(data["performed_at"]),
            reverted_at=datetime.fromisoformat(reverted_at) if reverted_at else None,
        )
