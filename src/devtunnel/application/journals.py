"""The scope split of the install journal, and the merged view over it.

Decision D6: ``Scope`` stops being a decorative enum and becomes load-bearing.
devtunnel now writes to **two** journals rather than one:

===========  ==========================================  ================================
Scope        Linux                                       Windows
===========  ==========================================  ================================
``USER``     ``~/.local/share/devtunnel/journal.json``   ``%LOCALAPPDATA%\\devtunnel\\...``
``MACHINE``  ``/var/lib/devtunnel/journal.json``         ``%ProgramData%\\devtunnel\\...``
===========  ==========================================  ================================

This is what makes an unprivileged install possible at all. Once tailcat is a
user-scope binary serving SSH itself, the default plan touches no system state
-- so it must not write to a system-owned path either, or the write itself
would re-impose the elevation the rest of the design just removed.

The split is invisible to a :class:`~devtunnel.application.steps.JournaledStep`,
which simply declares its own ``scope`` and is handed the matching repository.
It is equally invisible to ``status``, ``doctor`` and ``uninstall``, which read
:class:`MergedJournalRepository` -- one chronological sequence across both
files, with writes routed back to whichever journal actually holds the record.
That routing is the point: reverting a machine-scope record updates the machine
journal (and needs elevation), while a user-scope revert never touches it.
"""

from __future__ import annotations

from devtunnel.application.ports.journal_repo import JournalRepositoryPort
from devtunnel.domain.journal import ChangeRecord
from devtunnel.domain.models import Scope


class MergedJournalRepository(JournalRepositoryPort):
    """A read/update view over every scope's journal at once.

    Reads return the union in chronological order, so ``uninstall``'s
    reverse-chronological replay still unwinds changes in exactly the order
    they were made even when they landed in different files. Updates are
    routed by record id to the journal that actually holds the record, so a
    revert never silently migrates a record between scopes.

    ``append`` is deliberately **not** supported: a new record always belongs
    to one specific scope, and only the step creating it knows which. Steps
    call ``ctx.journal_for(scope)`` instead.
    """

    def __init__(self, journals: dict[Scope, JournalRepositoryPort]) -> None:
        self._journals = journals

    def load(self) -> list[ChangeRecord]:
        # Chronological, not per-file: two scopes' records interleave, and
        # uninstall's correctness depends on unwinding in true reverse order
        # (a machine-scope service registered after a user-scope binary must
        # still stop before that binary is deleted).
        #
        # The tie-break is each record's position within its own journal, not
        # its id. Ids carry a random suffix, so sorting by one would make the
        # order of same-instant records -- and therefore the order an
        # uninstall unwinds them in -- differ between runs. Position is the
        # one piece of ordering information a single journal genuinely has,
        # and it is exactly what a timestamp cannot distinguish.
        numbered: list[tuple[ChangeRecord, int]] = []
        for journal in self._journals.values():
            numbered.extend((record, index) for index, record in enumerate(journal.load()))
        numbered.sort(key=lambda pair: (pair[0].performed_at, pair[1]))
        return [record for record, _ in numbered]

    def append(self, record: ChangeRecord) -> None:
        raise NotImplementedError(
            "a record must be appended to a specific scope's journal; "
            "use ExecutionContext.journal_for(scope)"
        )

    def update(self, record: ChangeRecord) -> None:
        journal = self.journal_holding(record.id)
        if journal is None:
            raise LookupError(f"no journal holds record {record.id!r}")
        journal.update(record)

    def clear(self) -> None:
        """Clear every scope's journal.

        Only ever called once uninstall has reverted every record across all
        of them -- see ``UninstallEnvironmentUseCase``.
        """

        for journal in self._journals.values():
            journal.clear()

    def location(self) -> str:
        return ", ".join(
            f"{scope.value}: {journal.location()}" for scope, journal in self._journals.items()
        )

    # -- routing ---------------------------------------------------------

    def journal_holding(self, record_id: str) -> JournalRepositoryPort | None:
        for journal in self._journals.values():
            if any(r.id == record_id for r in journal.load()):
                return journal
        return None

    def scope_of(self, record_id: str) -> Scope | None:
        """Which scope's journal holds this record.

        ``uninstall`` asks this to decide whether a given revert needs
        elevation -- the whole payoff of the split.
        """

        for scope, journal in self._journals.items():
            if any(r.id == record_id for r in journal.load()):
                return scope
        return None
