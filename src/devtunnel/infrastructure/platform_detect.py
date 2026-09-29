"""Chain of Responsibility: detect which :class:`PlatformId` this machine is.

Adding a platform later (RHEL, Arch, ...) means inserting one more link in
:func:`detect_platform` -- nothing else in the codebase changes.
"""

from __future__ import annotations

import os
import platform
from abc import ABC, abstractmethod

from devtunnel.domain.errors import UnsupportedPlatformError
from devtunnel.domain.models import PlatformId


class PlatformDetector(ABC):
    def __init__(self, successor: PlatformDetector | None = None) -> None:
        self._successor = successor

    def detect(self) -> PlatformId:
        result = self._try()
        if result is not None:
            return result
        if self._successor is not None:
            return self._successor.detect()
        raise UnsupportedPlatformError(
            f"unsupported platform: {platform.system()} {platform.release()}"
        )

    @abstractmethod
    def _try(self) -> PlatformId | None: ...


class WindowsDetector(PlatformDetector):
    def _try(self) -> PlatformId | None:
        return PlatformId.WINDOWS if os.name == "nt" else None


class DebianDetector(PlatformDetector):
    """Matches Debian and its derivatives (Ubuntu, Mint, ...), all of which
    ship ``/etc/debian_version`` and use apt."""

    def _try(self) -> PlatformId | None:
        if platform.system() != "Linux":
            return None
        return PlatformId.DEBIAN if os.path.exists("/etc/debian_version") else None


def detect_platform() -> PlatformId:
    chain: PlatformDetector = WindowsDetector(DebianDetector())
    return chain.detect()
