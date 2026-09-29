from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.infrastructure.journal.json_journal import JsonJournalRepository


def test_round_trips_a_record_through_disk(tmp_path):
    path = str(tmp_path / "journal.json")
    repo = JsonJournalRepository(path)
    record = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git")

    repo.append(record)
    reloaded = JsonJournalRepository(path).load()

    assert len(reloaded) == 1
    assert reloaded[0].id == record.id
    assert reloaded[0].kind is ChangeKind.PACKAGE_INSTALLED
    assert reloaded[0].status is record.status


def test_update_replaces_the_matching_record_in_place(tmp_path):
    path = str(tmp_path / "journal.json")
    repo = JsonJournalRepository(path)
    record = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git")
    repo.append(record)

    repo.update(record.applied())

    reloaded = repo.load()
    assert len(reloaded) == 1
    assert reloaded[0].status.value == "applied"


def test_load_on_a_missing_file_returns_an_empty_list(tmp_path):
    repo = JsonJournalRepository(str(tmp_path / "nonexistent.json"))

    assert repo.load() == []


def test_clear_removes_the_file(tmp_path):
    path = str(tmp_path / "journal.json")
    repo = JsonJournalRepository(path)
    repo.append(ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git"))

    repo.clear()

    assert repo.load() == []
