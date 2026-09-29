"""Port for interactive user input.

The only adapter is a thin Rich-console wrapper; its Null-Object counterpart
is not a separate class but simply never being called -- ``--non-interactive``
is enforced upstream, in :class:`devtunnel.infrastructure.credentials.chain.CredentialChain`
and the use cases, which raise rather than reach the prompter at all.
"""

from __future__ import annotations

from typing import Protocol


class PrompterPort(Protocol):
    def ask_text(self, message: str, *, default: str | None = None) -> str: ...

    def ask_secret(self, message: str) -> str: ...

    def confirm(self, message: str, *, default: bool = False) -> bool: ...
