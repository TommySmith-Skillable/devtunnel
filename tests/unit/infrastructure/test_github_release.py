from __future__ import annotations

import hashlib
import json
import os
import ssl
import tarfile
import urllib.error
import zipfile

import pytest

from devtunnel.application.ports.process_runner import CompletedProcess
from devtunnel.infrastructure.release.github_release import (
    ChecksumMismatchError,
    GitHubReleaseInstaller,
    ReleaseArtifactError,
    ReleaseTrustError,
    UnsupportedArchitectureError,
    _build_tls_context,
    _tls_context,
)
from tests.fakes.fake_ports import FakeFileSystem, FakeProcessRunner

BINARY_PAYLOAD = b"\x7fELF not really, but it hashes the same way"
TARGET = "/opt/devtunnel/bin/tailcat"


class EmptyPathProcessRunner(FakeProcessRunner):
    """FakeProcessRunner.which() resolves every executable, which would make
    every install look like it found a pre-existing tailcat."""

    def which(self, executable: str) -> str | None:
        return None


class OfflineInstaller(GitHubReleaseInstaller):
    """Replaces the one network seam with a dict, so the tests exercise the real
    resolution, verification and unpacking code with no socket in sight."""

    def __init__(self, *args, payloads: dict[str, bytes] | None = None, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.payloads = dict(payloads or {})
        self.fetched: list[str] = []

    def _fetch_bytes(self, url: str) -> bytes:
        self.fetched.append(url)
        if url not in self.payloads:
            raise AssertionError(f"unexpected network fetch: {url}")
        return self.payloads[url]


def make_tar_gz(tmp_path, payload: bytes, *, member: str = "tailcat") -> bytes:
    source = tmp_path / "member.bin"
    source.write_bytes(payload)
    archive = tmp_path / "artifact.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(source, arcname=member)
    return archive.read_bytes()


def make_zip(tmp_path, payload: bytes, *, member: str = "tailcat.exe") -> bytes:
    archive = tmp_path / "artifact.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr(member, payload)
    return archive.read_bytes()


def checksums_for(asset_name: str, archive: bytes, *, digest: str | None = None) -> bytes:
    """Real releases list every artifact, so the installer has to pick its line
    out rather than trust the first one."""
    sha = digest or hashlib.sha256(archive).hexdigest()
    lines = [
        f"{'0' * 64}  tailcat_0.7.0_some_other_platform.tar.gz",
        f"{sha}  {asset_name}",
        f"{'f' * 64}  tailcat_0.7.0_yet_another.zip",
    ]
    return ("\n".join(lines) + "\n").encode("utf-8")


def build_installer(
    tmp_path,
    *,
    archive: bytes,
    system: str = "Linux",
    machine: str = "x86_64",
    digest: str | None = None,
    filesystem: FakeFileSystem | None = None,
    process=None,
    version: str | None = None,
) -> OfflineInstaller:
    filesystem = filesystem if filesystem is not None else FakeFileSystem()
    process = process if process is not None else EmptyPathProcessRunner()
    installer = OfflineInstaller(
        process,
        filesystem,
        cache_dir=str(tmp_path / "cache"),
        system=system,
        machine=machine,
        version=version,
    )
    asset = installer.resolve_asset()
    installer.payloads = {
        asset.download_url: archive,
        asset.checksums_url: checksums_for(asset.name, archive, digest=digest),
    }
    return installer


@pytest.mark.parametrize(
    ("system", "machine", "expected_name"),
    [
        ("Linux", "x86_64", "tailcat_0.7.0_linux_amd64.tar.gz"),
        ("Linux", "aarch64", "tailcat_0.7.0_linux_arm64.tar.gz"),
        ("Linux", "armv7l", "tailcat_0.7.0_linux_armv7.tar.gz"),
        ("Linux", "armv6l", "tailcat_0.7.0_linux_armv7.tar.gz"),
        ("Windows", "AMD64", "tailcat_0.7.0_windows_amd64.zip"),
        ("Windows", "ARM64", "tailcat_0.7.0_windows_arm64.zip"),
    ],
)
def test_arch_mapping_resolves_the_published_asset_name_for_each_supported_host(
    system, machine, expected_name
):
    installer = GitHubReleaseInstaller(
        EmptyPathProcessRunner(), FakeFileSystem(), system=system, machine=machine
    )

    asset = installer.resolve_asset()

    assert asset.name == expected_name
    assert asset.tag == "v0.7.0"
    assert asset.version == "0.7.0"
    assert asset.download_url.endswith(f"/releases/download/v0.7.0/{expected_name}")
    assert asset.checksums_url.endswith("/releases/download/v0.7.0/checksums.txt")


def test_unknown_architecture_fails_by_name_instead_of_falling_back_to_amd64():
    installer = GitHubReleaseInstaller(
        EmptyPathProcessRunner(), FakeFileSystem(), system="Linux", machine="riscv64"
    )

    with pytest.raises(UnsupportedArchitectureError) as raised:
        installer.resolve_asset()

    assert "riscv64" in str(raised.value)


def test_windows_on_armv7_fails_rather_than_guessing_an_asset_that_does_not_exist():
    installer = GitHubReleaseInstaller(
        EmptyPathProcessRunner(), FakeFileSystem(), system="Windows", machine="armv7l"
    )

    with pytest.raises(UnsupportedArchitectureError) as raised:
        installer.resolve_asset()

    message = str(raised.value)
    assert "windows/armv7" in message
    assert "armv7l" in message


def test_unsupported_operating_system_fails_by_name():
    installer = GitHubReleaseInstaller(
        EmptyPathProcessRunner(), FakeFileSystem(), system="Darwin", machine="arm64"
    )

    with pytest.raises(UnsupportedArchitectureError) as raised:
        installer.resolve_asset()

    assert "Darwin" in str(raised.value)


def test_checksum_mismatch_aborts_before_anything_is_written_to_the_target_path(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_tar_gz(tmp_path, BINARY_PAYLOAD)
    installer = build_installer(
        tmp_path, archive=archive, filesystem=filesystem, digest="a" * 64
    )

    with pytest.raises(ChecksumMismatchError) as raised:
        installer.install(TARGET)

    assert TARGET not in filesystem.files
    assert "a" * 64 in str(raised.value)


def test_a_verified_download_is_installed_and_journals_the_digest_and_version(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_tar_gz(tmp_path, BINARY_PAYLOAD)
    installer = build_installer(tmp_path, archive=archive, filesystem=filesystem)

    details = installer.install(TARGET)

    assert filesystem.files[TARGET] == BINARY_PAYLOAD
    assert details["path"] == TARGET
    assert details["version"] == "0.7.0"
    assert details["tag"] == "v0.7.0"
    assert details["asset"] == "tailcat_0.7.0_linux_amd64.tar.gz"
    assert details["sha256"] == hashlib.sha256(archive).hexdigest()
    assert details["preexisting"] is False
    assert "/opt/devtunnel/bin" in details["created_dirs"]


def test_a_windows_zip_release_is_unpacked_to_the_exe_target(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_zip(tmp_path, BINARY_PAYLOAD)
    installer = build_installer(
        tmp_path,
        archive=archive,
        filesystem=filesystem,
        system="Windows",
        machine="AMD64",
    )

    details = installer.install("C:/devtunnel/bin/tailcat.exe")

    assert filesystem.files["C:/devtunnel/bin/tailcat.exe"] == BINARY_PAYLOAD
    assert details["asset"] == "tailcat_0.7.0_windows_amd64.zip"


def test_a_preexisting_tailcat_on_path_is_adopted_and_nothing_is_written(tmp_path):
    filesystem = FakeFileSystem()
    filesystem.files["/usr/bin/tailcat"] = "installed by the user, not by devtunnel"
    process = FakeProcessRunner()  # resolves every executable, including tailcat
    installer = build_installer(
        tmp_path,
        archive=make_tar_gz(tmp_path, BINARY_PAYLOAD),
        filesystem=filesystem,
        process=process,
    )

    details = installer.install(TARGET)

    assert details["preexisting"] is True
    assert details["path"] == "/usr/bin/tailcat"
    assert details["created_dirs"] == []
    assert TARGET not in filesystem.files
    assert installer.fetched == []


def test_uninstall_never_removes_a_preexisting_binary(tmp_path):
    filesystem = FakeFileSystem()
    filesystem.files["/usr/bin/tailcat"] = "installed by the user, not by devtunnel"
    installer = build_installer(
        tmp_path,
        archive=make_tar_gz(tmp_path, BINARY_PAYLOAD),
        filesystem=filesystem,
        process=FakeProcessRunner(),
    )
    details = installer.install(TARGET)

    installer.uninstall(details)

    assert filesystem.files["/usr/bin/tailcat"] == "installed by the user, not by devtunnel"


def test_uninstall_removes_the_recorded_path_and_the_directories_it_created(tmp_path):
    filesystem = FakeFileSystem()
    installer = build_installer(
        tmp_path, archive=make_tar_gz(tmp_path, BINARY_PAYLOAD), filesystem=filesystem
    )
    details = installer.install(TARGET)

    installer.uninstall(details)

    assert TARGET not in filesystem.files
    assert "/opt/devtunnel/bin" not in filesystem.dirs


def test_latest_resolves_the_version_from_the_releases_api_tag_name(tmp_path):
    installer = OfflineInstaller(
        EmptyPathProcessRunner(),
        FakeFileSystem(),
        version="latest",
        system="Linux",
        machine="x86_64",
        payloads={
            "https://api.github.com/repos/tailscale/tailcat/releases/latest": json.dumps(
                {"tag_name": "v0.9.1", "name": "tailcat 0.9.1"}
            ).encode("utf-8")
        },
    )

    asset = installer.resolve_asset()

    assert asset.version == "0.9.1"
    assert asset.tag == "v0.9.1"
    assert asset.name == "tailcat_0.9.1_linux_amd64.tar.gz"


def test_a_pinned_version_never_touches_the_releases_api():
    installer = OfflineInstaller(
        EmptyPathProcessRunner(),
        FakeFileSystem(),
        version="0.7.0",
        system="Linux",
        machine="x86_64",
        payloads={},
    )

    assert installer.resolve_asset().tag == "v0.7.0"
    assert installer.fetched == []


def test_a_cached_artifact_is_reused_but_still_checksum_verified(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_tar_gz(tmp_path, BINARY_PAYLOAD)
    installer = build_installer(tmp_path, archive=archive, filesystem=filesystem)
    asset = installer.resolve_asset()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = os.path.join(str(cache_dir), asset.name)
    (cache_dir / asset.name).write_bytes(archive)
    filesystem.files[cache_path] = "present"
    del installer.payloads[asset.download_url]

    details = installer.install(TARGET)

    assert installer.fetched == [asset.checksums_url]
    assert filesystem.files[TARGET] == BINARY_PAYLOAD
    assert details["sha256"] == hashlib.sha256(archive).hexdigest()


def test_a_corrupted_cached_artifact_is_rejected_rather_than_trusted(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_tar_gz(tmp_path, BINARY_PAYLOAD)
    installer = build_installer(tmp_path, archive=archive, filesystem=filesystem)
    asset = installer.resolve_asset()
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cache_path = os.path.join(str(cache_dir), asset.name)
    (cache_dir / asset.name).write_bytes(b"tampered with after download")
    filesystem.files[cache_path] = "present"

    with pytest.raises(ChecksumMismatchError):
        installer.install(TARGET)

    assert TARGET not in filesystem.files


def test_an_archive_member_that_escapes_its_directory_is_refused(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_tar_gz(tmp_path, BINARY_PAYLOAD, member="../../etc/cron.d/tailcat")
    installer = build_installer(tmp_path, archive=archive, filesystem=filesystem)

    with pytest.raises(ReleaseArtifactError) as raised:
        installer.install(TARGET)

    assert "escapes its directory" in str(raised.value)
    assert TARGET not in filesystem.files


def test_an_archive_without_a_tailcat_member_is_a_named_failure(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_tar_gz(tmp_path, BINARY_PAYLOAD, member="README.md")
    installer = build_installer(tmp_path, archive=archive, filesystem=filesystem)

    with pytest.raises(ReleaseArtifactError) as raised:
        installer.install(TARGET)

    assert "does not contain a tailcat binary" in str(raised.value)


def test_checksums_without_a_line_for_the_asset_abort_the_install(tmp_path):
    filesystem = FakeFileSystem()
    archive = make_tar_gz(tmp_path, BINARY_PAYLOAD)
    installer = build_installer(tmp_path, archive=archive, filesystem=filesystem)
    asset = installer.resolve_asset()
    installer.payloads[asset.checksums_url] = b"%s  something_else.tar.gz\n" % (b"0" * 64)

    with pytest.raises(ReleaseArtifactError):
        installer.install(TARGET)

    assert TARGET not in filesystem.files


def test_is_installed_is_a_filesystem_question_not_a_process_one():
    filesystem = FakeFileSystem()
    installer = GitHubReleaseInstaller(
        EmptyPathProcessRunner(), filesystem, system="Linux", machine="x86_64"
    )

    assert installer.is_installed(TARGET) is False

    filesystem.files[TARGET] = "binary"

    assert installer.is_installed(TARGET) is True


def test_installed_version_parses_the_semver_printed_by_the_binary():
    process = EmptyPathProcessRunner()
    process.responses[(TARGET, "version")] = CompletedProcess(
        (TARGET, "version"), 0, "tailcat version 0.7.0\n", ""
    )
    installer = GitHubReleaseInstaller(
        process, FakeFileSystem(), system="Linux", machine="x86_64"
    )

    assert installer.installed_version(TARGET) == "0.7.0"


def test_installed_version_is_none_when_the_binary_cannot_answer():
    process = EmptyPathProcessRunner()
    for argv in ((TARGET, "version"), (TARGET, "--version")):
        process.responses[argv] = CompletedProcess(argv, 127, "", "not executable")
    installer = GitHubReleaseInstaller(
        process, FakeFileSystem(), system="Linux", machine="x86_64"
    )

    assert installer.installed_version(TARGET) is None


# -- TLS trust ------------------------------------------------------------


def ssl_url_error(message: str = "unable to get local issuer certificate") -> urllib.error.URLError:
    """The shape urlopen raises when the chain cannot be verified: the
    SSLCertVerificationError arrives wrapped as the URLError's reason."""
    reason = ssl.SSLCertVerificationError(message)
    reason.verify_message = message
    return urllib.error.URLError(reason)


def test_release_hosts_are_verified_against_the_operating_system_store():
    """The bug this guards: OpenSSL's snapshot of the Windows store is empty on
    a freshly imaged host, so verification has to go through the platform."""
    _build_tls_context.cache_clear()
    context = _tls_context()

    assert type(context).__module__.startswith("truststore")


def test_an_explicit_ca_bundle_replaces_operating_system_verification(tmp_path, monkeypatch):
    # Any real certificate will do -- load_verify_locations rejects an empty
    # file, and the point of the test is which store gets consulted.
    roots = ssl.create_default_context().get_ca_certs(binary_form=True)
    if not roots:
        pytest.skip("this host has no root certificates to borrow for the bundle")
    bundle = tmp_path / "corp-root.pem"
    bundle.write_text(ssl.DER_cert_to_PEM_cert(roots[0]))
    monkeypatch.setenv("DEVTUNNEL_CA_BUNDLE", str(bundle))
    _build_tls_context.cache_clear()

    context = _tls_context()

    assert type(context).__module__.startswith("ssl")
    assert context.verify_mode is ssl.CERT_REQUIRED


def test_a_certificate_failure_names_the_host_and_the_way_out(tmp_path):
    class UnverifiableInstaller(GitHubReleaseInstaller):
        def _open(self, url, timeout, context):
            raise ssl_url_error()

    installer = UnverifiableInstaller(
        EmptyPathProcessRunner(), FakeFileSystem(), system="Linux", machine="x86_64"
    )

    with pytest.raises(ReleaseTrustError) as caught:
        installer._fetch_bytes("https://objects.githubusercontent.com/tailcat.tar.gz")

    message = str(caught.value)
    assert "objects.githubusercontent.com" in message
    assert "unable to get local issuer certificate" in message
    assert "DEVTUNNEL_CA_BUNDLE" in message


def test_an_http_error_is_not_mistaken_for_a_trust_failure():
    """HTTPError is a URLError whose reason is a plain string -- a 404 on a
    missing asset must keep its own handling rather than be reported as a
    certificate problem."""

    class MissingAssetInstaller(GitHubReleaseInstaller):
        def _open(self, url, timeout, context):
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

    installer = MissingAssetInstaller(
        EmptyPathProcessRunner(), FakeFileSystem(), system="Linux", machine="x86_64"
    )

    with pytest.raises(urllib.error.HTTPError):
        installer._fetch_bytes("https://github.com/x/releases/download/v0.7.0/missing.tar.gz")
