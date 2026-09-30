# Plan: a git-free install path

Status: decided and applied to `README.md`. The one remaining step is
creating the `v0.0.1` tag itself, which has to wait until this change is
committed and pushed (see "Open decisions" below).

## Problem

The documented install is:

```bash
uv tool install git+https://github.com/TommySmith-Skillable/devtunnel
```

A `git+https://` source makes uv shell out to a `git` binary. On a fresh
machine that binary is exactly what is missing — and installing it is one of
the things devtunnel exists to do. The install instruction therefore assumes
the state it is supposed to create.

This bites hardest in the target scenario: a clean Windows box where the user
has no git, and so also no Git Bash, and is working in PowerShell.

## Proposed fix

Install from GitHub's source archive instead. uv fetches it over plain HTTPS
and builds it as a source distribution; git is never invoked.

```bash
uv tool install https://github.com/TommySmith-Skillable/devtunnel/archive/refs/tags/v0.0.1.tar.gz
```

Pointing at a tag rather than `refs/heads/main` (see decision 1, now settled)
means the URL is pinned: it keeps resolving to the same code even after main
moves on, and a reader can tell which version they installed from the URL
alone.

## Verification

Checked on Windows 11, uv 0.9.7, against the public repo (against `main`,
since the `v0.0.1` tag does not exist yet — the archive mechanism is identical
for a tag URL, only the ref differs). Each `uv tool install` ran with
`UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` pointed at a throwaway directory, so
nothing was written to a real tool dir.

| Check | Result |
| --- | --- |
| `uv pip install --dry-run <tarball>` | resolved and built devtunnel + 19 deps |
| `uv tool install <tarball>` from Git Bash | `Installed 1 executable: devtunnel` |
| `uv tool install <tarball>` from native PowerShell | `Installed 1 executable: devtunnel` |
| `devtunnel.exe --help` on the result | runs, prints the command table |

The PowerShell run matters on its own: Git Bash ships with git, so a bash-only
test would have proved nothing about the machine this is actually for.

Two supporting facts, both true of any supported Windows machine:

- uv installs from a PowerShell one-liner (`irm astral.sh/uv/install.ps1 | iex`)
  that pulls a prebuilt binary. No git, Python or build toolchain needed first.
- Unpacking `.tar.gz` is not a gap. uv handles the archive itself, and
  `tar.exe` has been in-box since Windows 10 1803 (`C:\WINDOWS\system32\tar.exe`)
  regardless. Note that because `tar.exe` is present on the test machine, the
  runs above cannot distinguish internal unpacking from a call out to it — the
  distinction has no practical consequence, but it was not proven either way.

## Applied README section

Already written into `README.md`'s `## Install` block.

````markdown
## Install

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — uv's
own installer is a standalone script and needs no git.

```bash
uv tool install https://github.com/TommySmith-Skillable/devtunnel/archive/refs/tags/v0.0.1.tar.gz
```

No git required — which matters, since installing git is one of the things
devtunnel does for you.

On a first-ever `uv tool install`, uv's bin directory may not be on `PATH` yet.
If `devtunnel` is not found, run `uv tool update-shell` and open a new terminal.

To upgrade to a newer tag, re-run the command above with `--force` and the new
tag in the URL.

If you already have git and want to track the repo directly:

```bash
uv tool install git+https://github.com/TommySmith-Skillable/devtunnel
```
````

The `PATH` note is new. uv warns about this at install time, but the current
README runs Install straight into Usage, so a reader hits "command not found"
between two steps that look continuous.

## Open decisions

1. **Pinning — settled: tags.** Tagging releases and pointing at
   `refs/tags/<tag>.tar.gz` pins the install, unlike `refs/heads/main` which
   always fetches current main (and gives uv no way to tell an upgrade from a
   reinstall). First tag will be `v0.0.1`. The tag does not exist on GitHub
   yet — it has to be cut from a commit that includes this change, so
   creating `v0.0.1` and pushing it is the follow-up step once this is
   committed. Until then the `v0.0.1` URL in `README.md` will 404.
2. **Release artifacts.** Building a wheel and attaching it to a GitHub
   Release would give a versioned `.whl` URL and remove the client-side build
   step entirely. Possible later; not needed now.
3. **Package management (PyPI or private).** Would reduce the whole thing to
   `uv tool install devtunnel` (public PyPI) or a private index. Bigger
   decision — a public name is permanent, and a private index is more
   infrastructure — so it stays listed, not pursued now.

## Already done

`README.md` line 23 pointed at `git+https://gitlab.com/lab-dev-tools/devtunnel`,
a repo that is not this project's home. Corrected to the GitHub URL. That edit
is in the working tree, uncommitted, alongside this file.

`docs/architecture.md` also links to gitlab.com, but that reference is to
`lab-dev-tools/windows-dev` — a distinct prior project — and was deliberately
left alone.

The `## Install` block itself has also been rewritten per this plan (see
"Applied README section" above). That edit is likewise uncommitted.
