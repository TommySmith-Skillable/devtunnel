"""The toolbox every :class:`~devtunnel.domain.plan.Step` is handed.

``ExecutionContext`` is assembled once by the composition root
(:mod:`devtunnel.container`) and threaded through plan execution. It bundles
platform-specific adapters behind ``toolkit`` (the Abstract Factory) and the
platform-agnostic ports individually -- see the port docstrings in
``application/ports/`` for why each one is shaped the way it is.

There is no ``credentials`` field: the credential chain existed to resolve one
vendor secret from env var, then flag, then prompt. tailcat has no vendor
secret -- its identity is a keypair generated locally -- so the whole tier
collapsed to **flag > config file > prompt**, which the CLI layer resolves
directly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from devtunnel.application.journals import MergedJournalRepository
from devtunnel.application.paths import DevtunnelPaths
from devtunnel.application.ports.binary_installer import BinaryInstallerPort
from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.journal_repo import JournalRepositoryPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.prompter import PrompterPort
from devtunnel.application.ports.toolkit import PlatformToolkit
from devtunnel.application.ports.tunnel_provider import TunnelProviderPort
from devtunnel.domain.events import EventBus, Phase
from devtunnel.domain.models import PlatformId, Scope


@dataclass
class ExecutionContext:
    toolkit: PlatformToolkit
    process: ProcessRunnerPort
    journals: dict[Scope, JournalRepositoryPort]
    filesystem: FileSystemPort
    prompter: PrompterPort
    tunnel_provider: TunnelProviderPort
    binary_installer: BinaryInstallerPort
    paths: DevtunnelPaths
    events: EventBus
    phase: Phase = Phase.INSTALL
    dry_run: bool = False
    non_interactive: bool = False
    _merged: MergedJournalRepository | None = field(default=None, repr=False, compare=False)

    @property
    def platform(self) -> PlatformId:
        return self.toolkit.platform

    def journal_for(self, scope: Scope) -> JournalRepositoryPort:
        """The journal a step of this scope writes to.

        A scope with no configured journal falls back to the user one rather
        than failing: a context built for a user-only run should still be able
        to execute a user-scope plan.
        """

        journal = self.journals.get(scope)
        if journal is None:
            return self.journals[Scope.USER]
        return journal

    @property
    def journal(self) -> MergedJournalRepository:
        """The merged read/update view across every scope.

        What ``status``, ``doctor`` and ``uninstall`` see. Steps do not use
        this for appends -- a new record belongs to one scope, and only the
        step knows which, so they go through :meth:`journal_for`.
        """

        if self._merged is None:
            self._merged = MergedJournalRepository(self.journals)
        return self._merged
