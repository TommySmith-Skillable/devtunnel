"""Helper to build an ``ExecutionContext`` wired entirely with fakes."""

from __future__ import annotations

from devtunnel.application.context import ExecutionContext
from devtunnel.domain.events import EventBus
from tests.fakes.fake_ports import (
    FakeFileSystem,
    FakeProcessRunner,
    FakePrompter,
    FakeToolkit,
    FakeTunnelProvider,
    InMemoryJournalRepository,
)


def make_context(**overrides) -> ExecutionContext:
    defaults = dict(
        toolkit=FakeToolkit(),
        process=FakeProcessRunner(),
        journal=InMemoryJournalRepository(),
        filesystem=FakeFileSystem(),
        prompter=FakePrompter(),
        credentials=None,
        tunnel_provider=FakeTunnelProvider(),
        events=EventBus(),
        dry_run=False,
        non_interactive=False,
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)
