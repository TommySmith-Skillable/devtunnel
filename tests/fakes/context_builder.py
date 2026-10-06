"""Helper to build an ``ExecutionContext`` wired entirely with fakes."""

from __future__ import annotations

from devtunnel.application.context import ExecutionContext
from devtunnel.domain.events import EventBus
from devtunnel.domain.models import Scope
from tests.fakes.fake_ports import (
    FakeBinaryInstaller,
    FakeFileSystem,
    FakeProcessRunner,
    FakePrompter,
    FakeToolkit,
    FakeTunnelProvider,
    InMemoryJournalRepository,
    fake_paths,
)


def make_context(**overrides) -> ExecutionContext:
    """A context with one in-memory journal per scope.

    Both scopes are always wired, even for a test that only exercises
    user-scope steps: the merged view is what ``status`` and ``uninstall``
    read, and a test that silently had only one journal would not notice a
    step writing to the wrong one.
    """

    journals = overrides.pop("journals", None)
    if journals is None:
        journals = {
            Scope.USER: InMemoryJournalRepository(),
            Scope.MACHINE: InMemoryJournalRepository(),
        }

    defaults = dict(
        toolkit=FakeToolkit(),
        process=FakeProcessRunner(),
        journals=journals,
        filesystem=FakeFileSystem(),
        prompter=FakePrompter(),
        tunnel_provider=FakeTunnelProvider(),
        binary_installer=FakeBinaryInstaller(),
        paths=fake_paths(),
        events=EventBus(),
        dry_run=False,
        non_interactive=False,
    )
    defaults.update(overrides)
    return ExecutionContext(**defaults)
