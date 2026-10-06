"""Concrete, reusable :class:`~devtunnel.domain.plan.Step` implementations.

:class:`JournaledStep` is the Template Method that fixes the
``detect -> skip-or-apply -> journal`` skeleton: a concrete step supplies only
``already_satisfied``, ``perform`` and ``undo``; the base class guarantees
every mutation is written to the journal as ``PENDING`` *before* it happens
and flipped to ``APPLIED``/``SKIPPED_PREEXISTING`` afterwards. No subclass can
forget to journal, because the journal calls live in the base class, not in
the override points. That skeleton is unchanged by the tailcat migration --
only the library of concrete steps underneath it is new.

What changed is that a step now declares a :class:`~devtunnel.domain.models.Scope`.
A ``USER``-scope step writes to the user journal and never needs elevation; a
``MACHINE``-scope step writes to the machine journal and does. The default is
``USER``, because after D1/D2/D5 almost nothing devtunnel installs is
system-wide any more.
"""

from __future__ import annotations

import os
import socket
from abc import abstractmethod
from dataclasses import replace

from devtunnel.application.allowlist import Allowlist
from devtunnel.application.context import ExecutionContext
from devtunnel.application.pairing import PairingBundle
from devtunnel.application.paths import CANDIDATE_SSH_IDENTITIES
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.errors import StepFailedError
from devtunnel.domain.events import StepEvent
from devtunnel.domain.journal import ChangeKind, ChangeRecord
from devtunnel.domain.models import PackageSpec, PlatformId, Scope, ServiceSpec
from devtunnel.domain.plan import Step, StepOutcome, StepStatus

_SSH_KEY_TYPES = (
    "ssh-ed25519",
    "ssh-rsa",
    "ecdsa-sha2-nistp256",
    "ecdsa-sha2-nistp384",
    "ecdsa-sha2-nistp521",
    "sk-ssh-ed25519@openssh.com",
    "sk-ecdsa-sha2-nistp256@openssh.com",
)

_ADOPTABLE_IDENTITIES = CANDIDATE_SSH_IDENTITIES
"""Re-exported under the local name this module already used. The order lives
in :mod:`devtunnel.application.paths` so that the step's adopt decision and
``DevtunnelPaths.resolve_ssh_identity`` can never disagree about which key this
machine offers -- when they did, install adopted ``id_rsa`` and every reader
went looking for ``id_ed25519``."""
"""Checked in this order. ed25519 first because it is what devtunnel would
generate, so a machine devtunnel has already touched adopts its own key."""


class JournaledStep(Step):
    """Template Method base: every subclass gets journaling for free."""

    kind: ChangeKind
    scope: Scope = Scope.USER

    def __init__(
        self, step_id: str, description: str, target: str, *, scope: Scope | None = None
    ) -> None:
        super().__init__(step_id, description)
        self.target = target
        if scope is not None:
            self.scope = scope

    # -- override points -------------------------------------------------

    @abstractmethod
    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        """True if the desired state already holds and nothing should change."""

    @abstractmethod
    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        """Apply the change. Returns ``(prior_state, details)`` to journal."""

    @abstractmethod
    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        """Reverse the change described by ``record``."""

    # -- template ----------------------------------------------------------

    def apply(self, ctx: ExecutionContext) -> StepOutcome:
        self._notify(ctx, "started")

        if ctx.dry_run:
            dry_run_message = self.describe_dry_run()
            self._notify(ctx, "dry_run", self._detail_or_blank(dry_run_message))
            return StepOutcome(StepStatus.DRY_RUN, dry_run_message)

        journal = ctx.journal_for(self.scope)

        if self.already_satisfied(ctx):
            record = ChangeRecord.pending(
                self.kind, self.target, details=self._scope_details()
            ).skipped_preexisting()
            journal.append(record)
            self._notify(ctx, "skipped", "already present")
            return StepOutcome(StepStatus.SKIPPED, "already present", record)

        record = ChangeRecord.pending(self.kind, self.target, details=self._scope_details())
        journal.append(record)  # PENDING before the mutation: crash-safe
        try:
            prior_state, details = self.perform(ctx)
        except Exception as exc:  # noqa: BLE001 - deliberately broad; re-raised typed
            self._notify(ctx, "failed", str(exc))
            raise StepFailedError(self.description, str(exc)) from exc

        applied = replace(record, prior_state=prior_state).applied(details=details)
        journal.update(applied)
        self._notify(ctx, "applied")
        return StepOutcome(StepStatus.APPLIED, "", applied)

    def revert(self, ctx: ExecutionContext, record: ChangeRecord) -> StepOutcome:
        if not record.needs_revert:
            return StepOutcome(StepStatus.SKIPPED, "nothing to revert")

        if ctx.dry_run:
            msg = f"would revert {record.target}"
            self._notify(ctx, "dry_run", self._detail_or_blank(msg))
            return StepOutcome(StepStatus.DRY_RUN, msg)

        self._notify(ctx, "started")
        journal = ctx.journal_for(self.scope)
        try:
            self.undo(ctx, record)
        except Exception as exc:  # noqa: BLE001
            failed = record.revert_failed(reason=str(exc))
            journal.update(failed)
            self._notify(ctx, "revert_failed", str(exc))
            return StepOutcome(StepStatus.REVERT_FAILED, str(exc), failed)

        reverted = record.reverted()
        journal.update(reverted)
        self._notify(ctx, "reverted")
        return StepOutcome(StepStatus.REVERTED, "", reverted)

    def _scope_details(self) -> dict:
        """Stamp the scope into every record.

        Uninstall reads this to decide whether a given revert needs elevation,
        without having to re-derive which journal file it came out of.
        """

        return {"scope": self.scope.value}

    def _notify(self, ctx: ExecutionContext, status: str, detail: str = "") -> None:
        ctx.events.publish(StepEvent(ctx.phase, self.id, self.description, status, detail))

    def _detail_or_blank(self, detail: str) -> str:
        """Avoid presenting a detail that just repeats the description."""
        return "" if detail == self.description else detail


# --------------------------------------------------------------------------
# Packages, services, git -- carried over unchanged in shape
# --------------------------------------------------------------------------


class BootstrapPackageManagerStep(JournaledStep):
    """Installs the platform's package manager itself (Chocolatey on
    Windows; a no-op on Debian, which ships with apt).

    Machine-scope, and now only ever planned behind ``--with-git`` -- it is
    part of the one remaining path that requires elevation.
    """

    kind = ChangeKind.PKGMGR_BOOTSTRAPPED
    scope = Scope.MACHINE

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return ctx.toolkit.package_bootstrap.is_bootstrapped()

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        details = ctx.toolkit.package_bootstrap.bootstrap()
        return {}, details

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        ctx.toolkit.package_bootstrap.teardown(record.details)


class EnsurePackageStep(JournaledStep):
    """Ensures a single named package/capability is present.

    Two callers remain: git (machine-scope, ``--with-git``) and the OpenSSH
    *client* on the client role. The latter is a Windows capability rather
    than a Chocolatey package, which is exactly the asymmetry
    ``PackageManagerPort`` exists to hide.
    """

    kind = ChangeKind.PACKAGE_INSTALLED
    scope = Scope.MACHINE

    def __init__(
        self,
        step_id: str,
        description: str,
        package: PackageSpec,
        *,
        scope: Scope | None = None,
    ) -> None:
        super().__init__(step_id, description, target=f"package:{package.key}", scope=scope)
        self.package = package

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return ctx.toolkit.manager_for(self.package.key).is_installed(self.package)

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        ctx.toolkit.manager_for(self.package.key).install(self.package)
        return {}, {"package_key": self.package.key}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        ctx.toolkit.manager_for(self.package.key).uninstall(self.package)


class EnsureServiceStep(JournaledStep):
    """Registers a service, starts it, and sets it to launch automatically,
    capturing whatever state it had before so uninstall restores exactly that.

    Unchanged by the migration except that it now asks the toolkit for the
    manager matching the spec's own scope -- so the same step drives a systemd
    *user* unit, a per-user Scheduled Task, a system unit or a Windows service
    without knowing which it got.
    """

    kind = ChangeKind.SERVICE_STATE_CHANGED

    def __init__(self, step_id: str, description: str, service: ServiceSpec) -> None:
        super().__init__(
            step_id, description, target=f"service:{service.key}", scope=service.scope
        )
        self.service = service

    def _manager(self, ctx: ExecutionContext):
        return ctx.toolkit.service_manager_for(self.service.scope)

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        state = self._manager(ctx).get_state(self.service)
        return state.exists and state.running and state.startup_automatic

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        manager = self._manager(ctx)
        prior = manager.get_state(self.service)
        manager.ensure_running_and_enabled(self.service)
        return prior.to_dict(), {"service_key": self.service.key}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        prior = ServiceState.from_dict(record.prior_state)
        self._manager(ctx).apply_state(self.service, prior)


class SetGitConfigStep(JournaledStep):
    """Sets one ``git config --global`` key, capturing its prior value (or
    absence) so revert restores exactly that -- never a blind overwrite of
    the user's existing git identity."""

    kind = ChangeKind.CONFIG_KEY_SET
    scope = Scope.MACHINE

    def __init__(self, step_id: str, description: str, git_key: str, value: str) -> None:
        super().__init__(step_id, description, target=f"gitconfig:{git_key}")
        self.git_key = git_key
        self.value = value

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return self._read(ctx) == self.value

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        prior = self._read(ctx)
        ctx.process.run(["git", "config", "--global", self.git_key, self.value])
        return {"had_value": prior is not None, "value": prior}, {}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        if record.prior_state.get("had_value"):
            ctx.process.run(
                ["git", "config", "--global", self.git_key, record.prior_state["value"]]
            )
        else:
            ctx.process.run(
                ["git", "config", "--global", "--unset", self.git_key], check=False
            )

    def _read(self, ctx: ExecutionContext) -> str | None:
        result = ctx.process.run(
            ["git", "config", "--global", "--get", self.git_key], check=False
        )
        value = result.stdout.strip()
        return value if result.ok and value else None


# --------------------------------------------------------------------------
# tailcat
# --------------------------------------------------------------------------


class InstallTailcatBinaryStep(JournaledStep):
    """Downloads, verifies and unpacks the tailcat executable.

    The checksum check lives in the adapter rather than here, because aborting
    *before anything is written* is a property of the download mechanism, not
    of the plan. What this step owns is the promise the rest of the project
    already makes: a tailcat the user installed themselves is detected, used,
    recorded as ``SKIPPED_PREEXISTING`` and never removed by ``uninstall``.
    """

    kind = ChangeKind.BINARY_INSTALLED

    def __init__(
        self,
        step_id: str,
        description: str,
        target_path: str,
        *,
        scope: Scope = Scope.USER,
        binary_name: str = "tailcat",
    ) -> None:
        super().__init__(step_id, description, target="binary:tailcat", scope=scope)
        self.target_path = target_path
        self.binary_name = binary_name

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        if ctx.binary_installer.is_installed(self.target_path):
            return True
        # A tailcat the user already has on PATH is theirs, not ours.
        return ctx.process.which(self.binary_name) is not None

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        details = ctx.binary_installer.install(self.target_path)
        return {}, details

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        ctx.binary_installer.uninstall(record.details)


class GenerateTailcatKeyStep(JournaledStep):
    """Creates a tailcat keypair via ``tailcat genkey``.

    The journaling contract is deliberately narrow: ``generate_key`` returns
    the prior state, ``remove_key`` consumes exactly that, and this step never
    has to know what "prior state" means for the provider underneath.

    Only *public* material reaches the journal -- the address and the node key.
    The private key at ``<config-dir>/tailcat/keys/<name>.private.json`` is never
    read, never copied and never backed up (see plan section 13.6).
    """

    kind = ChangeKind.KEY_GENERATED

    def __init__(
        self,
        step_id: str,
        description: str,
        key_name: str,
        *,
        client: bool = False,
        region: str | None = None,
        fixed_region: bool = False,
    ) -> None:
        super().__init__(step_id, description, target=f"tailcatkey:{key_name}")
        self.key_name = key_name
        self.client = client
        self.region = region
        self.fixed_region = fixed_region

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return ctx.tunnel_provider.has_key(self.key_name)

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        prior_state = ctx.tunnel_provider.generate_key(
            self.key_name,
            client=self.client,
            region=self.region,
            fixed_region=self.fixed_region,
        )
        details = {"key_name": self.key_name, "client": self.client}
        # Cache the public identifiers genkey printed, so `up`/`serve` can
        # render a connection command without starting anything.
        for public_field in ("address", "node_key"):
            if prior_state.get(public_field):
                details[public_field] = prior_state[public_field]
        return prior_state, details

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        ctx.tunnel_provider.remove_key(record.prior_state)


# --------------------------------------------------------------------------
# SSH identity and the authentication boundary
# --------------------------------------------------------------------------


class EnsureSshKeypairStep(JournaledStep):
    """Client role: make sure this machine has an SSH identity to offer.

    **Adopt, don't clobber.** If a usable default identity already exists it is
    used as-is and nothing is journaled, which means ``uninstall`` can never
    delete a key the user had before devtunnel ran. Only a keypair devtunnel
    itself generated is ever removed -- the same ``SKIPPED_PREEXISTING``
    guarantee the project already makes for packages, applied to the one piece
    of state where getting it wrong would be unrecoverable.

    Generated keys use an empty passphrase so unattended ``serve``/``connect``
    work; an interactive user who wants one passes a passphrase and accepts
    that an agent is then required for non-interactive connects.

    ``identity_path`` selects or generates a *dedicated* key rather than the
    default one. Note the asymmetry, resolved as an M0 task (plan section 20.5):
    creating a key at a custom path works fine, but ``tailcat ssh`` documents no
    ``-i`` passthrough to the stock ssh client it wraps, so devtunnel cannot ask
    for that identity at connect time. ``devtunnel connect`` therefore refuses
    ``--ssh-identity`` outright instead of connecting as the wrong key. The
    supported route for a non-default identity is ``ssh-agent``.
    """

    kind = ChangeKind.FILE_CREATED

    def __init__(
        self,
        step_id: str,
        description: str,
        *,
        identity_path: str | None = None,
        passphrase: str = "",
    ) -> None:
        super().__init__(step_id, description, target="file:ssh_identity")
        self.identity_path = identity_path
        self.passphrase = passphrase

    def _target_path(self, ctx: ExecutionContext) -> str:
        return self.identity_path or ctx.paths.default_ssh_identity()

    def adopted_identity(self, ctx: ExecutionContext) -> str | None:
        """An existing private key devtunnel should use rather than replace."""

        if self.identity_path is not None:
            return self.identity_path if ctx.filesystem.exists(self.identity_path) else None
        for name in _ADOPTABLE_IDENTITIES:
            candidate = os.path.join(ctx.paths.ssh_dir, name)
            if ctx.filesystem.exists(candidate):
                return candidate
        return None

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return self.adopted_identity(ctx) is not None

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        path = self._target_path(ctx)
        ctx.filesystem.ensure_dir(ctx.paths.ssh_dir)
        comment = f"devtunnel {self._user()}@{self._hostname()}"
        ctx.process.run(
            [
                "ssh-keygen",
                "-t",
                "ed25519",
                "-f",
                path,
                "-N",
                self.passphrase,
                "-C",
                comment,
            ]
        )
        ctx.filesystem.take_ownership_for_real_user(path)
        public_key = (ctx.filesystem.read_text(f"{path}.pub") or "").strip()
        return (
            {"existed": False},
            # The public half and the path only. The private key is never read.
            {"generated": True, "path": path, "public_key": public_key, "comment": comment},
        )

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        if not record.details.get("generated"):
            return  # adopted, never ours to delete
        path = record.details["path"]
        ctx.filesystem.remove_file(path)
        ctx.filesystem.remove_file(f"{path}.pub")

    @staticmethod
    def _user() -> str:
        return os.environ.get("SUDO_USER") or os.environ.get("USER") or (
            os.environ.get("USERNAME") or "user"
        )

    @staticmethod
    def _hostname() -> str:
        try:
            return socket.gethostname()
        except OSError:
            return "host"


class EnsureAuthorizedKeysStep(JournaledStep):
    """Writes the SSH authentication boundary.

    Under D1 tailcat serves SSH itself, so there is no sshd config, no PAM and
    no ``AllowUsers`` -- ``authorized_keys`` is the *entire* boundary. That is
    why this is a first-class journaled step rather than an incidental file
    write, and why it is paranoid about four specific things: it always backs
    up what was there, it **appends** rather than overwrites, it
    de-duplicates, and it sets ``0600`` on the file and ``0700`` on ``~/.ssh``.
    Widening access by accident here is the worst thing this tool could do.

    Entries naming a GitHub user (``tommy@github``) are *not* materialised into
    the file: tailcat fetches ``github.com/<user>.keys`` natively at serve
    time, and copying them in would freeze a set the user expects to stay live.
    They are recorded in the journal and passed through to the serve flags.
    """

    kind = ChangeKind.FILE_MODIFIED

    def __init__(self, step_id: str, description: str, entries: tuple[str, ...]) -> None:
        super().__init__(step_id, description, target="file:authorized_keys")
        self.entries = entries

    def resolve(self, ctx: ExecutionContext) -> tuple[list[str], list[str]]:
        """Split the configured entries into (key lines, delegated sources)."""

        key_lines: list[str] = []
        delegated: list[str] = []
        for entry in self.entries:
            value = entry.strip()
            if not value:
                continue
            if value.endswith("@github"):
                delegated.append(value)
            elif value.startswith(_SSH_KEY_TYPES):
                key_lines.append(value)
            else:
                content = ctx.filesystem.read_text(value)
                if content is None:
                    raise FileNotFoundError(f"authorized-keys source not found: {value}")
                key_lines.extend(
                    line.strip()
                    for line in content.splitlines()
                    if line.strip() and not line.lstrip().startswith("#")
                )
        return key_lines, delegated

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        key_lines, delegated = self.resolve(ctx)
        if not key_lines and not delegated:
            return True
        if delegated:
            # A GitHub source writes nothing to the file, but it is still a
            # decision about who may log in -- so it gets a record, and the
            # step is not "already satisfied" just because the file is quiet.
            return False
        existing = set(_lines_of(ctx.filesystem.read_text(ctx.paths.authorized_keys_path)))
        return all(line in existing for line in key_lines)

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        key_lines, delegated = self.resolve(ctx)
        path = ctx.paths.authorized_keys_path
        prior = _append_lines(ctx, path, key_lines)
        return prior, {"added": key_lines, "delegated": delegated, "path": path}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        _restore_file(ctx, record)


class EnsurePeerStep(JournaledStep):
    """Installs **both** halves of one peer's identity, as one record.

    A peer needs two things before it can get a shell: its node key in the
    allowlist (may it open a tunnel at all) and its SSH key in
    ``authorized_keys`` (may it log in). Splitting those into two independent
    steps would allow a half-reverted peer, and the two halves fail very
    differently: allow-listed but not authorized is a tunnel that refuses to
    log in -- annoying; authorized but not allow-listed leaves a working shell
    credential on the box for someone who was supposed to lose access --
    which is precisely the residue this project exists to prevent.

    So: one step, one journal record, one revert, both halves. If a revert can
    only remove one of them it raises rather than reporting success, because a
    partial revoke that *looks* clean is the failure mode to engineer against.
    """

    kind = ChangeKind.FILE_MODIFIED

    def __init__(self, step_id: str, description: str, bundle: PairingBundle) -> None:
        super().__init__(step_id, description, target=f"peer:{bundle.name}")
        self.bundle = bundle

    def _allowlist(self, ctx: ExecutionContext) -> Allowlist:
        return Allowlist(ctx.filesystem, ctx.paths.allowlist_path)

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        line = self.bundle.authorized_keys_line().strip()
        authorized = line in _lines_of(ctx.filesystem.read_text(ctx.paths.authorized_keys_path))
        return self._allowlist(ctx).contains(self.bundle.nodekey) and authorized

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        allowlist = self._allowlist(ctx)
        allowlist_backup = ctx.filesystem.backup(allowlist.path)
        added_to_allowlist = allowlist.add(self.bundle.nodekey)

        prior = _append_lines(
            ctx,
            ctx.paths.authorized_keys_path,
            [self.bundle.authorized_keys_line().strip()],
        )
        prior["allowlist_backup_path"] = allowlist_backup
        prior["added_to_allowlist"] = added_to_allowlist

        details = {
            "name": self.bundle.name,
            "nodekey": self.bundle.nodekey,
            "sshkey": self.bundle.sshkey,
            "fingerprint": self.bundle.fingerprint,
            "created": self.bundle.created,
        }
        return prior, details

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        failures: list[str] = []

        try:
            if record.prior_state.get("added_to_allowlist", True):
                self._allowlist(ctx).remove(record.details["nodekey"])
        except Exception as exc:  # noqa: BLE001 - reported, never swallowed
            failures.append(f"allowlist entry could not be removed: {exc}")

        try:
            _restore_file(ctx, record)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"authorized_keys line could not be removed: {exc}")

        if failures:
            # Deliberately loud and specific: the operator must be able to see
            # which half of the peer's access is still live.
            raise RuntimeError(
                f"peer {record.details.get('name', '?')!r} was only partially revoked -- "
                + "; ".join(failures)
            )


class EnsureAllowlistEntryStep(JournaledStep):
    """Adds one bare node key to the tunnel allowlist.

    The lower-level half of :class:`EnsurePeerStep`, for the operator who
    genuinely wants to admit a peer to the tunnel without also giving it a
    shell. It is a separate, journaled step rather than a side effect of the
    ``--allow`` flag because an unjournaled mutation would be a hole straight
    through the one promise this tool makes: that everything it changed can be
    named and undone.

    Admitting a peer to the tunnel is strictly weaker than authorising a
    login -- an allow-listed peer with no ``authorized_keys`` entry gets a
    connection and then a password prompt it cannot satisfy -- so this is the
    safe half to hold on its own. The dangerous asymmetry runs the other way,
    which is why there is no matching bare-``authorize`` step.
    """

    kind = ChangeKind.FILE_MODIFIED

    def __init__(self, step_id: str, description: str, node_key: str) -> None:
        super().__init__(step_id, description, target=f"allow:{node_key}")
        self.node_key = node_key

    def _allowlist(self, ctx: ExecutionContext) -> Allowlist:
        return Allowlist(ctx.filesystem, ctx.paths.allowlist_path)

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return self._allowlist(ctx).contains(self.node_key)

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        self._allowlist(ctx).add(self.node_key)
        return {"added": True}, {"nodekey": self.node_key}

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        self._allowlist(ctx).remove(record.details["nodekey"])


class AddToPathStep(JournaledStep):
    """Opt-in: put tailcat's directory on the user's ``PATH``.

    devtunnel never needs this -- it invokes tailcat by the absolute path
    recorded in the journal, which is exactly why installing the binary
    requires no ``PATH`` mutation and therefore no elevation. This step exists
    only for the user who wants to run ``tailcat`` by hand, and is journaled
    separately so it can be reverted on its own.
    """

    kind = ChangeKind.CONFIG_KEY_SET
    _MARKER = "# added by devtunnel"

    def __init__(self, step_id: str, description: str, directory: str) -> None:
        super().__init__(step_id, description, target="path:tailcat")
        self.directory = directory

    def already_satisfied(self, ctx: ExecutionContext) -> bool:
        return self.directory in self._current_path(ctx)

    def perform(self, ctx: ExecutionContext) -> tuple[dict, dict]:
        if ctx.platform is PlatformId.WINDOWS:
            prior = self._read_windows_user_path(ctx)
            updated = f"{prior};{self.directory}" if prior else self.directory
            self._write_windows_user_path(ctx, updated)
            return {"kind": "windows_user_path", "value": prior}, {"directory": self.directory}

        profile = os.path.join(ctx.paths.home, ".profile")
        backup_path = ctx.filesystem.backup(profile)
        existing = ctx.filesystem.read_text(profile) or ""
        line = f'export PATH="{self.directory}:$PATH"  {self._MARKER}\n'
        ctx.filesystem.write_text(profile, existing + line)
        return (
            {"kind": "profile", "path": profile, "backup_path": backup_path,
             "existed": backup_path is not None},
            {"directory": self.directory},
        )

    def undo(self, ctx: ExecutionContext, record: ChangeRecord) -> None:
        prior = record.prior_state
        if prior.get("kind") == "windows_user_path":
            self._write_windows_user_path(ctx, prior.get("value") or "")
            return
        path = prior.get("path")
        if path:
            ctx.filesystem.restore_from_backup(path, prior.get("backup_path"))

    def _current_path(self, ctx: ExecutionContext) -> str:
        if ctx.platform is PlatformId.WINDOWS:
            return self._read_windows_user_path(ctx)
        return os.environ.get("PATH", "")

    @staticmethod
    def _read_windows_user_path(ctx: ExecutionContext) -> str:
        # The user-scope PATH specifically, read through the .NET API rather
        # than %PATH%: the process environment is the *merged* machine+user
        # value, and writing that back would copy the machine PATH into the
        # user's, which `setx` is notorious for doing irreversibly.
        result = ctx.process.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "[Environment]::GetEnvironmentVariable('Path','User')",
            ],
            check=False,
        )
        return result.stdout.strip() if result.ok else ""

    @staticmethod
    def _write_windows_user_path(ctx: ExecutionContext, value: str) -> None:
        escaped = value.replace("'", "''")
        ctx.process.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"[Environment]::SetEnvironmentVariable('Path','{escaped}','User')",
            ]
        )


class RunCommandStep(Step):
    """A plain, non-journaled command (e.g. ``apt-get update``).

    Not every action is a reversible *change* -- refreshing a package index
    has nothing meaningful to undo. This step deliberately does not extend
    ``JournaledStep``: it produces no :class:`ChangeRecord`, and the base
    ``Step.revert`` no-op is exactly correct for it.
    """

    def __init__(
        self, step_id: str, description: str, argv: list[str], *, check: bool = True
    ) -> None:
        super().__init__(step_id, description)
        self.argv = argv
        self.check = check

    def apply(self, ctx: ExecutionContext) -> StepOutcome:
        ctx.events.publish(StepEvent(ctx.phase, self.id, self.description, "started"))
        if ctx.dry_run:
            msg = f"would run: {' '.join(self.argv)}"
            ctx.events.publish(
                StepEvent(ctx.phase, self.id, self.description, "dry_run", msg)
            )
            return StepOutcome(StepStatus.DRY_RUN, msg)
        try:
            ctx.process.run(self.argv, check=self.check)
        except Exception as exc:  # noqa: BLE001
            ctx.events.publish(
                StepEvent(ctx.phase, self.id, self.description, "failed", str(exc))
            )
            raise StepFailedError(self.description, str(exc)) from exc
        ctx.events.publish(StepEvent(ctx.phase, self.id, self.description, "applied"))
        return StepOutcome(StepStatus.APPLIED)


# --------------------------------------------------------------------------
# Shared file mechanics
# --------------------------------------------------------------------------


def _lines_of(content: str | None) -> list[str]:
    if not content:
        return []
    return [line.strip() for line in content.splitlines() if line.strip()]


def _append_lines(ctx: ExecutionContext, path: str, lines: list[str]) -> dict:
    """Back up, append the lines that are not already there, and lock down
    the permissions. Returns the prior state for the journal.

    Append-and-de-duplicate rather than write: this function's one caller
    class is the SSH authentication boundary, and an overwrite there would
    silently revoke keys the user put in by hand.
    """

    existed = ctx.filesystem.exists(path)
    backup_path = ctx.filesystem.backup(path) if existed else None

    directory = os.path.dirname(path)
    if directory:
        ctx.filesystem.ensure_dir(directory)
        _chmod(directory, 0o700)

    current = ctx.filesystem.read_text(path) or ""
    present = set(_lines_of(current))
    new_lines = [line for line in lines if line not in present]

    if new_lines:
        prefix = current if (not current or current.endswith("\n")) else current + "\n"
        ctx.filesystem.write_text(
            path, prefix + "".join(f"{line}\n" for line in new_lines), mode=0o600
        )
        ctx.filesystem.take_ownership_for_real_user(path)

    return {"existed": existed, "backup_path": backup_path, "path": path}


def _restore_file(ctx: ExecutionContext, record: ChangeRecord) -> None:
    """Restore a file to exactly the state the record captured.

    Restoring the backup byte-for-byte, rather than filtering out the lines we
    added, is the only version that is correct when the user edited the file
    themselves between install and uninstall -- and it is the only version
    that cannot accidentally remove a key devtunnel never added.
    """

    path = record.prior_state.get("path") or record.details.get("path")
    if not path:
        return
    if record.prior_state.get("existed"):
        ctx.filesystem.restore_from_backup(path, record.prior_state.get("backup_path"))
    else:
        ctx.filesystem.remove_file(path)


def _chmod(path: str, mode: int) -> None:
    """Direct ``os.chmod``, deliberately not routed through FileSystemPort.

    The port's write methods take a mode for files they create; this is the
    one case that needs to tighten permissions on a *directory* that may have
    predated devtunnel (``~/.ssh`` at 0755 is common and makes OpenSSH refuse
    the keys inside it). A no-op on Windows, which has no POSIX mode bits.
    """

    if os.name == "nt":
        return
    try:
        os.chmod(path, mode)
    except OSError:
        pass  # best effort: a mode we cannot set is not a reason to fail install


__all__ = [
    "JournaledStep",
    "BootstrapPackageManagerStep",
    "EnsurePackageStep",
    "EnsureServiceStep",
    "SetGitConfigStep",
    "InstallTailcatBinaryStep",
    "GenerateTailcatKeyStep",
    "EnsureSshKeypairStep",
    "EnsureAuthorizedKeysStep",
    "EnsurePeerStep",
    "EnsureAllowlistEntryStep",
    "AddToPathStep",
    "RunCommandStep",
]
