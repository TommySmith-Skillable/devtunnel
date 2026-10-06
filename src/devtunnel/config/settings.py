"""Optional JSON config file for unattended installs.

Precedence collapsed to **flag > config file > prompt**. The ngrok era had a
fourth tier, the ``NGROK_AUTHTOKEN`` environment variable, which existed only
to carry a vendor secret out of a dashboard; tailcat's identity is a keypair
generated locally, so there is no secret to source and the whole tier went with
it -- along with the credential chain that implemented it.

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
