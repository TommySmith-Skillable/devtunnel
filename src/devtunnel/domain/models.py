"""Core value objects.

Everything here is an immutable, dependency-free value object. None of it knows
how to install a package or start a service -- it only describes *what* those
things are, so the same values can flow through application use cases and
infrastructure adapters without those layers depending on each other.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class PlatformId(StrEnum):
    """A supported operating system family.

    Adding a new platform (e.g. RHEL) means adding a new member here, a new
    :class:`~devtunnel.infrastructure.toolkits.PlatformToolkit` implementation,
    and nothing else in the domain or application layers.
    """

    WINDOWS = "windows"
    DEBIAN = "debian"


class Scope(StrEnum):
    """Whether a piece of state belongs to the current user or the whole machine."""

    USER = "user"
    MACHINE = "machine"


@dataclass(frozen=True, slots=True)
class PackageSpec:
    """A package devtunnel may install, named per package manager.

    ``key`` is the canonical, platform-independent identifier used in the
    journal and in ``--skip``/``--keep`` CLI flags (e.g. ``"git"``). Each
    manager-specific field is read only by the one
    :class:`~devtunnel.application.ports.package_manager.PackageManagerPort`
    adapter that understands it -- e.g. ``windows_capability_id`` is read
    solely by the Windows-capability manager used for OpenSSH, never by
    Chocolatey, even though both run on Windows.
    """

    key: str
    display_name: str
    choco_id: str | None = None
    apt_id: str | None = None
    windows_capability_id: str | None = None


@dataclass(frozen=True, slots=True)
class ServiceSpec:
    """A service devtunnel may register, enable and start.

    ``scope`` decides *which* service manager runs it: a ``USER``-scope
    service is a systemd **user** unit or a per-user Scheduled Task, neither
    of which needs elevation; a ``MACHINE``-scope service is a real system
    unit or a Windows service and does. ``PlatformToolkit.service_manager_for``
    is the lookup that turns this field into the right adapter.
    """

    key: str
    display_name: str
    scope: Scope = Scope.MACHINE
    windows_service_name: str | None = None
    systemd_unit: str | None = None


@dataclass(frozen=True, slots=True)
class GitIdentity:
    """The ``user.name`` / ``user.email`` git expects for commits.

    Either field may be ``None``, meaning "leave whatever is already
    configured alone" -- this is what lets the install step skip identity
    configuration entirely when the caller supplies neither.
    """

    name: str | None = None
    email: str | None = None

    @property
    def is_empty(self) -> bool:
        return self.name is None and self.email is None


class Role(StrEnum):
    """Which half of a tailcat connection this machine is being set up as.

    A machine may be both, but each role is installed separately: the server
    role generates a listening key and an ``authorized_keys`` boundary, the
    client role generates a node key *and* an SSH identity to hand over as a
    pairing bundle.
    """

    SERVER = "server"
    CLIENT = "client"


@dataclass(frozen=True, slots=True)
class TunnelSpec:
    """Parameters for one ``tailcat serve`` invocation.

    Every field maps to exactly one tailcat flag or positional argument; the
    single place that turns this into an argv is
    :meth:`devtunnel.infrastructure.tailcat.tailcat_tunnel.TailcatTunnelProvider.build_argv`,
    so the CLI surface of a pre-1.0 dependency is pinned down in one method.
    """

    serve: tuple[str, ...] = ("ssh",)
    """What to expose: ``("ssh",)``, ``("no-auth-ssh",)``, or ports such as
    ``("8080,8443",)``."""

    key_name: str | None = "default"
    """Saved key to listen with. ``None`` means an ephemeral key
    (``--key=new``), whose address is only knowable by reading tailcat's
    startup banner."""

    allow: tuple[str, ...] = ()
    """``nodekey:<hex>`` values permitted to connect. **Empty means the
    address itself is the only credential** -- see ``--open``."""

    authorized_keys: tuple[str, ...] = ()
    """Paths and/or ``user@github`` sources for the SSH authentication
    boundary. Only meaningful when serving ``ssh``."""

    region: str | None = None
    bind: str | None = None
    forced_command: str | None = None
    """Passed after ``--``; restricts the peer to this one command."""

    @property
    def is_open(self) -> bool:
        """True when no allowlist is set, i.e. the address is the credential."""

        return not self.allow
