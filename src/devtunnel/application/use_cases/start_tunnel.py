"""Use case: open (and later close) a foreground tailcat tunnel.

Deliberately produces no journal entries: a foreground tunnel lives only as
long as the process does, so there is nothing here for uninstall to reverse.
The *persistent* form -- ``devtunnel serve --install-service`` -- is a
different thing entirely and is journaled, because a registered service is
exactly the kind of change a user would not want left behind.
"""

from __future__ import annotations

from devtunnel.application.allowlist import Allowlist
from devtunnel.application.context import ExecutionContext
from devtunnel.application.ports.tunnel_provider import TunnelHandle
from devtunnel.domain.errors import DevtunnelError
from devtunnel.domain.models import TunnelSpec


class OpenTunnelRefusedError(DevtunnelError):
    """Raised when a configuration would expose a shell to anyone holding the
    address, and the caller has not said so explicitly."""


class StartTunnelUseCase:
    def __init__(self, ctx: ExecutionContext) -> None:
        self._ctx = ctx

    def resolve_spec(self, spec: TunnelSpec, *, open_tunnel: bool = False) -> TunnelSpec:
        """Fill in the saved allowlist and GitHub sources, and enforce ``--open``.

        Two rules, both from plan section 11:

        * An empty allowlist means the address *is* the credential. That is a
          real mode tailcat supports, but it must be chosen, never inherited
          from an empty file -- so reaching it requires ``--open``.
        * ``no-auth-ssh`` hands a shell to anyone who can reach the tunnel.
          It requires ``--open`` on top, so a shell-to-anyone configuration
          cannot be assembled by accident out of two innocuous-looking flags.
        """

        if "no-auth-ssh" in spec.serve and not open_tunnel:
            raise OpenTunnelRefusedError(
                "--serve no-auth-ssh gives a shell to anyone who can reach the tunnel; "
                "pass --open to confirm you mean it"
            )

        fields = _as_dict(spec)
        fields["authorized_keys"] = spec.authorized_keys + self._delegated_sources()

        if not spec.allow:
            saved = tuple(
                Allowlist(self._ctx.filesystem, self._ctx.paths.allowlist_path).entries()
            )
            if saved:
                fields["allow"] = saved
            elif not open_tunnel:
                raise OpenTunnelRefusedError(
                    "no peers are allow-listed, so the address alone would be the credential; "
                    "add a peer with 'devtunnel pair add', or pass --open to accept that"
                )

        return TunnelSpec(**fields)

    def _delegated_sources(self) -> tuple[str, ...]:
        """``user@github`` sources recorded by the authorized-keys step.

        These were deliberately *not* written into ``authorized_keys`` -- tailcat
        fetches ``github.com/<user>.keys`` itself at serve time, and copying them
        in would freeze a set the user expects to stay live. That only works if
        they actually reach the serve flags, so they are read back out of the
        journal record that captured them. Without this the keys would be
        configured, journaled, and then silently never consulted.
        """

        sources: list[str] = []
        for record in self._ctx.journal.load():
            if record.target != "file:authorized_keys" or not record.needs_revert:
                continue
            for entry in record.details.get("delegated", ()):
                if entry not in sources:
                    sources.append(entry)
        return tuple(sources)

    def start(self, spec: TunnelSpec) -> TunnelHandle:
        if spec.key_name and not self._ctx.tunnel_provider.has_key(spec.key_name):
            raise DevtunnelError(
                f"no tailcat key named {spec.key_name!r}; run 'devtunnel install' first"
            )
        return self._ctx.tunnel_provider.start(spec)

    def stop(self, handle: TunnelHandle) -> None:
        self._ctx.tunnel_provider.stop(handle)


def _as_dict(spec: TunnelSpec) -> dict:
    return {
        "serve": spec.serve,
        "key_name": spec.key_name,
        "allow": spec.allow,
        "authorized_keys": spec.authorized_keys,
        "region": spec.region,
        "bind": spec.bind,
        "forced_command": spec.forced_command,
    }
