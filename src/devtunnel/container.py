"""Composition root: the one place allowed to know about every layer.

Detects the platform, wires the concrete adapters into an
:class:`~devtunnel.application.context.ExecutionContext`, and hands that back
to the CLI. Nothing above this module imports from ``infrastructure`` --
that is what keeps ``application`` free to be tested against fakes.
"""

from __future__ import annotations

import os

from devtunnel.application.context import ExecutionContext
from devtunnel.application.ports.toolkit import PlatformToolkit
from devtunnel.cli.presenters import JsonPresenter, RichPresenter
from devtunnel.cli.prompter import RichPrompter
from devtunnel.domain.events import EventBus
from devtunnel.domain.models import PlatformId
from devtunnel.infrastructure.credentials.chain import CredentialChain
from devtunnel.infrastructure.filesystem import LocalFileSystem
from devtunnel.infrastructure.journal.json_journal import JsonJournalRepository
from devtunnel.infrastructure.ngrok.ngrok_config import NgrokConfigWriter
from devtunnel.infrastructure.ngrok.ngrok_tunnel import NgrokTunnelProvider
from devtunnel.infrastructure.platform_detect import detect_platform
from devtunnel.infrastructure.process.dry_run_runner import DryRunProcessRunner
from devtunnel.infrastructure.process.subprocess_runner import SubprocessProcessRunner
from devtunnel.infrastructure.toolkits import DebianToolkit, WindowsToolkit


def _journal_path(platform: PlatformId) -> str:
    """System-scope, since installs are system-wide (require elevation)."""
    if platform is PlatformId.WINDOWS:
        base = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
        return os.path.join(base, "devtunnel", "journal.json")
    return "/var/lib/devtunnel/journal.json"


def build_context(
    *,
    dry_run: bool = False,
    non_interactive: bool = False,
    json_output: bool = False,
) -> ExecutionContext:
    platform = detect_platform()

    real_process = SubprocessProcessRunner()
    process = DryRunProcessRunner(real_process) if dry_run else real_process

    journal_path = _journal_path(platform)
    backups_dir = os.path.join(os.path.dirname(journal_path), "backups")
    filesystem = LocalFileSystem(backups_dir)

    toolkit: PlatformToolkit
    if platform is PlatformId.WINDOWS:
        toolkit = WindowsToolkit(process)
    else:
        toolkit = DebianToolkit(process, filesystem)

    prompter = RichPrompter()
    credentials = CredentialChain(prompter)
    tunnel_provider = NgrokTunnelProvider(process, NgrokConfigWriter(process, filesystem))

    events = EventBus()
    events.subscribe(JsonPresenter() if json_output else RichPresenter())

    return ExecutionContext(
        toolkit=toolkit,
        process=process,
        journal=JsonJournalRepository(journal_path),
        filesystem=filesystem,
        prompter=prompter,
        credentials=credentials,
        tunnel_provider=tunnel_provider,
        events=events,
        dry_run=dry_run,
        non_interactive=non_interactive,
    )
