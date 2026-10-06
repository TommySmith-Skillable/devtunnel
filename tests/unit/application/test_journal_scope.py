"""The user/machine journal split (D6).

The split is what lets a default install record what it did without needing a
system-owned path -- and therefore without needing elevation to write the
record of a change that itself needed none.
"""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from devtunnel.application.journals import MergedJournalRepository
from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.domain.models import Scope
from tests.fakes.fake_ports import InMemoryJournalRepository


def _journals():
    return {Scope.USER: InMemoryJournalRepository(), Scope.MACHINE: InMemoryJournalRepository()}


def _record(target: str) -> ChangeRecord:
    return ChangeRecord.pending(ChangeKind.FILE_MODIFIED, target).applied()


def _at(record: ChangeRecord, second: int) -> ChangeRecord:
    return replace(record, performed_at=datetime(2026, 10, 2, 12, 0, second, tzinfo=UTC))


def test_merged_load_returns_the_union_of_both_scopes():
    journals = _journals()
    journals[Scope.USER].append(_record("user:a"))
    journals[Scope.MACHINE].append(_record("machine:b"))

    merged = MergedJournalRepository(journals).load()

    assert {r.target for r in merged} == {"user:a", "machine:b"}


def test_merged_load_is_chronological_across_files_not_grouped_by_file():
    # Uninstall's correctness depends on this: a machine-scope service
    # registered after a user-scope binary must still be unwound first.
    # Timestamps are set explicitly because the point of the test is the
    # interleaving, not how fast the test machine can build three objects.
    journals = _journals()
    journals[Scope.USER].append(_at(_record("user:first"), 1))
    journals[Scope.USER].append(_at(_record("user:third"), 3))
    journals[Scope.MACHINE].append(_at(_record("machine:second"), 2))

    order = [r.target for r in MergedJournalRepository(journals).load()]

    assert order == ["user:first", "machine:second", "user:third"]


def test_an_update_is_routed_to_the_journal_that_holds_the_record():
    journals = _journals()
    record = _record("machine:b")
    journals[Scope.MACHINE].append(record)
    merged = MergedJournalRepository(journals)

    merged.update(record.reverted())

    assert journals[Scope.MACHINE].load()[0].status.value == "reverted"
    assert journals[Scope.USER].load() == []


def test_an_update_for_an_unknown_record_fails_rather_than_creating_one():
    merged = MergedJournalRepository(_journals())

    with pytest.raises(LookupError):
        merged.update(_record("nowhere"))


def test_append_is_refused_because_a_record_always_belongs_to_one_scope():
    merged = MergedJournalRepository(_journals())

    with pytest.raises(NotImplementedError):
        merged.append(_record("user:a"))


def test_scope_of_reports_which_journal_holds_a_record():
    journals = _journals()
    record = _record("user:a")
    journals[Scope.USER].append(record)

    assert MergedJournalRepository(journals).scope_of(record.id) is Scope.USER


def test_clear_empties_every_scope():
    journals = _journals()
    journals[Scope.USER].append(_record("user:a"))
    journals[Scope.MACHINE].append(_record("machine:b"))
    merged = MergedJournalRepository(journals)

    merged.clear()

    assert merged.load() == []


def test_location_names_both_paths_so_status_can_show_them():
    location = MergedJournalRepository(_journals()).location()

    assert "user" in location and "machine" in location
