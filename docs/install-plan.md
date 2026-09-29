# Plan: a git-free install path

Status: proposed, not yet applied. The only change on disk so far is the
corrected repo URL in `README.md` (see "Already done" below).

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
uv tool install https://github.com/TommySmith-Skillable/devtunnel/archive/refs/heads/main.tar.gz
```

## Verification

Checked on Windows 11, uv 0.9.7, against the public repo. Each `uv tool
install` ran with `UV_TOOL_DIR` and `UV_TOOL_BIN_DIR` pointed at a throwaway
directory, so nothing was written to a real tool dir.

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

## Proposed README section

Replaces the current `## Install` block.

````markdown
## Install

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — uv's
own installer is a standalone script and needs no git.

```bash
uv tool install https://github.com/TommySmith-Skillable/devtunnel/archive/refs/heads/main.tar.gz
```

No git required — which matters, since installing git is one of the things
devtunnel does for you.

On a first-ever `uv tool install`, uv's bin directory may not be on `PATH` yet.
If `devtunnel` is not found, run `uv tool update-shell` and open a new terminal.

If you already have git and want to track the repo:

```bash
uv tool install git+https://github.com/TommySmith-Skillable/devtunnel
```
````

The `PATH` note is new. uv warns about this at install time, but the current
README runs Install straight into Usage, so a reader hits "command not found"
between two steps that look continuous.

## Open decisions

1. **Pinning.** `refs/heads/main` always fetches current main, and uv cannot
   tell an upgrade from a reinstall, so updating means
   `uv tool install --force <url>`. Tagging releases and pointing at
   `refs/tags/v0.1.0.tar.gz` would pin instead. Untagged today.
2. **Release artifacts.** Building a wheel and attaching it to a GitHub Release
   gives a versioned `.whl` URL and removes the client-side build step
   entirely. More repo machinery; worth it if installs become frequent.
3. **PyPI.** Would reduce the whole thing to `uv tool install devtunnel`.
   Bigger decision — the name is public and permanent — so it is listed, not
   recommended.

## Already done

`README.md` line 23 pointed at `git+https://gitlab.com/lab-dev-tools/devtunnel`,
a repo that is not this project's home. Corrected to the GitHub URL. That edit
is in the working tree, uncommitted, alongside this file.

`docs/architecture.md` also links to gitlab.com, but that reference is to
`lab-dev-tools/windows-dev` — a distinct prior project — and was deliberately
left alone.
