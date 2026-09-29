"""Use case: open (and later close) the foreground ngrok SSH tunnel.

Deliberately produces no journal entries: the tunnel lives only as long as
the process does (see the plan's "Tunnel mode: foreground only" decision), so
there is nothing here for uninstall to ever need to reverse.
"""

from __future__ import annotations

from devtunnel.application.context import ExecutionContext
from devtunnel.application.ports.tunnel_provider import TunnelHandle
from devtunnel.domain.models import TunnelSpec


class StartTunnelUseCase:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    def start(self, spec: TunnelSpec) -> TunnelHandle:
        if not self._ctx.tunnel_provider.is_authtoken_configured():
            raise RuntimeError(
                "no ngrok authtoken is configured; run 'devtunnel install' first"
            )
        return self._ctx.tunnel_provider.start(spec)

    def stop(self, handle: TunnelHandle) -> None:
        self._ctx.tunnel_provider.stop(handle)
