"""The ngrok apt repository: keyring + sources.list.d entry, reversible.

Fixes the second bug present in the reference ``setup.sh``, which assumes
``/etc/apt/keyrings`` already exists -- it does not on older Debian releases.
Here, directory creation is tracked (``created_dir``) so uninstall only
removes the directory if devtunnel is the one that made it.

The signing key is fetched with ``httpx`` (already a dependency) rather than
shelling out to ``curl``, and handed to ``gpg --dearmor`` via a temporary file
rather than piping through subprocess stdout -- ``gpg --dearmor`` writes
binary output, and the process runner's ``run()`` captures output as text, so
routing binary bytes through it would corrupt them.
"""

from __future__ import annotations

import os

import httpx

from devtunnel.application import catalog
from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort


class AptNgrokRepository:
    def __init__(self, process: ProcessRunnerPort, filesystem: FileSystemPort) -> None:
        self._process = process
        self._filesystem = filesystem

    def is_registered(self, name: str) -> bool:
        return self._filesystem.exists(
            catalog.NGROK_APT_SOURCES_LIST_PATH
        ) and self._filesystem.exists(catalog.NGROK_APT_KEYRING_PATH)

    def register(self, name: str) -> dict:
        keyrings_dir = os.path.dirname(catalog.NGROK_APT_KEYRING_PATH)
        created_dir = self._filesystem.ensure_dir(keyrings_dir)

        response = httpx.get(catalog.NGROK_APT_KEYRING_URL, timeout=15.0, follow_redirects=True)
        response.raise_for_status()

        tmp_armored_path = f"{catalog.NGROK_APT_KEYRING_PATH}.asc.tmp"
        self._filesystem.write_text(tmp_armored_path, response.text)
        try:
            self._process.run(
                [
                    "gpg",
                    "--yes",
                    "--dearmor",
                    "-o",
                    catalog.NGROK_APT_KEYRING_PATH,
                    tmp_armored_path,
                ]
            )
        finally:
            self._filesystem.remove_file(tmp_armored_path)

        self._filesystem.write_text(
            catalog.NGROK_APT_SOURCES_LIST_PATH, catalog.NGROK_APT_SOURCES_LIST_CONTENT
        )

        return {
            "keyring_path": catalog.NGROK_APT_KEYRING_PATH,
            "sources_list_path": catalog.NGROK_APT_SOURCES_LIST_PATH,
            "created_dir": created_dir,
            "dir_path": keyrings_dir,
        }

    def unregister(self, name: str, details: dict) -> None:
        self._filesystem.remove_file(
            details.get("sources_list_path", catalog.NGROK_APT_SOURCES_LIST_PATH)
        )
        self._filesystem.remove_file(details.get("keyring_path", catalog.NGROK_APT_KEYRING_PATH))
        if details.get("created_dir") and details.get("dir_path"):
            self._filesystem.remove_dir_if_empty(details["dir_path"])
