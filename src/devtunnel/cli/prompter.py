"""The only :class:`PrompterPort` adapter: a thin Rich-console wrapper."""

from __future__ import annotations

from rich.console import Console
from rich.prompt import Confirm, Prompt


class RichPrompter:
    def __init__(self, console: Console | None = None) -> None:
        self._console = console or Console()

    def ask_text(self, message: str, *, default: str | None = None) -> str:
        return Prompt.ask(message, default=default, console=self._console) or ""

    def ask_secret(self, message: str) -> str:
        return Prompt.ask(message, password=True, console=self._console) or ""

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return Confirm.ask(message, default=default, console=self._console)
