"""Port for registering a third-party package repository, where the platform
needs one.

Not in the plan's original port list, and earned by a real asymmetry: adding
the ngrok apt repository (download a signing key, ``gpg --dearmor`` it into
``/etc/apt/keyrings``, write a ``sources.list.d`` entry, refresh the index) is
a Debian-only concern with real OS mechanism behind it -- ``choco install
ngrok`` needs no repository setup at all. ``PlanBuilder`` omits the whole step
on platforms whose :class:`~devtunnel.application.ports.toolkit.PlatformToolkit`
returns ``repository_provider = None``, rather than faking a no-op adapter.

Journaling discipline stays with the calling ``Step`` (see
:class:`devtunnel.application.steps.EnsureRepositoryStep`), not with this
port: the adapter only performs the mechanism and reports back what it
touched, so the one-record-per-change guarantee in
:class:`~devtunnel.application.steps.JournaledStep` still holds.
"""

from __future__ import annotations

from typing import Protocol


class RepositoryProviderPort(Protocol):
    def is_registered(self, name: str) -> bool: ...

    def register(self, name: str) -> dict:
        """Idempotently register the named repository.

        Returns a ``details`` dict (e.g. paths written, whether a directory
        had to be created) sufficient for :meth:`unregister` to reverse it.
        """

    def unregister(self, name: str, details: dict) -> None:
        """Reverse whatever :meth:`register` did, using its ``details``."""
