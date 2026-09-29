"""Port for reading and changing a system service's run state.

``ServiceState`` is a snapshot value object used both as the return of
:meth:`ServiceManagerPort.get_state` and as the shape stored in a
:class:`~devtunnel.domain.journal.ChangeRecord`'s ``prior_state`` -- so revert
is always "restore the exact state we captured," never a hardcoded
stop+disable that might fight a service the machine already depended on.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Protocol

from devtunnel.domain.models import ServiceSpec


@dataclass(frozen=True, slots=True)
class ServiceState:
    exists: bool
    running: bool
    startup_automatic: bool

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> ServiceState:
        return cls(
            exists=data["exists"],
            running=data["running"],
            startup_automatic=data["startup_automatic"],
        )


class ServiceManagerPort(Protocol):
    def get_state(self, service: ServiceSpec) -> ServiceState: ...

    def ensure_running_and_enabled(self, service: ServiceSpec) -> None:
        """Start the service and set it to start automatically at boot."""

    def apply_state(self, service: ServiceSpec, state: ServiceState) -> None:
        """Force the service into exactly ``state`` (used to revert)."""
