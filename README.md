# devtunnel

A cross-platform CLI that sets up **both ends** of an SSH connection over
[tailcat](https://github.com/tailscale/tailcat) — point-to-point WireGuard with
no Tailscale control plane. There is no account to create and no vendor
authtoken to paste: identity is a keypair generated on your machine. The
default install needs **no Administrator and no sudo** — tailcat is a
user-scope binary that serves SSH itself — and every change devtunnel makes is
journaled with its prior state, so `devtunnel uninstall` puts the machine back
the way it was. Supports Windows 10/11 and Debian-based Linux.

## Install the CLI

Requires [`uv`](https://docs.astral.sh/uv/getting-started/installation/) — uv's
own installer is a standalone script and needs no git.

```bash
uv tool install https://github.com/TommySmith-Skillable/devtunnel/archive/refs/tags/v0.0.1.tar.gz
```

On a first-ever `uv tool install`, uv's bin directory may not be on `PATH` yet.
If `devtunnel` is not found, run `uv tool update-shell` and open a new terminal.
To upgrade, re-run the command with `--force` and a newer tag.

This installs the CLI only. tailcat itself arrives with `devtunnel install`,
from GitHub Releases, with a mandatory checksum check.

## Getting a shell: the two-sided walkthrough

A tailcat connection has two ends and both need provisioning. The **host** is
the machine you want a shell on; the **client** is the machine you are sitting
at. Do these in order.

### 1. Client: install and produce a pairing bundle

```console
$ devtunnel install --client
✓ Install tailcat 0.7.0
✓ Generate the tailcat client key 'client-default'
✓ Create or adopt an SSH identity

Your pairing bundle -- send this to whoever runs the host:

  dtp1:eyJ2IjoxLCJuYW1lIjoidG9tbXlAbGFwdG9wIiwibm9kZWtleSI6Im5vZGVrZXk6Y2Zi...

  tunnel key   nodekey:cfb6bfa7...
  shell key    ssh-ed25519 AAAAC3Nz...
  fingerprint  4f2a-91c3-de07-b85e

They run:  devtunnel pair add dtp1:...
```

Send the `dtp1:` token to whoever runs the host, by any means — chat, email, a
ticket. It contains only public keys. Tell them the fingerprint **separately**,
over a channel they already trust you on.

### 2. Host: install, then add the peer

```console
$ devtunnel install
Using /home/ops/.ssh/id_ed25519.pub as the authorized key.
✓ Install tailcat 0.7.0
✓ Install the authorized SSH keys
✓ Generate the tailcat key 'default'

Install complete.
Your address: tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu
Next: 'devtunnel pair add <bundle>' to authorise a client, then 'devtunnel up'.
```

```console
$ devtunnel pair add dtp1:eyJ2IjoxLCJuYW1lIjoidG9tbXlAbGFwdG9w...
Peer "tommy@laptop"
  tunnel key   nodekey:cfb6bfa7...
  shell key    ssh-ed25519 AAAAC3Nz...
  fingerprint  4f2a-91c3-de07-b85e
Confirm this fingerprint with them out of band. Add this peer? [y/N]: y
  + allowlist          nodekey:cfb6bfa7...
  + authorized_keys    ssh-ed25519 AAAAC3Nz...

Send them back:
  devtunnel connect tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu

The allowlist changed. Restart the tunnel for it to take effect
('devtunnel serve --stop' then '--install-service', or Ctrl+C a foreground 'up').
```

`pair add` does **not** restart a running tunnel for you. A tunnel reads its
allowlist once, at startup, and auto-restarting would drop whoever is connected
through it right now — so devtunnel tells you and leaves the timing to you.

### 3. Host: open the tunnel

```console
$ devtunnel up
devtunnel connect tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu
  (raw: tailcat ssh tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu)
Press Ctrl+C to close the tunnel.
```

`up` is foreground and closes on Ctrl+C. For an always-on host, see
[persistent service mode](#persistent-service-mode) below.

### 4. Client: connect

```console
$ devtunnel connect tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu
ops@host:~$
```

To pin the host as well as being pinned by it, pass `--expect`. devtunnel
decodes the address locally (`tailcat parse`) and refuses to connect if the
server key inside it is not the one you expected:

```console
$ devtunnel connect tcomFw... --expect nodekey:9c8d2e67...
```

## Why there are two keys

A client is useless to a host until **two** public keys are installed there, and
each one gates a different thing:

| Key | Created by | Gates | Lands on the host in |
|---|---|---|---|
| tailcat node key (`nodekey:<hex>`) | `tailcat genkey --client` | the **tunnel** — may this peer connect at all | the server's allowlist |
| SSH public key (`ssh-ed25519 AAAA…`) | `ssh-keygen` | the **shell** — which user may log in | the server's `authorized_keys` |

Either one alone is a broken state, and each fails differently: allow-listed but
not authorized is a tunnel that refuses to log in; authorized but not
allow-listed leaves a working shell credential on the box for someone who can no
longer reach it — and nothing will remind you it is there. That is why the two
travel as one `dtp1:` bundle, are installed by one command (`pair add`), and are
revoked by one command (`pair remove`), as a single journal record. `allow` and
`authorize` exist underneath for operators who genuinely need one half at a
time; prefer `pair`.

## Command reference

| Command | What it does |
|---|---|
| `devtunnel install` | Host side: install tailcat, write `authorized_keys`, generate the server key |
| `devtunnel install --client` | Client side: install tailcat, generate a client key and an SSH identity, print the bundle |
| `devtunnel install --with-git` | Also install git. The one path that needs elevation |
| `devtunnel install --dry-run` | Print the plan without changing anything |
| `devtunnel uninstall` | Reverse every journaled change, newest first |
| `devtunnel pair export` | Print this machine's bundle (`--out <file>`, `--name <label>`) |
| `devtunnel pair add <bundle>` | Authorise a peer: allowlist + `authorized_keys` (`--from <file>`, `--yes`) |
| `devtunnel pair list` | Every enrolled peer (`--json`) |
| `devtunnel pair remove <name\|nodekey>` | Revoke both halves, or fail loudly |
| `devtunnel pair fingerprint [bundle]` | The short digest to compare out of band |
| `devtunnel key show` | This machine's `nodekey:<hex>` |
| `devtunnel key list` | Known tailcat keys and their addresses |
| `devtunnel address` | The stable `tc…` address peers connect to |
| `devtunnel allow <nodekey>…` | Edit the allowlist directly (`--remove`, `--list`) |
| `devtunnel authorize <key\|path\|user@github>…` | Edit `authorized_keys` directly |
| `devtunnel up` | Foreground tunnel (`--serve`, `--key-name`, `--allow`, `--region`, `--bind`, `--open`) |
| `devtunnel serve --install-service` | Register the tunnel as a persistent service |
| `devtunnel connect <address>` | SSH to a host over tailcat (`--expect`, `--key-name`) |
| `devtunnel status` | What devtunnel installed here, and whether a tunnel is listening |
| `devtunnel doctor` | Has anything drifted since install? |

Useful `install` flags: `--authorized-keys <path\|pubkey\|user@github>`,
`--allow <nodekey>`, `--peer <bundle>` (enrol peers during install),
`--key-name`, `--region` / `--fixed-region`, `--tailcat-version <v>|latest`,
`--add-to-path`, `--system`, `--skip <package>`, `--config <file>`,
`--non-interactive`, `--json`.

`devtunnel authorize --remove` deliberately refuses and points you at
`devtunnel pair remove`. Removing one key by hand is exactly how you end up
with a peer that is still allow-listed, and the tool will not help you do it.

`--ssh-identity` works on `install --client` (it generates or adopts a key at
that path) but is **refused** by `devtunnel connect`: `tailcat ssh` wraps the
stock ssh client behind a ProxyCommand and documents no `-i` passthrough, so
devtunnel cannot ask for a non-default identity at connect time. Use `ssh-agent`
instead of connecting as the wrong key.

## Persistent service mode

`up` dies with your terminal. For a host that should stay reachable:

```console
$ devtunnel serve --install-service
Tunnel service registered and running.

$ devtunnel serve --status
exists=True running=True automatic=True

$ devtunnel serve --stop
$ devtunnel serve --remove-service
```

On Linux this is a **systemd user unit** (`~/.config/systemd/user/devtunnel-tailcat.service`);
on Windows a per-user **Scheduled Task at logon** registered `/RL LIMITED`.
Neither needs elevation. `--system` registers a real system unit or Windows
service instead, and does.

The honest caveat: a systemd *user* instance is torn down at logout unless the
account is lingering, and `loginctl enable-linger` needs polkit or root — it
routinely fails on a headless host, while `systemctl --user is-enabled` goes on
reporting `enabled`. devtunnel does not treat that as success:

```console
$ devtunnel serve --install-service
WARNING: loginctl enable-linger ops failed (exit 1): ...
The tunnel will NOT survive logout. Re-run with --system for a service that does.
```

If lingering was already on before devtunnel ran, uninstall leaves it on — it
only disables what it enabled.

## Uninstall

```console
$ devtunnel uninstall
$ devtunnel uninstall --dry-run      # show what would be reverted
$ devtunnel uninstall --keep git     # revert everything except git
$ devtunnel uninstall --force        # continue past a failed revert
```

Changes unwind in reverse order of how they were made: peers → SSH identity →
tailcat key → `authorized_keys` → service → binary → OpenSSH client → git →
Chocolatey. Peers go first on purpose: access is revoked before the machinery
that enforces it is removed, so an interrupted uninstall never leaves a live
credential behind.

What it will **not** remove:

- **A tailcat you already had.** If `tailcat` was on `PATH` first, that binary
  is used, recorded as `SKIPPED_PREEXISTING`, and never deleted. Same rule for a
  package that was already installed.
- **An SSH key you already had.** `install --client` adopts an existing
  `~/.ssh/id_ed25519` (then `id_ecdsa`, then `id_rsa`) and journals nothing, so
  uninstall cannot delete it. Only a keypair devtunnel generated is removed.
- **Anything outside the journal.** `authorized_keys` is restored from the
  backup taken before devtunnel appended to it, not truncated.

Once uninstall reports clean, `uv tool uninstall devtunnel` removes the CLI.

## Security notes

- **`authorized_keys` is the entire authentication boundary.** tailcat serves
  SSH itself, so there is no sshd config, no PAM and no `AllowUsers`. The step
  that writes this file always backs it up, appends rather than overwrites,
  de-duplicates, and sets `0600` on the file and `0700` on `~/.ssh`.
- **Checksum verification is mandatory.** devtunnel downloads an executable and
  runs it, so the SHA-256 is checked against the release's `checksums.txt`
  before anything is written, and a mismatch aborts with nothing on disk. There
  is deliberately no `--no-verify` flag. The version is pinned by default;
  `--tailcat-version latest` is opt-in.
- **`--open` and `no-auth-ssh` mean the address is the credential.** An empty
  allowlist is a real tailcat mode, but it must be chosen: `up` refuses to start
  without `--open` when no peer is allow-listed, and `--serve no-auth-ssh`
  requires `--open` on top, so a shell-to-anyone configuration cannot be
  assembled by accident out of two innocuous flags.
- **A bundle is public material, but it is an authorization request.** It
  carries no secrets and can cross a chat window safely — but anyone who gets
  one into the host operator's clipboard is asking for both tunnel and shell
  access. Hence the fingerprint and the prompt. Check it out of band; `--yes`
  is where you take that verification on yourself.
- **Private keys are never journaled.** `~/.ssh/id_*` and
  `~/.config/tailcat/keys/*.private.json` are never read, copied or backed up.

## Config file

For unattended installs, pass `--config <file>`. Precedence is **flag > config
file > prompt**; there is no environment-variable tier (it existed only to carry
a vendor secret, and there is none any more).

```json
{
  "role": "server",
  "with_git": false,
  "git_name": null,
  "git_email": null,
  "tailcat_version": "0.7.0",
  "key_name": null,
  "region": null,
  "fixed_region": false,
  "authorized_keys": ["~/.ssh/id_ed25519.pub"],
  "allow": ["nodekey:cfb6bfa77a0654d7450947fd6acef17d2cd848da1d30b2540b13dac272ddfd16"],
  "peers": ["dtp1:eyJ2IjoxLCJuYW1l..."],
  "ssh_identity": null,
  "add_to_path": false,
  "skip_packages": []
}
```

`key_name: null` means "whichever default matches the role" — `default` for a
server, `client-default` for a client. Those are tailcat's own names, so a
hand-run `tailcat` with no flags picks up the same key devtunnel created.

## Development

```bash
uv sync --group dev
uv run pytest
uv run ruff check .
```

See [`docs/architecture.md`](docs/architecture.md) for the design and why it
looks like this.

## License

Internal tooling — see your organization's usage policy.
