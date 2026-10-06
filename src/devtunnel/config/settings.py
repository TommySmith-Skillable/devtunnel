"""Optional JSON config file for unattended installs.

Precedence is **flag > config file > prompt**. There is no environment-variable
tier: it existed only to carry a vendor secret out of a dashboard, and tailcat's
identity is a keypair generated locally, so there is no secret to source. The
credential chain that implemented that tier went with it.

Git identity still has no chain at all: if neither a flag nor the file supplies
it, devtunnel leaves whatever git identity already exists untouched.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field

from devtunnel.application import catalog


class FileConfig(BaseModel):
    role: str = "server"
    with_git: bool = False
    git_name: str | None = None
    git_email: str | None = None
    tailcat_version: str = catalog.TAILCAT_VERSION
    key_name: str | None = None
    """``None`` means "whichever default matches the role" -- ``default`` for a
    server, ``client-default`` for a client -- rather than forcing a config file
    to restate tailcat's own naming."""

    region: str | None = None
    fixed_region: bool = False
    authorized_keys: list[str] = Field(default_factory=list)
    allow: list[str] = Field(default_factory=list)
    peers: list[str] = Field(default_factory=list)
    ssh_identity: str | None = None
    add_to_path: bool = False
    skip_packages: list[str] = Field(default_factory=list)

    @classmethod
    def load(cls, path: str | None) -> FileConfig:
        if not path:
            return cls()
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls.model_validate(data)
