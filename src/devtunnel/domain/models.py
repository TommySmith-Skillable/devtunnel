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
    """A system service devtunnel may enable/start (e.g. sshd)."""

    key: str
    display_name: str
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


@dataclass(frozen=True, slots=True)
class TunnelSpec:
    """Parameters for an ngrok TCP tunnel fronting a local SSH daemon."""

    local_port: int = 22
    region: str | None = None
    protocol: str = "tcp"
