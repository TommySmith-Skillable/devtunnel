"""The fixed catalog of things devtunnel knows how to install.

Centralising these as data (rather than scattering literals through steps and
toolkits) is what lets both ``PlanBuilder`` (building the install plan) and
the uninstall path (reconstructing a step from a journal record's ``target``)
agree on the same keys, and what lets ``--skip``/``--keep`` flags refer to
packages by a single stable name across platforms.
"""

from __future__ import annotations

from devtunnel.domain.models import PackageSpec, ServiceSpec

GIT = PackageSpec(key="git", display_name="Git", choco_id="git", apt_id="git")

NGROK = PackageSpec(key="ngrok", display_name="ngrok", choco_id="ngrok", apt_id="ngrok")

OPENSSH_CLIENT = PackageSpec(
    key="openssh-client",
    display_name="OpenSSH Client",
    windows_capability_id="OpenSSH.Client~~~~0.0.1.0",
    apt_id="openssh-client",
)

OPENSSH_SERVER = PackageSpec(
    key="openssh-server",
    display_name="OpenSSH Server",
    windows_capability_id="OpenSSH.Server~~~~0.0.1.0",
    apt_id="openssh-server",
)

GPG = PackageSpec(key="gpg", display_name="GNU Privacy Guard", apt_id="gpg")
"""Debian-only: needed to ``--dearmor`` ngrok's apt signing key. Never added
to the plan on Windows, where Chocolatey needs no repository setup at all."""

PACKAGES_BY_KEY: dict[str, PackageSpec] = {
    p.key: p for p in (GIT, NGROK, OPENSSH_CLIENT, OPENSSH_SERVER, GPG)
}

SSHD = ServiceSpec(
    key="sshd",
    display_name="OpenSSH Server (sshd)",
    windows_service_name="sshd",
    systemd_unit="ssh",
)

SERVICES_BY_KEY: dict[str, ServiceSpec] = {SSHD.key: SSHD}

# The one third-party repository devtunnel ever registers. Debian ships
# neither git, ngrok's apt package, nor gpg needs no extra repo -- only ngrok
# does. See infrastructure/debian/apt_repository.py for the mechanism.
NGROK_APT_REPOSITORY_NAME = "ngrok"
NGROK_APT_KEYRING_URL = "https://ngrok-agent.s3.amazonaws.com/ngrok.asc"
NGROK_APT_KEYRING_PATH = "/etc/apt/keyrings/ngrok.gpg"
NGROK_APT_SOURCES_LIST_PATH = "/etc/apt/sources.list.d/ngrok.list"
NGROK_APT_SOURCES_LIST_CONTENT = (
    f"deb [signed-by={NGROK_APT_KEYRING_PATH}] "
    "https://ngrok-agent.s3.amazonaws.com buster main\n"
)
