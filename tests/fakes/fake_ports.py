"""In-memory fakes for every port, used across the unit test suite.

None of these touch the filesystem, network, or a real subprocess -- that is
what lets the application-layer tests (steps, plan builder, install/uninstall
round-trip) run instantly and assert on exact state transitions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from devtunnel.application.ports.process_runner import CompletedProcess
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.models import PlatformId


class FakeProcessRunner:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.responses: dict[tuple[str, ...], CompletedProcess] = {}

    def run(
        self,
        argv: Sequence[str],
        *,
        check: bool = True,
        input: str | None = None,
        env: Mapping[str, str] | None = None,
        timeout: float | None = None,
    ) -> CompletedProcess:
        self.calls.append(list(argv))
        key = tuple(argv)
        if key in self.responses:
            return self.responses[key]
        return CompletedProcess(tuple(argv), 0, "", "")

    def spawn(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None):
        raise NotImplementedError("not exercised by unit tests")

    def which(self, executable: str) -> str | None:
        return f"/usr/bin/{executable}"


class InMemoryJournalRepository:
    def __init__(self) -> None:
        self._records = []

    def load(self):
        return list(self._records)

    def append(self, record) -> None:
        self._records.append(record)

    def update(self, record) -> None:
        for i, existing in enumerate(self._records):
            if existing.id == record.id:
                self._records[i] = record
                return
        self._records.append(record)

    def clear(self) -> None:
        self._records = []

    def location(self) -> str:
        return "memory://journal"


class FakeFileSystem:
    def __init__(self) -> None:
        self.files: dict[str, str] = {}
        self.dirs: set[str] = set()

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    def read_text(self, path: str) -> str | None:
        return self.files.get(path)

    def write_text(self, path: str, content: str, *, mode: int | None = None) -> None:
        self.files[path] = content

    def write_bytes(self, path: str, content: bytes, *, mode: int | None = None) -> None:
        self.files[path] = content  # type: ignore[assignment]

    def remove_file(self, path: str) -> None:
        self.files.pop(path, None)

    def ensure_dir(self, path: str) -> bool:
        created = path not in self.dirs
        self.dirs.add(path)
        return created

    def remove_dir_if_empty(self, path: str) -> None:
        if path in self.dirs and not any(f.startswith(path + "/") for f in self.files):
            self.dirs.discard(path)

    def backup(self, path: str) -> str | None:
        if path not in self.files:
            return None
        backup_path = f"{path}.bak"
        self.files[backup_path] = self.files[path]
        return backup_path

    def restore_from_backup(self, path: str, backup_path: str | None) -> None:
        if backup_path is None:
            self.files.pop(path, None)
        else:
            self.files[path] = self.files.get(backup_path, "")

    def real_user_home(self) -> str:
        return "/home/fake"

    def take_ownership_for_real_user(self, path: str) -> None:
        return None


class FakePrompter:
    def __init__(self, text_answers=None, secret_answers=None, confirm_answer: bool = True) -> None:
        self._text = list(text_answers or [])
        self._secret = list(secret_answers or [])
        self.confirm_answer = confirm_answer

    def ask_text(self, message: str, *, default: str | None = None) -> str:
        return self._text.pop(0) if self._text else (default or "")

    def ask_secret(self, message: str) -> str:
        return self._secret.pop(0) if self._secret else ""

    def confirm(self, message: str, *, default: bool = False) -> bool:
        return self.confirm_answer


class FakePackageManager:
    def __init__(
        self, platform: PlatformId = PlatformId.DEBIAN, installed: set[str] | None = None
    ) -> None:
        self.platform = platform
        self.installed: set[str] = installed if installed is not None else set()

    def is_installed(self, package) -> bool:
        return package.key in self.installed

    def install(self, package) -> None:
        self.installed.add(package.key)

    def uninstall(self, package) -> None:
        self.installed.discard(package.key)


class FakeBootstrap:
    def __init__(self, bootstrapped: bool = False) -> None:
        self.bootstrapped = bootstrapped

    def is_bootstrapped(self) -> bool:
        return self.bootstrapped

    def bootstrap(self) -> dict:
        self.bootstrapped = True
        return {"install_dir": "/fake/choco"}

    def teardown(self, details: dict) -> None:
        self.bootstrapped = False


class FakeRepositoryProvider:
    def __init__(self) -> None:
        self.registered: set[str] = set()

    def is_registered(self, name: str) -> bool:
        return name in self.registered

    def register(self, name: str) -> dict:
        self.registered.add(name)
        return {"created_dir": True, "dir_path": "/fake/keyrings"}

    def unregister(self, name: str, details: dict) -> None:
        self.registered.discard(name)


class FakeServiceManager:
    def __init__(self) -> None:
        self._states: dict[str, ServiceState] = {}

    def get_state(self, service) -> ServiceState:
        return self._states.get(
            service.key, ServiceState(exists=False, running=False, startup_automatic=False)
        )

    def ensure_running_and_enabled(self, service) -> None:
        self._states[service.key] = ServiceState(exists=True, running=True, startup_automatic=True)

    def apply_state(self, service, state: ServiceState) -> None:
        self._states[service.key] = state


class FakeToolkit:
    def __init__(
        self, platform: PlatformId = PlatformId.DEBIAN, has_repository: bool = True
    ) -> None:
        self.platform = platform
        self.package_bootstrap = FakeBootstrap()
        self.service_manager = FakeServiceManager()
        self.repository_provider = FakeRepositoryProvider() if has_repository else None
        self.manager = FakePackageManager(platform)

    def manager_for(self, package_key: str):
        return self.manager

    def is_elevated(self) -> bool:
        return True

    def elevation_hint(self) -> str:
        return "fake: already elevated"


class FakeTunnelProvider:
    def __init__(self) -> None:
        self.configured = False

    def is_authtoken_configured(self) -> bool:
        return self.configured

    def configure_authtoken(self, token: str) -> dict:
        self.configured = True
        return {"had_prior_token": False}

    def remove_authtoken(self, prior_state: dict) -> None:
        self.configured = False

    def start(self, spec, *, timeout: float = 15.0):
        raise NotImplementedError("not exercised by unit tests")

    def stop(self, handle) -> None:
        return None
