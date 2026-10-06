"""Composition root: the one place allowed to know about every layer.

Detects the platform, wires the concrete adapters into an
:class:`~devtunnel.application.context.ExecutionContext`, and hands that back
to the CLI. Nothing above this module imports from ``infrastructure`` --
that is what keeps ``application`` free to be tested against fakes.

Two wiring decisions are worth calling out, because both are where the
migration's design actually lands:

* **Journals are scope-aware** (D6). The context is handed one journal per
  scope. A user-scope install writes under the user's own data directory and
  never needs elevation to record what it did; only the ``--with-git`` and
  ``--system`` paths touch the machine journal.
* **The service's command comes from ``build_argv``.** The systemd unit and
  the Scheduled Task both need the exact ``tailcat serve`` invocation, and
  there is precisely one method in the codebase allowed to construct it. The
  alternative -- spelling the argv out a second time in a unit template --
  would guarantee the two drift the first time tailcat's pre-1.0 CLI moves.
"""

from __future__ import annotations

import os

from devtunnel.application import catalog
from devtunnel.application.context import ExecutionContext
from devtunnel.application.paths import DevtunnelPaths, resolve_paths
from devtunnel.application.ports.journal_repo import JournalRepositoryPort
from devtunnel.application.ports.toolkit import PlatformToolkit
from devtunnel.cli.presenters import JsonPresenter, RichPresenter
from devtunnel.cli.prompter import RichPrompter
from devtunnel.domain.events import EventBus
from devtunnel.domain.models import PlatformId, Scope, TunnelSpec
from devtunnel.infrastructure.filesystem import LocalFileSystem
from devtunnel.infrastructure.journal.json_journal import JsonJournalRepository
from devtunnel.infrastructure.platform_detect import detect_platform
from devtunnel.infrastructure.process.dry_run_runner import DryRunProcessRunner
from devtunnel.infrastructure.process.subprocess_runner import SubprocessProcessRunner
from devtunnel.infrastructure.release.github_release import GitHubReleaseInstaller
from devtunnel.infrastructure.tailcat.tailcat_keys import TailcatKeys
from devtunnel.infrastructure.tailcat.tailcat_tunnel import TailcatTunnelProvider
from devtunnel.infrastructure.toolkits import DebianToolkit, WindowsToolkit


def _journal_paths(platform: PlatformId, paths: DevtunnelPaths) -> dict[Scope, str]:
    return {
        Scope.USER: os.path.join(paths.user_state_dir, "journal.json"),
        Scope.MACHINE: os.path.join(paths.machine_state_dir, "journal.json"),
    }


def build_context(
    *,
    dry_run: bool = False,
    non_interactive: bool = False,
    json_output: bool = False,
    system_scope: bool = False,
    tailcat_version: str | None = None,
    key_name: str = catalog.DEFAULT_SERVER_KEY,
) -> ExecutionContext:
    platform = detect_platform()

    real_process = SubprocessProcessRunner()
    process = DryRunProcessRunner(real_process) if dry_run else real_process

    scope = Scope.MACHINE if system_scope else Scope.USER
    # Backups live beside the user journal: the files devtunnel backs up
    # (authorized_keys, the allowlist, ~/.profile) are all the user's own, and
    # a backup of a user file has no business in a system-owned directory.
    bootstrap_fs = LocalFileSystem(os.path.join(_user_state_dir_guess(platform), "backups"))
    paths = resolve_paths(platform, bootstrap_fs, scope=scope)
    filesystem = LocalFileSystem(os.path.join(paths.user_state_dir, "backups"))

    keys = TailcatKeys(process, filesystem, binary_path=paths.binary_path)
    tunnel_provider = TailcatTunnelProvider(process, keys, binary_path=paths.binary_path)

    # The one construction of the serve argv, reused verbatim by the service.
    tunnel_command = tunnel_provider.build_argv(
        TunnelSpec(
            serve=("ssh",),
            key_name=key_name,
            authorized_keys=(paths.authorized_keys_path,),
        )
    )

    toolkit: PlatformToolkit
    if platform is PlatformId.WINDOWS:
        toolkit = WindowsToolkit(process, tunnel_command=tunnel_command)
    else:
        toolkit = DebianToolkit(process, filesystem, tunnel_command=tunnel_command)

    journals: dict[Scope, JournalRepositoryPort] = {
        s: JsonJournalRepository(path) for s, path in _journal_paths(platform, paths).items()
    }

    binary_installer = GitHubReleaseInstaller(
        process,
        filesystem,
        repo=catalog.TAILCAT_REPO,
        version=tailcat_version or catalog.TAILCAT_VERSION,
        cache_dir=paths.cache_dir,
    )

    events = EventBus()
    events.subscribe(JsonPresenter() if json_output else RichPresenter())

    return ExecutionContext(
        toolkit=toolkit,
        process=process,
        journals=journals,
        filesystem=filesystem,
        prompter=RichPrompter(),
        tunnel_provider=tunnel_provider,
        binary_installer=binary_installer,
        paths=paths,
        events=events,
        dry_run=dry_run,
        non_interactive=non_interactive,
    )


def _user_state_dir_guess(platform: PlatformId) -> str:
    """Where backups go before ``resolve_paths`` has run.

    A chicken-and-egg: ``LocalFileSystem`` needs a backups directory, and
    resolving the real one needs a filesystem to ask for the real user's home.
    This first, throwaway instance only ever answers ``real_user_home()``, so
    the guess it is built with is never actually written to.
    """

    if platform is PlatformId.WINDOWS:
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "devtunnel")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "devtunnel")
