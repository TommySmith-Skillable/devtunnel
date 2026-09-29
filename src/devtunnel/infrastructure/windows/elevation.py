"""Administrator-privilege detection on Windows."""

from __future__ import annotations

import os


def is_admin() -> bool:
    if os.name != "nt":
        return False
    import ctypes

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001 - best-effort detection
        return False
