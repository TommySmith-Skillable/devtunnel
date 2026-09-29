"""The real ``TunnelProviderPort`` adapter: spawns the ngrok agent and polls
its local API until the public tunnel address is ready.

Composed from :class:`~devtunnel.infrastructure.ngrok.ngrok_config.NgrokConfigWriter`
for the authtoken half of the port and its own process-lifecycle logic for
``start``/``stop`` -- together they satisfy the whole
:class:`~devtunnel.application.ports.tunnel_provider.TunnelProviderPort`.
"""

from __future__ import annotations

import time

import httpx

from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.application.ports.tunnel_provider import TunnelHandle
from devtunnel.domain.models import TunnelSpec
from devtunnel.infrastructure.ngrok.ngrok_config import NgrokConfigWriter

_LOCAL_API_URL = "http://127.0.0.1:4040/api/tunnels"
_POLL_INTERVAL_SECONDS = 0.5


class NgrokTunnelProvider:
    def __init__(self, process: ProcessRunnerPort, config: NgrokConfigWriter) -> None:
        self._process = process
        self._config = config

    # -- authtoken -----------------------------------------------------

    def is_authtoken_configured(self) -> bool:
        return self._config.is_configured()

    def configure_authtoken(self, token: str) -> dict:
        return self._config.write_authtoken(token)

    def remove_authtoken(self, prior_state: dict) -> None:
        self._config.remove_authtoken(prior_state)

    # -- tunnel lifecycle ------------------------------------------------

    def start(self, spec: TunnelSpec, *, timeout: float = 15.0) -> TunnelHandle:
        argv = ["ngrok", spec.protocol, str(spec.local_port), "--log=stdout"]
        if spec.region:
            argv.extend(["--region", spec.region])

        process = self._process.spawn(argv)
        deadline = time.monotonic() + timeout
        last_error: Exception | None = None

        while time.monotonic() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                raise RuntimeError(f"ngrok exited early with code {exit_code}")
            try:
                response = httpx.get(_LOCAL_API_URL, timeout=2.0)
                response.raise_for_status()
                tunnels = response.json().get("tunnels", [])
                for tunnel in tunnels:
                    if tunnel.get("proto") == spec.protocol:
                        public_url = tunnel["public_url"]  # e.g. "tcp://0.tcp.ngrok.io:12345"
                        host_port = public_url.split("://", 1)[1]
                        host, _, port = host_port.partition(":")
                        return TunnelHandle(
                            public_host=host,
                            public_port=int(port),
                            local_port=spec.local_port,
                            process=process,
                        )
            except httpx.HTTPError as exc:
                last_error = exc
            time.sleep(_POLL_INTERVAL_SECONDS)

        process.terminate()
        raise TimeoutError(
            f"ngrok did not report a public URL within {timeout}s"
            + (f" (last error: {last_error})" if last_error else "")
        )

    def stop(self, handle: TunnelHandle) -> None:
        handle.process.terminate()
        try:
            handle.process.wait(timeout=5.0)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass
