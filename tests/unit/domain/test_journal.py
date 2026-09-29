from devtunnel.domain.journal import ChangeKind, ChangeRecord, RecordStatus


def test_pending_creates_a_unique_crash_safe_record():
    record = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git")

    assert record.status is RecordStatus.PENDING
    assert record.kind is ChangeKind.PACKAGE_INSTALLED
    assert record.target == "package:git"
    assert record.id  # non-empty, assigned eagerly
    assert record.needs_revert  # a crash after this write is still recoverable


def test_two_pending_records_get_distinct_ids():
    a = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git")
    b = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:ngrok")

    assert a.id != b.id


def test_applied_merges_details_and_keeps_prior_state():
    record = ChangeRecord.pending(
        ChangeKind.SERVICE_STATE_CHANGED, "service:sshd", prior_state={"running": False}
    )
    applied = record.applied(details={"note": "started"})

    assert applied.status is RecordStatus.APPLIED
    assert applied.prior_state == {"running": False}
    assert applied.details == {"note": "started"}
    assert applied.needs_revert


def test_skipped_preexisting_is_never_reverted():
    record = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git")
    skipped = record.skipped_preexisting()

    assert skipped.status is RecordStatus.SKIPPED_PREEXISTING
    assert not skipped.needs_revert


def test_reverted_clears_the_need_to_revert_again():
    record = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git").applied()
    reverted = record.reverted()

    assert reverted.status is RecordStatus.REVERTED
    assert reverted.reverted_at is not None
    assert not reverted.needs_revert


def test_revert_failed_stays_eligible_for_a_retry():
    record = ChangeRecord.pending(ChangeKind.PACKAGE_INSTALLED, "package:git").applied()
    failed = record.revert_failed(reason="permission denied")

    assert failed.status is RecordStatus.REVERT_FAILED
    assert failed.details["revert_failure_reason"] == "permission denied"
    assert failed.needs_revert  # a later uninstall run should try again
