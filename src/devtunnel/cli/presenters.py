"""Observer-pattern subscribers that turn :class:`StepEvent`s into output.

Both presenters subscribe to the same :class:`~devtunnel.domain.events.EventBus`;
the core plan-execution code never knows which (if either) is listening. This
is what makes ``--json`` a second subscriber rather than a branch threaded
through every step.
"""

from __future__ import annotations

import json
import sys
from typing import TextIO

from rich.console import Console

from devtunnel.domain.events import StepEvent

_STATUS_STYLE = {
    "applied": ("✓", "green"),
    "skipped": ("-", "yellow"),
    "failed": ("✗", "bold red"),
    "reverted": ("✓", "green"),
    "revert_failed": ("✗", "bold red"),
    "dry_run": ("»", "cyan"),
}


class RichPresenter:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def on_event(self, event: StepEvent) -> None:
        if event.status == "started":
            return  # only the terminal state of each step is rendered
        symbol, style = _STATUS_STYLE.get(event.status, ("?", "white"))
        detail = f" ({event.detail})" if event.detail else ""
        self._console.print(f"[{style}]{symbol}[/{style}] {event.description}{detail}")


class JsonPresenter:
    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream or sys.stdout

    def on_event(self, event: StepEvent) -> None:
        payload = {
            "phase": event.phase.value,
            "step_id": event.step_id,
            "description": event.description,
            "status": event.status,
            "detail": event.detail,
        }
        print(json.dumps(payload), file=self._stream, flush=True)
