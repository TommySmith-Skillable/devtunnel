"""Port for installing a standalone executable from a release artifact.

Earned by D2: tailcat arrives as a checksum-verified GitHub release archive,
not as a package, so none of the package ports fit. ``PackageManagerPort``
answers "is this named thing present according to the OS?", which is a
question an unpacked binary at a devtunnel-chosen path cannot answer.

One adapter (:class:`devtunnel.infrastructure.release.github_release.GitHubReleaseInstaller`)
covers both platforms, which is why this hangs off ``ExecutionContext``
directly rather than off the per-platform ``PlatformToolkit``: the only
platform-specific part is the asset name, and resolving that is the adapter's
own job.
"""

from __future__ import annotations

from typing import Protocol


class BinaryInstallerPort(Protocol):
    def is_installed(self, target_path: str) -> bool: ...

    def installed_version(self, target_path: str) -> str | None:
        """The version of the binary at ``target_path``, or ``None`` if it is
        absent or does not answer a version query."""

    def install(self, target_path: str) -> dict:
        """Download, verify and unpack the binary to ``target_path``.

        Verification is part of the contract, not an option: devtunnel
        downloads an executable and then runs it, so an implementation must
        abort before writing anything if the checksum does not match.

        Returns a ``details`` dict for the journal. It must record enough for
        :meth:`uninstall` to reverse exactly this install -- including whether
        a pre-existing binary was adopted rather than installed, since
        devtunnel never removes what it did not put there.
        """

    def uninstall(self, details: dict) -> None:
        """Reverse :meth:`install` using exactly its ``details`` dict."""
