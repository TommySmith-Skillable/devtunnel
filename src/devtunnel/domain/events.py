"""Progress events and the Observer-pattern bus that carries them.

Plan execution reports its progress by publishing these events rather than
printing directly. That keeps the domain/application layers ignorant of how
progress is *shown* -- the Rich console presenter and the ``--json`` presenter
both just subscribe to the same :class:`EventBus`. See
:mod:`devtunnel.cli.presenters`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class Phase(StrEnum):
    INSTALL = "install"
    UNINSTALL = "uninstall"


@dataclass(frozen=True, slots=True)
class StepEvent:
    """A single notification about one step's progress."""

    phase: Phase
    step_id: str
    description: str
    # "started" | "applied" | "skipped" | "failed" | "reverted" | "revert_failed" | "dry_run"
    status: str
    detail: str = ""


class EventListener(Protocol):
    """Anything that can receive :class:`StepEvent` notifications."""

    def on_event(self, event: StepEvent) -> None: ...


class EventBus:
    """A minimal, synchronous Subject (Observer pattern).

    No I/O, no threading -- publishing simply calls each subscriber in turn.
    Kept in the domain layer because it is pure in-memory fan-out with no
    dependency on any port or adapter.
    """

    def __init__(self) -> None:
        self._listeners: list[EventListener] = []

    def subscribe(self, listener: EventListener) -> None:
        self._listeners.append(listener)

    def publish(self, event: StepEvent) -> None:
        for listener in self._listeners:
            listener.on_event(event)
