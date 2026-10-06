"""The real :class:`FileSystemPort` adapter.

``real_user_home``/``take_ownership_for_real_user`` exist specifically to fix
a bug present in the reference scripts: running the Linux installer with
``sudo`` makes ``~`` resolve to ``/root``, so a naively-written config file
lands where the real user's tooling will never look for it. Resolving
``SUDO_USER`` and chowning the result back is what keeps the file usable
after the script exits.
"""

from __future__ import annotations

import os
import shutil
import uuid


class LocalFileSystem:
    def __init__(self, backups_dir: str) -> None:
        self._backups_dir = backups_dir

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def read_text(self, path: str) -> str | None:
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def write_text(self, path: str, content: str, *, mode: int | None = None) -> None:
        self._ensure_parent(path)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        if mode is not None and os.name != "nt":
            os.chmod(path, mode)

    def write_bytes(self, path: str, content: bytes, *, mode: int | None = None) -> None:
        self._ensure_parent(path)
        with open(path, "wb") as f:
            f.write(content)
        if mode is not None and os.name != "nt":
            os.chmod(path, mode)

    def remove_file(self, path: str) -> None:
        try:
            os.remove(path)
        except FileNotFoundError:
            pass

    def ensure_dir(self, path: str) -> bool:
        if os.path.isdir(path):
            return False
        os.makedirs(path, exist_ok=True)
        return True

    def remove_dir_if_empty(self, path: str) -> None:
        try:
            if os.path.isdir(path) and not os.listdir(path):
                os.rmdir(path)
        except OSError:
            pass

    def backup(self, path: str) -> str | None:
        if not os.path.exists(path):
            return None
        os.makedirs(self._backups_dir, exist_ok=True)
        backup_path = os.path.join(
            self._backups_dir, f"{uuid.uuid4().hex}-{os.path.basename(path)}.bak"
        )
        shutil.copy2(path, backup_path)
        return backup_path

    def restore_from_backup(self, path: str, backup_path: str | None) -> None:
        if backup_path is None:
            self.remove_file(path)
            return
        self._ensure_parent(path)
        shutil.copy2(backup_path, path)

    def real_user_home(self) -> str:
        if os.name != "nt":
            sudo_user = os.environ.get("SUDO_USER")
            if sudo_user:
                import pwd

                try:
                    return pwd.getpwnam(sudo_user).pw_dir
                except KeyError:
                    pass
        return os.path.expanduser("~")

    def take_ownership_for_real_user(self, path: str) -> None:
        if os.name == "nt":
            return
        sudo_user = os.environ.get("SUDO_USER")
        if not sudo_user:
            return
        import pwd

        try:
            entry = pwd.getpwnam(sudo_user)
        except KeyError:
            return
        uid, gid = entry.pw_uid, entry.pw_gid
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                for name in (*dirs, *files):
                    os.chown(os.path.join(root, name), uid, gid)
        os.chown(path, uid, gid)

    @staticmethod
    def _ensure_parent(path: str) -> None:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
