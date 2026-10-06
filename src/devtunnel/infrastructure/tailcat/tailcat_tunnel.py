"""The real ``TunnelProviderPort`` adapter: runs ``tailcat serve`` and learns
the address peers reach it on.

Composed from :class:`~devtunnel.infrastructure.tailcat.tailcat_keys.TailcatKeys`
for the key half of the port and its own process-lifecycle logic for
``start``/``stop`` -- together they satisfy the whole
:class:`~devtunnel.application.ports.tunnel_provider.TunnelProviderPort`.

Two positions are deliberate:

* **The address is not scraped when it can be known.** A tailcat key's address
  is fixed at generation time, so the persistent path asks
  :meth:`TailcatKeys.address_for` and never reads a line of output -- which
  means a change to upstream's banner wording cannot break ``devtunnel up``
  for anyone using a saved key.
* **Failures are never opaque.** A bare ``"exited early with code 1"`` discards
  the child's stderr, which is exactly the text that says *why*. Here it is
  drained into the exception.
"""

from __future__ import annotations

import re
import time

from devtunnel.application import catalog
from devtunnel.application.ports.process_runner import ManagedProcess, ProcessRunnerPort
from devtunnel.application.ports.tunnel_provider import TunnelHandle
from devtunnel.domain.models import TunnelSpec
from devtunnel.infrastructure.tailcat.tailcat_keys import TailcatKeys

_ADDRESS_RE = re.compile(r"\btc[A-Za-z0-9_-]{16,}\b")

_BANNER_MARKER = "listening"
"""The startup banner is ``Server listening with saved key "default": tc...``
or ``Server listening with new address: tc...``. Requiring the word anchors the
address match to the one line that announces the listener, so an address
appearing in, say, an error about a peer is never mistaken for our own."""

_READ_SLICE_SECONDS = 0.5
"""How long a single stderr read may block. Keeping it well under the caller's
overall timeout is what lets the loop re-check ``poll()`` for an early exit
while still waiting on a child that has not spoken yet."""

_MAX_STDERR_CONTEXT_LINES = 10


class TailcatTunnelProvider:
    def __init__(
        self,
        process: ProcessRunnerPort,
        keys: TailcatKeys,
        *,
        binary_path: str = catalog.TAILCAT_BINARY_NAME,
    ) -> None:
        self._process = process
        self._keys = keys
        self._binary_path = binary_path

    # -- keys ------------------------------------------------------------

    def has_key(self, name: str) -> bool:
        return self._keys.has_key(name)

    def generate_key(
        self,
        name: str,
        *,
        client: bool = False,
        region: str | None = None,
        fixed_region: bool = False,
    ) -> dict:
        return self._keys.generate(
            name, client=client, region=region, fixed_region=fixed_region
        )

    def remove_key(self, prior_state: dict) -> None:
        """Reverse :meth:`generate_key` using exactly the dict it returned.

        A key recorded as ``existed`` was the user's before devtunnel ran, so it
        is left strictly alone -- the same promise ``SKIPPED_PREEXISTING`` makes
        for packages, and the reason the prior-state dict is the whole revert
        input rather than just a name.
        """

        if prior_state.get("existed"):
            return
        name = prior_state.get("name")
        if not name:
            return
        self._keys.delete(name)

    def node_key(self, name: str) -> str:
        node_key = self._keys.node_key(name)
        if node_key is None:
            raise RuntimeError(
                f"tailcat reported no node key for {name!r}; "
                "generate it with `devtunnel install` first"
            )
        return node_key

    def address_for(self, name: str) -> str | None:
        return self._keys.address_for(name)

    # -- argv ------------------------------------------------------------

    def build_argv(self, spec: TunnelSpec) -> list[str]:
        """Turn a :class:`~devtunnel.domain.models.TunnelSpec` into an argv.

        **The single source of truth for tailcat's command line.** tailcat is
        pre-1.0 and its flags may still move, so every invocation of ``serve``
        in the codebase comes through here: when upstream renames a flag, this
        method is the diff, and its tests are the regression net. No other
        module -- not the CLI, not the service-unit writer -- assembles these
        strings.

        ``--ssh-authorized-keys`` is emitted only when the spec serves exactly
        ``ssh``: the flag is meaningless when forwarding ports, and passing it
        anyway would imply an authentication boundary that is not there.
        """

        argv = [self._binary_path, "serve"]
        argv.append(f"--key={spec.key_name}" if spec.key_name else "--key=new")
        if spec.allow:
            argv.append("--allow=" + ",".join(spec.allow))
        if spec.authorized_keys and spec.serve == ("ssh",):
            argv.append("--ssh-authorized-keys=" + ",".join(spec.authorized_keys))
        if spec.region:
            argv.append(f"--region={spec.region}")
        if spec.bind:
            argv.append(f"--bind={spec.bind}")
        argv.extend(spec.serve)
        if spec.forced_command:
            argv.extend(["--", spec.forced_command])
        return argv

    # -- lifecycle -------------------------------------------------------

    def start(self, spec: TunnelSpec, *, timeout: float = 15.0) -> TunnelHandle:
        """Launch ``tailcat serve`` and return once its address is known.

        A saved key's address is resolved *before* anything is spawned, and in
        that case not one line of output is read: the persistent path must not
        depend on upstream's banner wording. Scraping is the fallback for an
        ephemeral (``--key=new``) tunnel, whose address does not exist until the
        process mints it.
        """

        known_address = self._keys.address_for(spec.key_name) if spec.key_name else None

        process = self._process.spawn(self.build_argv(spec))

        if known_address:
            return TunnelHandle(address=known_address, spec=spec, process=process)

        deadline = time.monotonic() + timeout
        seen: list[str] = []

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break

            exit_code = process.poll()
            if exit_code is not None:
                seen.extend(self._drain_stderr(process))
                raise RuntimeError(
                    f"tailcat exited early with code {exit_code}"
                    + self._stderr_context(seen)
                )

            line = process.read_stderr_line(timeout=min(_READ_SLICE_SECONDS, remaining))
            if line is None:
                continue
            seen.append(line)
            if _BANNER_MARKER in line.lower():
                match = _ADDRESS_RE.search(line)
                if match:
                    return TunnelHandle(address=match.group(0), spec=spec, process=process)

        process.terminate()
        raise TimeoutError(
            f"tailcat did not announce an address within {timeout}s"
            + self._stderr_context(seen)
        )

    def stop(self, handle: TunnelHandle) -> None:
        handle.process.terminate()
        try:
            handle.process.wait(timeout=5.0)
        except Exception:  # noqa: BLE001 - best-effort cleanup
            pass

    # -- internals -------------------------------------------------------

    @staticmethod
    def _drain_stderr(process: ManagedProcess) -> list[str]:
        """Take whatever stderr is already buffered, without blocking.

        A child that has exited will not produce more, so a zero timeout is
        enough; the cap stops a chatty failure from turning an exception message
        into a log file.
        """

        lines: list[str] = []
        while len(lines) < _MAX_STDERR_CONTEXT_LINES:
            line = process.read_stderr_line(timeout=0.0)
            if line is None:
                break
            lines.append(line)
        return lines

    @staticmethod
    def _stderr_context(lines: list[str]) -> str:
        """Attach tailcat's own words to the failure.

        This is the whole point: the diagnosis is almost always in the last few
        lines the child wrote, and discarding them turns every failure into a
        bare exit code the user has to reproduce by hand to understand.
        """

        tail = [line for line in lines[-_MAX_STDERR_CONTEXT_LINES:] if line.strip()]
        if not tail:
            return " (it wrote nothing to stderr)"
        return "; tailcat said:\n" + "\n".join(f"  {line}" for line in tail)
