"""The toolbox every :class:`~devtunnel.domain.plan.Step` is handed.

``ExecutionContext`` is assembled once by the composition root
(:mod:`devtunnel.container`) and threaded through plan execution. It bundles
platform-specific adapters behind ``toolkit`` (the Abstract Factory) and the
platform-agnostic ports individually -- see the port docstrings in
``application/ports/`` for why each one is shaped the way it is.
"""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application.ports.credentials import CredentialProviderPort
from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.journal_repo import JournalRepositoryPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.prompter import PrompterPort
from devtunnel.application.ports.toolkit import PlatformToolkit
from devtunnel.application.ports.tunnel_provider import TunnelProviderPort
from devtunnel.domain.events import EventBus, Phase
from devtunnel.domain.models import PlatformId


@dataclass
class ExecutionContext:
    toolkit: PlatformToolkit
    process: ProcessRunnerPort
    journal: JournalRepositoryPort
    filesystem: FileSystemPort
    prompter: PrompterPort
    credentials: CredentialProviderPort
    tunnel_provider: TunnelProviderPort
    events: EventBus
    phase: Phase = Phase.INSTALL
    dry_run: bool = False
    non_interactive: bool = False

    @property
    def platform(self) -> PlatformId:
        return self.toolkit.platform
