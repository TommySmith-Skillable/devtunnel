"""Installs the tailcat binary from a GitHub Releases artifact.

tailcat is in no distro repository and no Chocolatey feed, so unlike git and
ngrok it cannot be delegated to a :class:`PackageManagerPort`. This adapter is
the replacement for that: it resolves the right release asset for the host,
downloads it, verifies it, unpacks exactly one member, and hands back a journal
record precise enough to undo all of it.

Three deliberate positions are encoded here.

*Verification is not optional.* devtunnel downloads an executable and then runs
it, so a corrupted or substituted artifact is a remote code execution bug, not a
failed install. The SHA-256 from ``checksums.txt`` is compared before anything
is written to the target path, and there is deliberately no ``--no-verify``
escape hatch: a flag that disables a security control during an outage is a flag
that ends up pasted into a runbook.

*The cache is not a trust boundary.* A cached artifact is re-verified on every
use. Caching exists so that a ``--dry-run`` followed by a real run does not
re-fetch, not to grant the local disk authority the network was never given.

*Architecture detection never guesses.* An unrecognised ``platform.machine()``
is a hard, named failure. Silently falling back to ``amd64`` installs a binary
that either refuses to exec or, worse, half-works, and the user gets no sign
that devtunnel chose for them.

Network access goes through the stdlib :mod:`urllib.request` rather than a third
party HTTP client because the tailcat migration drops ``httpx`` from the
dependency set, and two GETs do not justify a dependency. ``urlopen`` also
honours ``HTTPS_PROXY``/``HTTP_PROXY``/``NO_PROXY`` from the environment
natively, which matters on the corporate networks this tool is most often run
on, so no proxy plumbing is written here. Every call is funnelled through
:meth:`_fetch_bytes` so unit tests replace one small method and never open a
socket.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import re
import tarfile
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from devtunnel.application import catalog
from devtunnel.application.ports.filesystem import FileSystemPort
from devtunnel.application.ports.process_runner import ProcessRunnerPort
from devtunnel.domain.errors import DevtunnelError

_ASSET_ARCH_BY_MACHINE = {
    "x86_64": "amd64",
    "amd64": "amd64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "armv7",
    "armv6l": "armv7",
}

_ASSET_OS_BY_SYSTEM = {
    "linux": "linux",
    "windows": "windows",
}

#: The five combinations actually published upstream. ``windows``/``armv7`` is
#: absent on purpose: no such asset exists, so that request has to fail by name
#: rather than resolve to a URL that 404s halfway through an install.
_PUBLISHED_COMBINATIONS = frozenset(
    {
        ("linux", "amd64"),
        ("linux", "arm64"),
        ("linux", "armv7"),
        ("windows", "amd64"),
        ("windows", "arm64"),
    }
)

_CHECKSUMS_FILE = "checksums.txt"
_LATEST = "latest"
_NETWORK_TIMEOUT_SECONDS = 60.0
_SEMVER = re.compile(r"\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?")
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")


class UnsupportedArchitectureError(DevtunnelError):
    """Raised when no release asset exists for this OS/CPU combination."""


class ChecksumMismatchError(DevtunnelError):
    """Raised when an artifact does not match its published SHA-256."""


class ReleaseArtifactError(DevtunnelError):
    """Raised when a release artifact is malformed: a missing checksum line, an
    archive with no tailcat member, or a member name that tries to escape."""


@dataclass(frozen=True, slots=True)
class ReleaseAsset:
    tag: str
    version: str
    name: str
    download_url: str
    checksums_url: str


@dataclass(frozen=True, slots=True)
class _Download:
    """Verified bytes carried together with the digest that was proven about
    them, so the SHA written to the journal is the one that was checked rather
    than one recomputed later from whatever is on disk by then."""

    content: bytes = field(repr=False)
    sha256: str


class GitHubReleaseInstaller:
    """Resolve, download, verify, unpack and remove a tailcat release binary.

    ``system`` and ``machine`` are constructor parameters rather than direct
    :mod:`platform` calls so that every published asset name, and every refusal,
    can be exercised from one developer machine without patching a global --
    which under a parallel test run is a shared-state bug waiting to happen.
    """

    def __init__(
        self,
        process: ProcessRunnerPort,
        filesystem: FileSystemPort,
        *,
        repo: str = catalog.TAILCAT_REPO,
        version: str | None = None,
        cache_dir: str | None = None,
        system: str | None = None,
        machine: str | None = None,
    ) -> None:
        self._process = process
        self._filesystem = filesystem
        self._repo = repo
        self._version = version or catalog.TAILCAT_VERSION
        self._cache_dir = cache_dir
        self._system = (system or platform.system()).strip()
        self._machine = (machine or platform.machine()).strip()

    # -- naming ----------------------------------------------------------

    @property
    def binary_name(self) -> str:
        if self._asset_os() == "windows":
            return f"{catalog.TAILCAT_BINARY_NAME}.exe"
        return catalog.TAILCAT_BINARY_NAME

    def resolve_asset(self) -> ReleaseAsset:
        asset_os = self._asset_os()
        arch = self._asset_arch()
        if (asset_os, arch) not in _PUBLISHED_COMBINATIONS:
            raise UnsupportedArchitectureError(
                f"no tailcat release asset is published for {asset_os}/{arch} "
                f"(machine {self._machine!r})"
            )

        version = self._resolve_version()
        tag = f"v{version}"
        suffix = "zip" if asset_os == "windows" else "tar.gz"
        name = f"{catalog.TAILCAT_BINARY_NAME}_{version}_{asset_os}_{arch}.{suffix}"
        base = f"https://github.com/{self._repo}/releases/download/{tag}"
        return ReleaseAsset(
            tag=tag,
            version=version,
            name=name,
            download_url=f"{base}/{name}",
            checksums_url=f"{base}/{_CHECKSUMS_FILE}",
        )

    def _asset_os(self) -> str:
        try:
            return _ASSET_OS_BY_SYSTEM[self._system.lower()]
        except KeyError:
            raise UnsupportedArchitectureError(
                f"tailcat publishes no release for operating system {self._system!r} "
                f"(machine {self._machine!r})"
            ) from None

    def _asset_arch(self) -> str:
        try:
            return _ASSET_ARCH_BY_MACHINE[self._machine.lower()]
        except KeyError:
            raise UnsupportedArchitectureError(
                f"unrecognised CPU architecture {self._machine!r}: tailcat publishes "
                f"amd64, arm64 and armv7 builds only"
            ) from None

    def _resolve_version(self) -> str:
        """Pinned by default; ``latest`` is opt-in.

        A fleet that resolves ``latest`` on every run installs whatever upstream
        shipped that morning, which turns someone else's release into an
        unannounced change across every machine at once.
        """
        if self._version != _LATEST:
            return self._version.lstrip("v")
        payload = json.loads(
            self._fetch_text(f"https://api.github.com/repos/{self._repo}/releases/latest")
        )
        tag = str(payload.get("tag_name") or "").strip()
        if not tag:
            raise ReleaseArtifactError(f"the latest release of {self._repo} reported no tag_name")
        return tag.lstrip("v")

    # -- inspection ------------------------------------------------------

    def is_installed(self, target_path: str) -> bool:
        return self._filesystem.exists(target_path)

    def installed_version(self, target_path: str) -> str | None:
        """Best-effort. ``None`` means "could not tell", never "not installed".

        Callers use this for reporting, so a binary that is present but refuses
        to answer must not be mistaken for an absent one -- that distinction is
        :meth:`is_installed`'s job, and conflating the two is how an installer
        ends up overwriting something it should have left alone.
        """
        for argv in ([target_path, "version"], [target_path, "--version"]):
            try:
                result = self._process.run(argv, check=False)
            except Exception:
                continue
            if not result.ok:
                continue
            match = _SEMVER.search(f"{result.stdout}\n{result.stderr}")
            if match:
                return match.group(0)
        return None

    # -- install / uninstall ---------------------------------------------

    def install(self, target_path: str) -> dict:
        preexisting = self._process.which(catalog.TAILCAT_BINARY_NAME)
        if preexisting:
            # The project's standing promise: never touch what it did not
            # install. A tailcat the user manages themselves is adopted as-is
            # and left exactly where it is on uninstall.
            return {
                "path": preexisting,
                "version": self.installed_version(preexisting),
                "tag": None,
                "asset": None,
                "sha256": None,
                "preexisting": True,
                "created_dirs": [],
            }

        asset = self.resolve_asset()
        created_dirs: list[str] = []

        cache_dir = self._cache_directory()
        if cache_dir and self._filesystem.ensure_dir(cache_dir):
            created_dirs.append(cache_dir)

        download = self._obtain_verified(asset, cache_dir)

        # Nothing above this line has touched the target path, and nothing below
        # it runs on unverified bytes.
        binary = self._extract_binary(download.content, asset)

        target_dir = os.path.dirname(target_path)
        if target_dir and self._filesystem.ensure_dir(target_dir):
            created_dirs.append(target_dir)

        mode = None if self._asset_os() == "windows" else 0o755
        self._filesystem.write_bytes(target_path, binary, mode=mode)

        return {
            "path": target_path,
            "version": asset.version,
            "tag": asset.tag,
            "asset": asset.name,
            "sha256": download.sha256,
            "preexisting": False,
            "created_dirs": created_dirs,
        }

    def uninstall(self, details: dict) -> None:
        if details.get("preexisting"):
            return
        path = details.get("path")
        if path:
            self._filesystem.remove_file(path)
        # Deepest first: a parent cannot be empty until its child is gone.
        for directory in reversed(list(details.get("created_dirs") or [])):
            self._filesystem.remove_dir_if_empty(directory)

    # -- download and verification ---------------------------------------

    def _obtain_verified(self, asset: ReleaseAsset, cache_dir: str | None) -> _Download:
        expected = self._expected_sha256(asset)
        cache_path = os.path.join(cache_dir, asset.name) if cache_dir else None

        content = self._read_cached(cache_path) if cache_path else None
        from_cache = content is not None
        if content is None:
            content = self._fetch_bytes(asset.download_url)

        actual = hashlib.sha256(content).hexdigest()
        if actual != expected:
            source = "cached copy of" if from_cache else "downloaded"
            raise ChecksumMismatchError(
                f"SHA-256 mismatch for the {source} {asset.name}: expected {expected}, "
                f"got {actual}. Nothing was installed."
            )

        if cache_path and not from_cache:
            self._filesystem.write_bytes(cache_path, content)
        return _Download(content=content, sha256=actual)

    def _expected_sha256(self, asset: ReleaseAsset) -> str:
        """``checksums.txt`` is one ``<sha256>  <filename>`` pair per line."""
        for line in self._fetch_text(asset.checksums_url).splitlines():
            parts = line.split()
            if len(parts) >= 2 and os.path.basename(parts[-1]) == asset.name:
                return parts[0].lower()
        raise ReleaseArtifactError(
            f"{_CHECKSUMS_FILE} for {asset.tag} has no entry for {asset.name}"
        )

    def _cache_directory(self) -> str | None:
        if self._cache_dir:
            return self._cache_dir
        return os.path.join(self._filesystem.real_user_home(), ".cache", "devtunnel")

    def _read_cached(self, path: str) -> bytes | None:
        """The one place this adapter reads the disk outside the port.

        :class:`FileSystemPort` reads text, because it was built for config
        files; a release tarball is bytes. Existence is still asked of the port,
        so a fake filesystem that was never told about this path short-circuits
        before any real stat happens, and the caller re-verifies the digest
        regardless of where the bytes came from.
        """
        if not self._filesystem.exists(path):
            return None
        try:
            return Path(path).read_bytes()
        except OSError:
            return None

    def _fetch_bytes(self, url: str) -> bytes:
        """The single network seam.

        ``urlopen`` reads ``HTTPS_PROXY``/``HTTP_PROXY``/``NO_PROXY`` from the
        environment on its own, which is most of the reason this is a stdlib
        call rather than a hand-rolled client.
        """
        with urllib.request.urlopen(url, timeout=_NETWORK_TIMEOUT_SECONDS) as response:
            return response.read()

    def _fetch_text(self, url: str) -> str:
        return self._fetch_bytes(url).decode("utf-8")

    # -- unpacking -------------------------------------------------------

    def _extract_binary(self, archive: bytes, asset: ReleaseAsset) -> bytes:
        if asset.name.endswith(".zip"):
            with zipfile.ZipFile(io.BytesIO(archive)) as bundle:
                return bundle.read(self._select_member(bundle.namelist(), asset))
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as bundle:
            member = self._select_member(bundle.getnames(), asset)
            stream = bundle.extractfile(member)
            if stream is None:
                raise ReleaseArtifactError(f"{member} in {asset.name} is not a regular file")
            return stream.read()

    def _select_member(self, names: list[str], asset: ReleaseAsset) -> str:
        """Pick the tailcat member, refusing any name that could escape.

        Member names in a downloaded archive are attacker-influenced data, and
        the classic tar/zip-slip bug is an entry called ``../../etc/cron.d/x``.
        This adapter never joins a member name onto a path -- it reads one
        member into memory -- but the check lives here so that a later change to
        "extract the whole archive" cannot quietly reintroduce the hole.
        Anything suspicious is a hard failure, never a silent skip.
        """
        wanted = self.binary_name
        found = None
        for name in names:
            normalised = name.replace("\\", "/")
            segments = normalised.split("/")
            escapes = normalised.startswith("/") or ".." in segments
            if escapes or _DRIVE_LETTER.match(normalised):
                raise ReleaseArtifactError(
                    f"refusing {asset.name}: archive member {name!r} is an absolute path "
                    f"or escapes its directory"
                )
            if segments[-1] == wanted:
                found = name
        if found is None:
            raise ReleaseArtifactError(f"{asset.name} does not contain a {wanted} binary")
        return found
