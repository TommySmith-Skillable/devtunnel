"""Optional JSON config file for unattended installs.

Precedence, end to end: CLI flag > config file > (for the ngrok authtoken
only) the ``NGROK_AUTHTOKEN`` environment variable > interactive prompt. The
first three are resolved here and in ``cli/app.py``; the last two are the
``CredentialChain``'s job (see :mod:`devtunnel.infrastructure.credentials.chain`).
Git identity has no chain -- if neither a flag nor the file supplies it,
devtunnel leaves whatever git identity already exists untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field


class FileConfig(BaseModel):
    git_name: str | None = None
    git_email: str | None = None
    ngrok_authtoken: str | None = None
    skip_packages: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | None) -> FileConfig:
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)
