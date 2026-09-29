"""Port for resolving a single secret value (the ngrok authtoken).

The concrete adapter (:class:`devtunnel.infrastructure.credentials.chain.CredentialChain`)
implements Chain of Responsibility internally: environment variable, then a
CLI flag value passed straight through, then an interactive prompt -- each
link only handling the case it owns and falling through otherwise. Modelling
the whole chain as one port keeps that ordering decision out of the use case,
which just asks "give me the token" and gets back a resolved value or a
:class:`~devtunnel.domain.errors.CredentialResolutionError`.
"""

from __future__ import annotations

from typing import Protocol


class CredentialProviderPort(Protocol):
    def resolve(
        self,
        *,
        what: str,
        env_var: str,
        flag_value: str | None,
        non_interactive: bool,
        secret: bool = True,
    ) -> str:
        """Resolve a value from env var, then ``flag_value``, then a prompt.

        Raises ``NonInteractiveInputRequiredError`` if nothing resolves and
        ``non_interactive`` is True. Raises ``CredentialResolutionError`` if
        an interactive prompt is attempted but yields an empty value.
        """
