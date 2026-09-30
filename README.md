# devtunnel

A cross-platform CLI that provisions **git**, **ngrok**, and **OpenSSH** to
open a dev-tunnel for SSH — and fully reverses itself on `uninstall`, removing
only what it installed and never touching anything that was already there.

Supports Windows 10/11 and Debian-based Linux (Debian, Ubuntu, Mint, ...).

## Why not just run a shell script?

Because uninstalling one is the hard part. Every change devtunnel makes is
recorded in a journal *before* it happens, together with whatever the machine
looked like beforehand. `devtunnel uninstall` replays that journal backwards:
anything devtunnel found already present is left alone; anything it changed
is restored to its exact prior state, not just deleted. See
[`docs/architecture.md`](docs/architecture.md) for the full design.

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

## Usage

```bash
# Windows: run from an elevated (Administrator) terminal.
# Linux: run with sudo.

devtunnel install                       # prompts for a git identity (optional) and an ngrok authtoken
devtunnel install --non-interactive --authtoken $NGROK_AUTHTOKEN   # git identity stays untouched
devtunnel install --dry-run             # show the plan without changing anything

devtunnel up                            # opens a foreground SSH tunnel; Ctrl+C to close
devtunnel up --port 22 --region eu

devtunnel status                        # what has devtunnel installed here?
devtunnel doctor                        # has anything drifted since install?

devtunnel uninstall                     # reverses everything, in reverse order
devtunnel uninstall --keep git          # revert everything except git
devtunnel uninstall --dry-run

uv tool uninstall devtunnel             # remove the CLI itself, once uninstall is clean
```

The ngrok authtoken resolves in this order: `NGROK_AUTHTOKEN` environment
variable → `--authtoken` flag (or a `--config` JSON file's `ngrok_authtoken`
key) → an interactive prompt. `--non-interactive` fails loudly instead of
prompting if nothing else supplied it.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```

## License

Internal tooling — see your organization's usage policy.
