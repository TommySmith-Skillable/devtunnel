"""In-memory fakes for every port, used across the unit test suite.

None of these touch the filesystem, network, or a real subprocess -- that is
what lets the application-layer tests (steps, plan builder, install/uninstall
round-trip) run instantly and assert on exact state transitions.

``FakeTunnelProvider`` is the surviving justification for ``TunnelProviderPort``:
the port is a test seam, not a provider abstraction.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from devtunnel.application.ports.process_runner import CompletedProcess
from devtunnel.application.ports.service_manager import ServiceState
from devtunnel.domain.models import PlatformId, Scope

FAKE_ADDRESS = "tcFAKEADDRESSFAKEADDRESSFAKEADDRESS"
FAKE_NODE_KEY = "nodekey:" + "ab" * 32


class FakeManagedProcess:
    """A long-running child whose stderr is a scripted list of lines.

    ``read_stderr_line`` returning ``None`` once the script is exhausted is
    what lets a test drive the wait-for-address loop all the way to its
    timeout branch without any real waiting.
    """

    def __init__(self, stderr_lines: Sequence[str] = (), exit_code: int | None = None) -> None:
        self._lines = list(stderr_lines)
        self._exit_code = exit_code
        self.terminated = False
        self.waited = False
        self.stderr_reads = 0

    @property
    def pid(self) -> int:
        return 4242

    def poll(self) -> int | None:
        return self._exit_code

    def terminate(self) -> None:
        self.terminated = True
        self._exit_code = -15

    def wait(self, timeout: float | None = None) -> int:
        self.waited = True
        return self._exit_code or 0

    def read_stderr_line(self, timeout: float | None = None) -> str | None:
        self.stderr_reads += 1
        return self._lines.pop(0) if self._lines else None


class FakeProcessRunner:
    def __init__(self, *, missing: Sequence[str] = ()) -> None:
        self.calls: list[list[str]] = []
        self.envs: list[Mapping[str, str] | None] = []
        self.responses: dict[tuple[str, ...], CompletedProcess] = {}
        self.spawned: list[list[str]] = []
        self.spawn_result: FakeManagedProcess | None = None
        # Executables `which` should report as absent -- how a test says
        # "this machine has no ssh-keygen" without touching a real PATH.
        self.missing = set(missing)

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
        self.envs.append(env)
        key = tuple(argv)
        if key in self.responses:
            return self.responses[key]
        return CompletedProcess(tuple(argv), 0, "", "")

    def spawn(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None):
        self.spawned.append(list(argv))
        return self.spawn_result or FakeManagedProcess()

    def which(self, executable: str) -> str | None:
        if executable in self.missing:
            return None
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
        self.modes: dict[str, int] = {}
        self.owned: list[str] = []

    def exists(self, path: str) -> bool:
        return path in self.files or path in self.dirs

    def read_text(self, path: str) -> str | None:
        value = self.files.get(path)
        return value if isinstance(value, str) else None

    def write_text(self, path: str, content: str, *, mode: int | None = None) -> None:
        self.files[path] = content
        if mode is not None:
            self.modes[path] = mode

    def write_bytes(self, path: str, content: bytes, *, mode: int | None = None) -> None:
        self.files[path] = content  # type: ignore[assignment]
        if mode is not None:
            self.modes[path] = mode

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
        self.owned.append(path)


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


class FakeServiceManager:
    def __init__(self) -> None:
        self._states: dict[str, ServiceState] = {}
        self.last_linger_error: str | None = None

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
        self, platform: PlatformId = PlatformId.DEBIAN, *, elevated: bool = True
    ) -> None:
        self.platform = platform
        self.package_bootstrap = FakeBootstrap()
        self.manager = FakePackageManager(platform)
        self.user_service = FakeServiceManager()
        self.system_service = FakeServiceManager()
        self.elevated = elevated

    def service_manager_for(self, scope: Scope):
        return self.user_service if scope is Scope.USER else self.system_service

    @property
    def service_manager(self):
        return self.system_service

    def manager_for(self, package_key: str):
        return self.manager

    def is_elevated(self) -> bool:
        return self.elevated

    def elevation_hint(self) -> str:
        return "fake: elevation required"


class FakeBinaryInstaller:
    """Stands in for the GitHub release installer.

    Records whether a pre-existing binary was adopted, because that flag is
    what ``uninstall`` consults before deleting anything.
    """

    def __init__(self, *, preexisting: bool = False, version: str = "0.7.0") -> None:
        self.installed: dict[str, dict] = {}
        self.preexisting = preexisting
        self.version = version

    def is_installed(self, target_path: str) -> bool:
        return target_path in self.installed

    def installed_version(self, target_path: str) -> str | None:
        return self.version if target_path in self.installed else None

    def install(self, target_path: str) -> dict:
        details = {
            "path": target_path,
            "version": self.version,
            "preexisting": self.preexisting,
            "sha256": "0" * 64,
        }
        self.installed[target_path] = details
        return details

    def uninstall(self, details: dict) -> None:
        if details.get("preexisting"):
            return  # never ours to remove
        self.installed.pop(details.get("path", ""), None)


class FakeTunnelProvider:
    def __init__(self) -> None:
        self.keys: dict[str, dict] = {}
        self.started: list = []
        self.stopped: list = []

    def has_key(self, name: str) -> bool:
        return name in self.keys

    def generate_key(
        self,
        name: str,
        *,
        client: bool = False,
        region: str | None = None,
        fixed_region: bool = False,
    ) -> dict:
        prior = {
            "name": name,
            "client": client,
            "existed": name in self.keys,
            "address": None if client else FAKE_ADDRESS,
            "node_key": FAKE_NODE_KEY,
            "region": region,
            "fixed_region": fixed_region,
        }
        self.keys[name] = prior
        return prior

    def remove_key(self, prior_state: dict) -> None:
        if prior_state.get("existed"):
            return  # the key predated devtunnel
        self.keys.pop(prior_state.get("name", ""), None)

    def node_key(self, name: str) -> str:
        return FAKE_NODE_KEY

    def address_for(self, name: str) -> str | None:
        entry = self.keys.get(name)
        return entry.get("address") if entry else None

    def start(self, spec, *, timeout: float = 15.0):
        from devtunnel.application.ports.tunnel_provider import TunnelHandle

        process = FakeManagedProcess()
        handle = TunnelHandle(address=FAKE_ADDRESS, spec=spec, process=process)
        self.started.append(handle)
        return handle

    def stop(self, handle) -> None:
        self.stopped.append(handle)


def fake_paths(home: str = "/home/fake"):
    """A ``DevtunnelPaths`` with every location under one fake home."""

    from devtunnel.application.paths import DevtunnelPaths

    state = os.path.join(home, ".local", "share", "devtunnel")
    return DevtunnelPaths(
        home=home,
        user_state_dir=state,
        machine_state_dir="/var/lib/devtunnel",
        binary_path=os.path.join(state, "bin", "tailcat"),
        allowlist_path=os.path.join(state, "allow.list"),
        authorized_keys_path=os.path.join(home, ".ssh", "authorized_keys"),
        ssh_dir=os.path.join(home, ".ssh"),
        cache_dir=os.path.join(state, "cache"),
        tailcat_keys_dir=os.path.join(home, ".config", "tailcat", "keys"),
    )
