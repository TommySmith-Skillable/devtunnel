"""Builder: assembles the ordered install ``Plan`` from settings + platform.

Because the plan is built as data (a tree of :class:`~devtunnel.domain.plan.Step`
objects) rather than executed inline, ``--dry-run`` is free: the CLI walks the
exact same tree it would otherwise apply and prints each step's
:meth:`~devtunnel.domain.plan.Step.describe_dry_run` instead of calling
``apply``. See :mod:`devtunnel.application.use_cases.install_environment` for
where that walk happens.
"""

from __future__ import annotations

from dataclasses import dataclass

from devtunnel.application import catalog
from devtunnel.application.ports.toolkit import PlatformToolkit
from devtunnel.application.steps import (
    BootstrapPackageManagerStep,
    ConfigureNgrokAuthtokenStep,
    EnsurePackageStep,
    EnsureRepositoryStep,
    EnsureServiceStep,
    RunCommandStep,
    SetGitConfigStep,
)
from devtunnel.domain.models import GitIdentity, PackageSpec, PlatformId
from devtunnel.domain.plan import Plan, StepGroup


@dataclass(frozen=True, slots=True)
class InstallSettings:
    """Everything the plan needs to know, already resolved by the CLI layer.

    Resolution (env var / flag / prompt / --skip) happens before this point;
    the builder itself makes no I/O decisions.
    """

    git_identity: GitIdentity
    ngrok_authtoken: str | None
    skip_packages: frozenset[str] = frozenset()


class PlanBuilder:
    """Composes a :class:`~devtunnel.domain.plan.Plan` for one platform."""

    def __init__(self, toolkit: PlatformToolkit) -> None:
        self._toolkit = toolkit

    def build_install_plan(self, settings: InstallSettings) -> Plan:
        groups: list[StepGroup] = []

        if self._toolkit.platform is PlatformId.WINDOWS:
            groups.append(self._bootstrap_group())

        groups.append(self._package_group("git", catalog.GIT, settings))
        if not settings.git_identity.is_empty:
            groups.append(self._git_identity_group(settings.git_identity))

        if self._toolkit.repository_provider is not None:
            groups.append(self._ngrok_repository_group(settings))

        groups.append(self._package_group("ngrok", catalog.NGROK, settings))
        if settings.ngrok_authtoken:
            groups.append(self._ngrok_authtoken_group(settings.ngrok_authtoken))

        groups.append(self._package_group("openssh-client", catalog.OPENSSH_CLIENT, settings))
        groups.append(self._package_group("openssh-server", catalog.OPENSSH_SERVER, settings))
        groups.append(self._sshd_group())

        return Plan("devtunnel install", [g for g in groups if g.children])

    # -- individual groups ---------------------------------------------

    def _bootstrap_group(self) -> StepGroup:
        step = BootstrapPackageManagerStep(
            "bootstrap-package-manager",
            "Install Chocolatey",
            target="chocolatey",
        )
        return StepGroup("Package manager", [step])

    def _package_group(
        self, step_id: str, package: PackageSpec, settings: InstallSettings
    ) -> StepGroup:
        if package.key in settings.skip_packages:
            return StepGroup(f"Install {package.display_name} (skipped)", [])
        step = EnsurePackageStep(step_id, f"Install {package.display_name}", package)
        return StepGroup(f"Install {package.display_name}", [step])

    def _git_identity_group(self, identity: GitIdentity) -> StepGroup:
        children = []
        if identity.name is not None:
            children.append(
                SetGitConfigStep(
                    "git-user-name", "Set git user.name", "user.name", identity.name
                )
            )
        if identity.email is not None:
            children.append(
                SetGitConfigStep(
                    "git-user-email", "Set git user.email", "user.email", identity.email
                )
            )
        return StepGroup("Configure git identity", children)

    def _ngrok_repository_group(self, settings: InstallSettings) -> StepGroup:
        children: list = []
        if catalog.GPG.key not in settings.skip_packages:
            children.append(EnsurePackageStep("ngrok-repo-gpg", "Install gpg", catalog.GPG))
        children.append(
            EnsureRepositoryStep("ngrok-apt-repo", "Register the ngrok apt repository")
        )
        children.append(
            RunCommandStep(
                "apt-update-after-ngrok-repo",
                "Refresh apt package index",
                ["apt-get", "update"],
            )
        )
        return StepGroup("Add ngrok apt repository", children)

    def _ngrok_authtoken_group(self, token: str) -> StepGroup:
        step = ConfigureNgrokAuthtokenStep(
            "ngrok-authtoken", "Configure ngrok authtoken", token
        )
        return StepGroup("Configure ngrok", [step])

    def _sshd_group(self) -> StepGroup:
        step = EnsureServiceStep("sshd-enable", "Start and enable sshd", catalog.SSHD)
        return StepGroup("Enable SSH service", [step])
