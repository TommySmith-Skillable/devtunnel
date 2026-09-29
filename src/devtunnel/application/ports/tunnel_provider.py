"""Port for the ngrok tunnel itself: authtoken configuration and lifecycle.

Configuring the authtoken and starting/stopping the tunnel are both "ngrok
concerns" and are grouped on one port, implemented together by
:class:`devtunnel.infrastructure.ngrok.ngrok_tunnel.NgrokTunnelProvider`
(composed internally from the config-file mechanics in
``infrastructure/ngrok/ngrok_config.py`` and the process-lifecycle mechanics
in the same module).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from devtunnel.application.ports.process_runner import ManagedProcess
from devtunnel.domain.models import TunnelSpec


@dataclass(frozen=True, slots=True)
class TunnelHandle:
    public_host: str
    public_port: int
    local_port: int
    process: ManagedProcess

    @property
    def ssh_command(self) -> str:
        return f"ssh -p {self.public_port} <user>@{self.public_host}"


class TunnelProviderPort(Protocol):
    def is_authtoken_configured(self) -> bool: ...

    def configure_authtoken(self, token: str) -> dict:
        """Write ``token`` to ngrok's config. Returns prior-state details."""

    def remove_authtoken(self, prior_state: dict) -> None:
        """Reverse :meth:`configure_authtoken` using its prior-state details."""

    def start(self, spec: TunnelSpec, *, timeout: float = 15.0) -> TunnelHandle:
        """Launch the ngrok agent and wait until the public URL is ready."""

    def stop(self, handle: TunnelHandle) -> None:
        """Terminate the tunnel process cleanly."""
