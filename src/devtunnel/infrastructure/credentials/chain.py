"""Chain of Responsibility: env var -> CLI flag -> interactive prompt.

Each link tries only the source it owns and, if empty, defers to the next.
The final link (the prompt) either returns a value or raises -- it never
falls through silently, since there is nothing left to try after it.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass

from devtunnel.application.ports.prompter import PrompterPort
from devtunnel.domain.errors import CredentialResolutionError, NonInteractiveInputRequiredError


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    what: str
    env_var: str
    flag_value: str | None
    non_interactive: bool
    secret: bool


class CredentialLink(ABC):
    def __init__(self, successor: CredentialLink | None = None) -> None:
        self._successor = successor

    def handle(self, request: CredentialRequest) -> str:
        value = self._try(request)
        if value is not None:
            return value
        if self._successor is not None:
            return self._successor.handle(request)
        raise CredentialResolutionError(f"could not resolve {request.what}")

    @abstractmethod
    def _try(self, request: CredentialRequest) -> str | None: ...


class EnvVarLink(CredentialLink):
    def _try(self, request: CredentialRequest) -> str | None:
        return os.environ.get(request.env_var) or None


class FlagLink(CredentialLink):
    def _try(self, request: CredentialRequest) -> str | None:
        return request.flag_value or None


class PromptLink(CredentialLink):
    def __init__(self, prompter: PrompterPort, successor: CredentialLink | None = None) -> None:
        super().__init__(successor)
        self._prompter = prompter

    def _try(self, request: CredentialRequest) -> str | None:
        if request.non_interactive:
            raise NonInteractiveInputRequiredError(request.what)
        message = f"Enter {request.what}"
        if request.secret:
            value = self._prompter.ask_secret(message)
        else:
            value = self._prompter.ask_text(message)
        if not value:
            raise CredentialResolutionError(f"{request.what} must not be empty")
        return value


class CredentialChain:
    """The :class:`~devtunnel.application.ports.credentials.CredentialProviderPort`
    adapter, wiring the three links in precedence order."""

    def __init__(self, prompter: PrompterPort) -> None:
        self._chain: CredentialLink = EnvVarLink(FlagLink(PromptLink(prompter)))

    def resolve(
        self,
        *,
        what: str,
        env_var: str,
        flag_value: str | None,
        non_interactive: bool,
        secret: bool = True,
    ) -> str:
        request = CredentialRequest(what, env_var, flag_value, non_interactive, secret)
        return self._chain.handle(request)
