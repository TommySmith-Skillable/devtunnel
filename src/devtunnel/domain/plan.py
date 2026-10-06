"""The install/uninstall plan: a Composite tree of reversible Steps.

A :class:`Step` is a Command -- it knows how to ``apply`` itself and, given
the :class:`~devtunnel.domain.journal.ChangeRecord` it produced, how to
``revert`` itself. :class:`Plan` (and the :class:`StepGroup` nodes nested
inside it) is the Composite that lets :meth:`StepGroup.walk` flatten an
arbitrarily nested tree of named groups ("Install tailcat", "Configure peers", ...)
into the ordered sequence of leaf steps that actually get executed -- the same
traversal drives both real execution and ``--dry-run`` rendering.

``Step`` does not import anything from ``application`` or ``infrastructure``
at runtime. Its ``apply``/``revert``/``dry_run`` methods are typed against
``ExecutionContext`` only under ``TYPE_CHECKING``, via the postponed
evaluation of annotations (``from __future__ import annotations``) -- so the
domain layer stays free of outward dependencies while still giving type
checkers and readers a precise contract for what a Step is handed.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from devtunnel.domain.journal import ChangeRecord

if TYPE_CHECKING:
    from devtunnel.application.context import ExecutionContext


class StepStatus(StrEnum):
    APPLIED = "applied"
    SKIPPED = "skipped"
    FAILED = "failed"
    REVERTED = "reverted"
    REVERT_FAILED = "revert_failed"
    DRY_RUN = "dry_run"


@dataclass(frozen=True, slots=True)
class StepOutcome:
    """What happened when a step was applied, reverted, or dry-run."""

    status: StepStatus
    message: str = ""
    record: ChangeRecord | None = None


class Step(ABC):
    """A single reversible unit of work (Command pattern).

    ``step_id`` is stable across a single plan build and is used to correlate
    progress events and to support ``--skip``/``--keep`` filters -- it is
    *not* the journal record id (that is assigned fresh, per invocation, by
    :meth:`~devtunnel.domain.journal.ChangeRecord.pending`).
    """

    def __init__(self, step_id: str, description: str) -> None:
        self.id = step_id
        self.description = description

    @abstractmethod
    def apply(self, ctx: ExecutionContext) -> StepOutcome:
        """Perform the change, journaling it via ``ctx.journal`` before/after.

        Implementations must be idempotent: if the target state already
        exists, they should return ``StepOutcome(StepStatus.SKIPPED, ...)``
        with a ``SKIPPED_PREEXISTING`` record rather than re-applying.
        """

    def revert(self, ctx: ExecutionContext, record: ChangeRecord) -> StepOutcome:
        """Undo a previously applied change, given its journal record.

        The default implementation is for steps that are never themselves
        reverted (preflight checks, group markers) -- concrete Steps that
        journal a :class:`~devtunnel.domain.journal.ChangeKind` override this.
        """

        return StepOutcome(status=StepStatus.SKIPPED, message="nothing to revert")

    def describe_dry_run(self) -> str:
        """A one-line human description used when rendering ``--dry-run``."""

        return self.description


@dataclass
class StepGroup:
    """A named, ordered, possibly-nested collection of steps (Composite).

    Groups exist purely for structure and presentation (e.g. "Install git",
    "Configure OpenSSH") -- they carry no journaled state of their own.
    """

    description: str
    children: list[Step | StepGroup] = field(default_factory=list)

    def walk(self) -> Iterator[Step]:
        """Depth-first flatten to leaf :class:`Step` instances, in order."""

        for child in self.children:
            if isinstance(child, StepGroup):
                yield from child.walk()
            else:
                yield child


class Plan(StepGroup):
    """The root of the composite tree produced by ``PlanBuilder``."""

    def __init__(self, name: str, children: list[Step | StepGroup]) -> None:
        super().__init__(description=name, children=children)
