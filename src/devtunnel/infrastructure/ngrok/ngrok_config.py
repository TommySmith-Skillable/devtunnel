"""ngrok config-file mechanics: locating it, and writing/removing the
authtoken it holds, sudo/elevation-aware.

Fixes the third bug present in the reference scripts: running the installer
with ``sudo`` makes ``ngrok config add-authtoken`` write into ``/root``'s
config rather than the real user's, so ``ngrok`` never finds the token
afterwards. Here the ngrok CLI is invoked with ``HOME`` (or the equivalent on
Windows) pointed at the real user's home directory, and the resulting file's
ownership is corrected afterwards.

Writing goes through ngrok's own ``config add-authtoken`` command rather than
hand-editing the YAML file directly -- that keeps devtunnel from having to
parse or reproduce ngrok's config format, and from clobbering any other
settings (region, log path, ...) the user may already have in it.
"""

from __future__ import annotations

import os

from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort


class NgrokConfigWriter:
    def __init__(self, process: ProcessRunnerPort, filesystem: FileSystemPort) -> None:
        self._process = process
        self._filesystem = filesystem

    def config_path(self) -> str:
        home = self._filesystem.real_user_home()
        if os.name == "nt":
            default_local_app_data = os.path.join(home, "AppData", "Local")
            local_app_data = os.environ.get("LOCALAPPDATA") or default_local_app_data
            return os.path.join(local_app_data, "ngrok", "ngrok.yml")
        return os.path.join(home, ".config", "ngrok", "ngrok.yml")

    def is_configured(self) -> bool:
        content = self._filesystem.read_text(self.config_path())
        return content is not None and "authtoken:" in content

    def write_authtoken(self, token: str) -> dict:
        path = self.config_path()
        existed = self._filesystem.exists(path)
        backup_path = self._filesystem.backup(path) if existed else None

        self._process.run(["ngrok", "config", "add-authtoken", token], env=self._home_env())
        self._filesystem.take_ownership_for_real_user(path)

        return {"config_path": path, "existed": existed, "backup_path": backup_path}

    def remove_authtoken(self, prior_state: dict) -> None:
        path = prior_state.get("config_path", self.config_path())
        if prior_state.get("existed"):
            self._filesystem.restore_from_backup(path, prior_state.get("backup_path"))
        else:
            self._filesystem.remove_file(path)
            self._filesystem.remove_dir_if_empty(os.path.dirname(path))

    def _home_env(self) -> dict[str, str]:
        if os.name == "nt":
            return {}
        return {"HOME": self._filesystem.real_user_home()}
