"""The fixed catalog of things devtunnel knows how to install.

Centralising these as data (rather than scattering literals through steps and
toolkits) is what lets both ``PlanBuilder`` (building the install plan) and
the uninstall path (reconstructing a step from a journal record's ``target``)
agree on the same keys, and what lets ``--skip``/``--keep`` flags refer to
packages by a single stable name across platforms.

It is deliberately much shorter than it was. tailcat arrives as a verified
release binary rather than a package, and serves SSH itself -- so the packaged
tunnel client, gpg, the OpenSSH *server* and the apt-repository constants all
left with it. What remains is git (opt-in, the one step that still needs
elevation) and the OpenSSH *client*, which the client role still needs for
``ssh``/``ssh-keygen``.
"""

from __future__ import annotations

from devtunnel.domain.models import PackageSpec, Scope, ServiceSpec

# --------------------------------------------------------------------------
# tailcat itself
# --------------------------------------------------------------------------

TAILCAT_REPO = "tailscale/tailcat"

TAILCAT_VERSION = "0.7.0"
"""Pinned by default, and overridable per-invocation with ``--tailcat-version``.

A fleet that installs ``latest`` installs whatever upstream published this
morning; pinning means an install is reproducible and an upstream release can
never silently change what lands on a machine. ``--tailcat-version=latest`` is
the opt-in escape hatch.
"""

TAILCAT_BINARY_NAME = "tailcat"

DEFAULT_SERVER_KEY = "default"
DEFAULT_CLIENT_KEY = "client-default"
"""tailcat's own default key names. Using them means a hand-run ``tailcat``
with no flags picks up the same key devtunnel created."""

# --------------------------------------------------------------------------
# Packages
# --------------------------------------------------------------------------

GIT = PackageSpec(key="git", display_name="Git", choco_id="git", apt_id="git")
"""Opt-in since D5: installing git is the only remaining step that needs
Administrator/sudo, and leaving it in the default plan would single-handedly
force elevation on an otherwise unprivileged install. ``--with-git`` adds it."""

OPENSSH_CLIENT = PackageSpec(
    key="openssh-client",
    display_name="OpenSSH Client",
    windows_capability_id="OpenSSH.Client~~~~0.0.1.0",
    apt_id="openssh-client",
)
"""Retained despite tailcat serving SSH itself: ``tailcat ssh`` wraps the
*stock* ssh client with a ProxyCommand, so the client role needs ``ssh`` to
connect and ``ssh-keygen`` to create an identity. This is the one remaining
caller of ``WindowsCapabilityManager``. Planned only when ``ssh-keygen`` is
absent."""

PACKAGES_BY_KEY: dict[str, PackageSpec] = {p.key: p for p in (GIT, OPENSSH_CLIENT)}

# --------------------------------------------------------------------------
# Services
# --------------------------------------------------------------------------

TUNNEL_SERVICE = ServiceSpec(
    key="tunnel",
    display_name="devtunnel tailcat tunnel",
    scope=Scope.USER,
    windows_service_name="DevtunnelTailcat",
    systemd_unit="devtunnel-tailcat",
)
"""User-scope by default: a systemd *user* unit or a per-user Scheduled Task
needs no elevation, which keeps ``devtunnel serve --install-service`` inside
the unprivileged story. ``--system`` swaps in the machine-scope spec below."""

TUNNEL_SERVICE_SYSTEM = ServiceSpec(
    key="tunnel",
    display_name="devtunnel tailcat tunnel (system)",
    scope=Scope.MACHINE,
    windows_service_name="DevtunnelTailcat",
    systemd_unit="devtunnel-tailcat",
)
"""Same ``key`` as :data:`TUNNEL_SERVICE` on purpose -- the journal records
``service:tunnel`` either way, and ``details["scope"]`` distinguishes them so
revert reaches for the right manager."""

SERVICES_BY_KEY: dict[str, ServiceSpec] = {TUNNEL_SERVICE.key: TUNNEL_SERVICE}

# --------------------------------------------------------------------------
# Deferred, with the seam left open
# --------------------------------------------------------------------------
# `--ssh-mode=sshd` (fronting the system sshd with `tailcat serve 22`) would
# restore OPENSSH_SERVER and an `sshd` ServiceSpec here, and give
# WindowsCapabilityManager a second caller. Both are in git history at the
# commit that removed them.
