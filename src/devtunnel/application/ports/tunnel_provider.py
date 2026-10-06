"""Port for tailcat: key material and tunnel lifecycle.

Keys and the tunnel are grouped on one port because they are one vendor's
concern, implemented together by
:class:`devtunnel.infrastructure.tailcat.tailcat_tunnel.TailcatTunnelProvider`
(composed internally from the key mechanics in
``infrastructure/tailcat/tailcat_keys.py`` and its own process lifecycle).

The key trio -- :meth:`~TunnelProviderPort.has_key`,
:meth:`~TunnelProviderPort.generate_key`, :meth:`~TunnelProviderPort.remove_key`
-- keeps a deliberately narrow journaling contract:
``generate_key`` returns the prior state as a dict, ``remove_key`` consumes
exactly that dict. That is what lets :class:`~devtunnel.application.steps.JournaledStep`
stay oblivious to which provider is underneath.

The port exists as a **test seam**, not as a provider abstraction:
``FakeTunnelProvider`` is its second implementation and the only one that will
ever be added.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from devtunnel.application.ports.process_runner import ManagedProcess
from devtunnel.domain.models import TunnelSpec


@dataclass(frozen=True, slots=True)
class TunnelHandle:
    """A running ``tailcat serve`` and the address peers reach it on.

    Lives here rather than in ``domain`` only because it holds a
    :class:`~devtunnel.application.ports.process_runner.ManagedProcess`; the
    domain layer has no outward dependencies, and a live process handle is
    not a value object.
    """

    address: str
    spec: TunnelSpec
    process: ManagedProcess

    @property
    def connect_command(self) -> str:
        """The raw tailcat invocation a peer runs. devtunnel's own
        ``devtunnel connect <address>`` wraps this."""

        return f"tailcat ssh {self.address}"


class TunnelProviderPort(Protocol):
    # -- keys ------------------------------------------------------------

    def has_key(self, name: str) -> bool:
        """Whether a saved key of this name already exists."""

    def generate_key(
        self,
        name: str,
        *,
        client: bool = False,
        region: str | None = None,
        fixed_region: bool = False,
    ) -> dict:
        """Run ``tailcat genkey``.

        Returns a prior-state dict for the journal. It carries the address
        (server keys) or ``nodekey:<hex>`` (client keys) that ``genkey``
        printed, so later commands never have to re-derive either.
        """

    def remove_key(self, prior_state: dict) -> None:
        """Reverse :meth:`generate_key` using exactly its prior-state dict."""

    def node_key(self, name: str) -> str:
        """This machine's ``nodekey:<hex>``, for a peer's allowlist."""

    def address_for(self, name: str) -> str | None:
        """The stable address of a saved key, without starting anything.

        This is what makes the persistent path deterministic: ``up`` and
        ``serve`` can print a connection command *before* the process has
        said anything, and never have to scrape the startup banner.
        """

    # -- lifecycle -------------------------------------------------------

    def start(self, spec: TunnelSpec, *, timeout: float = 15.0) -> TunnelHandle:
        """Launch ``tailcat serve`` and return once its address is known."""

    def stop(self, handle: TunnelHandle) -> None:
        """Terminate the tunnel process cleanly."""
