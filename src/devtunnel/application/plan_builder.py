"""Builder: assembles the ordered install ``Plan`` from settings + platform.

Because the plan is built as data (a tree of :class:`~devtunnel.domain.plan.Step`
objects) rather than executed inline, ``--dry-run`` is free: the CLI walks the
exact same tree it would otherwise apply and prints each step's
:meth:`~devtunnel.domain.plan.Step.describe_dry_run` instead of calling
``apply``.

It also makes the elevation check honest. The use case no longer asserts
"installs need Administrator" up front -- it asks the built plan whether it
contains a single ``Scope.MACHINE`` step, and only then insists. After D1, D2
and D5 the default plan contains none, so the common case runs unprivileged.
That is the single change that makes an unprivileged install possible, and it
works precisely because the plan is inspectable before it is run.

Four shapes come out of here: server (the default), client, ``--with-git``,
and ``--system``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from devtunnel.application import catalog
from devtunnel.application.pairing import PairingBundle, decode_bundle, normalise_nodekey
from devtunnel.application.paths import DevtunnelPaths
from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.toolkit import PlatformToolkit
from devtunnel.application.steps import (
    AddToPathStep,
    BootstrapPackageManagerStep,
    EnsureAllowlistEntryStep,
    EnsureAuthorizedKeysStep,
    EnsurePackageStep,
    EnsurePeerStep,
    EnsureSshKeypairStep,
    GenerateTailcatKeyStep,
    InstallTailcatBinaryStep,
    JournaledStep,
    SetGitConfigStep,
)
from devtunnel.domain.models import GitIdentity, PackageSpec, PlatformId, Role, Scope
from devtunnel.domain.plan import Plan, Step, StepGroup


@dataclass(frozen=True, slots=True)
class InstallSettings:
    """Everything the plan needs to know, already resolved by the CLI layer.

    Resolution (flag / config file / prompt / ``--skip``) happens before this
    point; the builder itself makes no I/O decisions. There is no
    environment-variable tier: it existed only to carry a vendor secret, and
    tailcat has none.
    """

    role: Role = Role.SERVER
    with_git: bool = False
    git_identity: GitIdentity = field(default_factory=GitIdentity)
    tailcat_version: str = catalog.TAILCAT_VERSION
    key_name: str = catalog.DEFAULT_SERVER_KEY
    region: str | None = None
    fixed_region: bool = False
    authorized_keys: tuple[str, ...] = ()
    allow: tuple[str, ...] = ()
    peers: tuple[str, ...] = ()
    ssh_identity: str | None = None
    add_to_path: bool = False
    system_scope: bool = False
    skip_packages: frozenset[str] = frozenset()

    @property
    def scope(self) -> Scope:
        return Scope.MACHINE if self.system_scope else Scope.USER


class PlanBuilder:
    """Composes a :class:`~devtunnel.domain.plan.Plan` for one platform."""

    def __init__(
        self,
        toolkit: PlatformToolkit,
        paths: DevtunnelPaths,
        process: ProcessRunnerPort | None = None,
    ) -> None:
        self._toolkit = toolkit
        self._paths = paths
        # Only consulted to decide whether the OpenSSH client step is needed
        # at all; a builder without it conservatively plans the step.
        self._process = process

    def build_install_plan(self, settings: InstallSettings) -> Plan:
        groups: list[StepGroup] = []

        if settings.with_git:
            groups.extend(self._git_groups(settings))

        if settings.role is Role.CLIENT:
            groups.extend(self._client_groups(settings))
        else:
            groups.extend(self._server_groups(settings))

        if settings.add_to_path:
            groups.append(self._add_to_path_group())

        return Plan("devtunnel install", [g for g in groups if g.children])

    # -- shapes ----------------------------------------------------------

    def _server_groups(self, settings: InstallSettings) -> list[StepGroup]:
        groups = [
            self._binary_group(settings),
            StepGroup(
                "Configure SSH access",
                [
                    EnsureAuthorizedKeysStep(
                        "ensure-authorized-keys",
                        "Install the authorized SSH keys",
                        settings.authorized_keys,
                    )
                ]
                if settings.authorized_keys
                else [],
            ),
            StepGroup(
                "Configure tailcat identity",
                [
                    GenerateTailcatKeyStep(
                        "generate-tailcat-key",
                        f"Generate the tailcat key {settings.key_name!r}",
                        settings.key_name,
                        region=settings.region,
                        fixed_region=settings.fixed_region,
                    )
                ],
            ),
        ]

        peer_steps = self._peer_steps(settings)
        if peer_steps:
            groups.append(StepGroup("Configure peers", peer_steps))
        return groups

    def _client_groups(self, settings: InstallSettings) -> list[StepGroup]:
        groups: list[StepGroup] = []

        # tailcat ssh wraps the *stock* ssh client, so the client role needs
        # ssh to connect and ssh-keygen to create an identity -- planned only
        # when ssh-keygen is actually missing, which on a normal machine it is
        # not. This is the one remaining caller of WindowsCapabilityManager.
        if self._needs_openssh_client(settings):
            groups.append(
                StepGroup(
                    "Install OpenSSH client",
                    [
                        EnsurePackageStep(
                            "openssh-client",
                            "Install the OpenSSH client",
                            catalog.OPENSSH_CLIENT,
                        )
                    ],
                )
            )

        groups.append(self._binary_group(settings))
        groups.append(
            StepGroup(
                "Configure tailcat identity",
                [
                    GenerateTailcatKeyStep(
                        "generate-tailcat-key",
                        f"Generate the tailcat client key {settings.key_name!r}",
                        settings.key_name,
                        client=True,
                        region=settings.region,
                        fixed_region=settings.fixed_region,
                    )
                ],
            )
        )
        groups.append(
            StepGroup(
                "Configure SSH identity",
                [
                    EnsureSshKeypairStep(
                        "ensure-ssh-keypair",
                        "Create or adopt an SSH identity",
                        identity_path=settings.ssh_identity,
                    )
                ],
            )
        )
        return groups

    def _git_groups(self, settings: InstallSettings) -> list[StepGroup]:
        """The one path that still requires elevation (D5).

        Installing git is the only remaining machine-scope step. Leaving it in
        the default plan would single-handedly force Administrator/sudo on an
        otherwise unprivileged install, so it is opt-in -- and it drags the
        Chocolatey bootstrap along with it on Windows.
        """

        groups: list[StepGroup] = []
        if self._toolkit.platform is PlatformId.WINDOWS:
            groups.append(
                StepGroup(
                    "Package manager",
                    [
                        BootstrapPackageManagerStep(
                            "bootstrap-package-manager", "Install Chocolatey", "chocolatey"
                        )
                    ],
                )
            )
        groups.append(self._package_group("git", catalog.GIT, settings))
        if not settings.git_identity.is_empty:
            groups.append(self._git_identity_group(settings.git_identity))
        return groups

    # -- individual groups -----------------------------------------------

    def _binary_group(self, settings: InstallSettings) -> StepGroup:
        step = InstallTailcatBinaryStep(
            "install-tailcat-binary",
            f"Install tailcat {settings.tailcat_version}",
            self._paths.binary_path,
            scope=settings.scope,
        )
        return StepGroup("Install tailcat", [step])

    def _peer_steps(self, settings: InstallSettings) -> list[Step | StepGroup]:
        steps: list[Step | StepGroup] = []
        for index, bundle in enumerate(self._bundles(settings)):
            steps.append(
                EnsurePeerStep(
                    f"ensure-peer-{index}", f"Authorise peer {bundle.name!r}", bundle
                )
            )
        # A bare --allow admits a peer to the tunnel without giving it a
        # shell. It still gets a record: an allowlist entry devtunnel added
        # and cannot name is an allowlist entry uninstall will leave behind.
        for index, raw in enumerate(settings.allow):
            node_key = normalise_nodekey(raw)
            steps.append(
                EnsureAllowlistEntryStep(
                    f"ensure-allow-{index}", f"Allow {node_key[:24]}...", node_key
                )
            )
        return steps

    @staticmethod
    def _bundles(settings: InstallSettings) -> list[PairingBundle]:
        """Decode every ``--peer`` bundle up front.

        A malformed bundle must fail while the plan is still being built, not
        half way through applying it -- the plan is also what ``--dry-run``
        renders, and a dry run that hides a bad bundle would be worse than
        useless.
        """

        return [decode_bundle(token) for token in settings.peers]

    def _package_group(
        self, step_id: str, package: PackageSpec, settings: InstallSettings
    ) -> StepGroup:
        if package.key in settings.skip_packages:
            return StepGroup(f"Install {package.display_name} (skipped)", [])
        step = EnsurePackageStep(step_id, f"Install {package.display_name}", package)
        return StepGroup(f"Install {package.display_name}", [step])

    def _git_identity_group(self, identity: GitIdentity) -> StepGroup:
        children: list[Step | StepGroup] = []
        if identity.name is not None:
            children.append(
                SetGitConfigStep("git-user-name", "Set git user.name", "user.name", identity.name)
            )
        if identity.email is not None:
            children.append(
                SetGitConfigStep(
                    "git-user-email", "Set git user.email", "user.email", identity.email
                )
            )
        return StepGroup("Configure git identity", children)

    def _add_to_path_group(self) -> StepGroup:
        import os

        directory = os.path.dirname(self._paths.binary_path)
        return StepGroup(
            "Add tailcat to PATH",
            [AddToPathStep("add-to-path", "Add tailcat to PATH", directory)],
        )

    def _needs_openssh_client(self, settings: InstallSettings) -> bool:
        if catalog.OPENSSH_CLIENT.key in settings.skip_packages:
            return False
        if self._process is None:
            return True
        return self._process.which("ssh-keygen") is None


def plan_requires_elevation(plan: Plan) -> bool:
    """Whether any step in ``plan`` writes machine-scope state.

    Asked by ``InstallEnvironmentUseCase`` in place of the old unconditional
    ``is_elevated()`` assertion. A plan of purely user-scope steps -- which is
    now the default -- must not demand privileges it will never use.
    """

    return any(
        isinstance(step, JournaledStep) and step.scope is Scope.MACHINE for step in plan.walk()
    )
