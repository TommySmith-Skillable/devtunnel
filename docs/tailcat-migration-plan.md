# Technical plan: replace ngrok with tailcat

Status: proposed
Date: 2026-10-02
Supersedes: the ngrok provisioning described in [`architecture.md`](architecture.md)

---

## 1. Purpose and scope

devtunnel currently provisions **git + ngrok + OpenSSH** and opens a foreground
ngrok TCP tunnel to the local SSH daemon. This plan replaces ngrok with
[tailcat](https://github.com/tailscale/tailcat) — point-to-point WireGuard over
Tailscale's data plane, with no Tailscale control plane, no account and no root.

The goal is unchanged in spirit and larger in ambition: **devtunnel is the one
command that sets up everything needed for a tailcat connection, on both ends,
and fully reverses itself.**

In scope:

- Removing ngrok end to end (adapter, credential chain, apt repository).
- Installing the tailcat binary from GitHub Releases, with checksum verification.
- Key generation, address derivation and peer allowlisting.
- **Client identity**: creating *both* a tailcat node key and an SSH keypair on
  the client, and sharing them to the intended host as one pairing bundle (§12).
- A foreground tunnel (`up`) and a persistent background service (`serve`).
- A client-side install and a `connect` command.
- Keeping every mutation journaled and reversible.

Out of scope (see §18):

- System `sshd` as an alternative SSH backend.
- Keeping ngrok as a second provider.
- tailcat's file transfer, SOCKS, exit-node and perf subcommands.
- Publishing addresses as DNS TXT records.

---

## 2. Background: what actually changes

| Concern | ngrok (today) | tailcat |
|---|---|---|
| Identity | Vendor authtoken from a dashboard | WireGuard keypair generated locally |
| Secret storage | `~/.config/ngrok/ngrok.yml` | `~/.config/tailcat/keys/<name>.private.json` |
| Address | `0.tcp.ngrok.io:12345` (host + port) | One opaque blob: `tc…` |
| Address discovery | Poll `http://127.0.0.1:4040/api/tunnels` | Printed by `genkey`; echoed to **stderr** at startup |
| Privilege | Root/Administrator for install | None — no root, no routing or DNS changes |
| Authentication | None; sshd is the only gate | `--allow=nodekey:…` allowlist + WireGuard PSK |
| Distribution | apt repo (Debian) / Chocolatey (Windows) | GitHub Releases, Scoop, Snap, Homebrew |
| Client requirement | Stock `ssh` | tailcat on the client too |

Two consequences drive the whole redesign:

1. **A tailcat connection is two-sided.** ngrok only ever provisioned the
   server. devtunnel now needs a client story: install the binary, generate a
   client key, hand its node key to the server operator, connect.
2. **Nothing requires elevation any more.** The built-in SSH server plus a
   user-scope binary means a default install touches no system state at all.

---

## 3. Decisions

### D1 — SSH is served by tailcat's built-in server

`tailcat serve --ssh-authorized-keys=<path> ssh`.

Consequences: on the **server**, OpenSSH Server, the `sshd` service step and the
`sshd` `ServiceSpec` leave the plan entirely. `authorized_keys` becomes the
**sole** authentication boundary and is therefore promoted to a first-class,
journaled install step.

On the **client**, OpenSSH stays. `tailcat ssh` wraps the stock ssh client with
a ProxyCommand piping through tailcat, so `ssh` and `ssh-keygen` must be present
and `OPENSSH_CLIENT` remains in the catalog as a conditional step — see §12.1.

Rejected: fronting system `sshd` with `tailcat serve 22`. Smaller diff, but it
re-imposes elevation on every install for a capability most users of this tool
do not need. See §18 for how to add it back later.

### D2 — The binary comes from GitHub Releases

Download `tailcat_<ver>_<os>_<arch>.{tar.gz,zip}`, verify against
`checksums.txt`, unpack to a devtunnel-owned directory.

Consequences: one adapter covers both platforms. The apt-repository machinery
(keyring, `gpg --dearmor`, `sources.list.d`, `apt-get update`) is deleted
outright. Revert is `rm` of a file devtunnel placed.

Rejected: Scoop + `.deb`. tailcat is in Scoop's main bucket but **not** in
Chocolatey, so the Windows path would need a brand-new Scoop bootstrap adapter
purely for this one package. Deferred to an opt-in flag (§18).

### D3 — A persistent service mode is added

`devtunnel up` stays foreground. `devtunnel serve --install-service` registers a
journaled **systemd user unit** (Linux) or **Scheduled Task at logon** (Windows),
both of which avoid elevation, with `--system` available for a real system unit
or Windows service when running elevated.

Rationale: this is where devtunnel's journal keeps earning its place once the
install plan shrinks, and a stable always-on address is strictly better than
anything ngrok offered.

### D4 — ngrok is removed, not retained behind the port

`TunnelProviderPort` gets exactly one implementation. The port survives as a
test seam (`FakeTunnelProvider`), not as a provider abstraction.

Rationale: the two providers have incompatible shapes (authtoken vs keypair,
host+port vs opaque blob, apt repo vs release binary). A port spanning both
degrades to a lowest common denominator that serves neither.

### D5 (judgment call) — git becomes opt-in

With D1 and D2, installing git is the only remaining step that needs
Administrator/sudo. Leaving it in the default plan would single-handedly force
elevation on an otherwise unprivileged install.

`devtunnel install` no longer installs git. `devtunnel install --with-git` does,
and that invocation (and only that one) requires elevation.

### D6 (judgment call) — the journal splits by scope

`Scope` already exists in `domain/models.py` and is used nowhere. It becomes
load-bearing:

| Scope | Path (Linux) | Path (Windows) | Holds |
|---|---|---|---|
| `USER` | `~/.local/share/devtunnel/journal.json` | `%LOCALAPPDATA%\devtunnel\journal.json` | binary, keys, authorized_keys, allowlist, user service |
| `MACHINE` | `/var/lib/devtunnel/journal.json` | `%ProgramData%\devtunnel\journal.json` | git, system service |

`status`, `doctor` and `uninstall` read both and merge, newest-first across the
union. Only operations that wrote to the machine journal require elevation to
revert.

---

## 4. Target architecture

Layering is unchanged — this is the payoff of the existing Ports & Adapters
seam. `domain/journal.py`, `domain/plan.py`, `JournaledStep`, `PlanBuilder`,
`StepReverter` and the event bus survive untouched in structure.

```
domain/            + TunnelSpec/TunnelHandle reshaped, ServiceSpec.scope, Scope in use
application/       + key/allowlist/binary steps; - credential chain, repo provider
infrastructure/    + release/, tailcat/, user-scope services; - ngrok/, credentials/, apt_repository
config/            FileConfig reshaped
cli/               + key, allow, serve, connect commands
container.py       rewired; journal selection now scope-aware
```

---

## 5. Removals

Delete entirely:

- `src/devtunnel/infrastructure/ngrok/` (`ngrok_config.py`, `ngrok_tunnel.py`)
- `src/devtunnel/infrastructure/credentials/` (`chain.py`)
- `src/devtunnel/application/ports/credentials.py`
- `src/devtunnel/infrastructure/debian/apt_repository.py`
- `src/devtunnel/application/ports/repository_provider.py`
- `tests/unit/infrastructure/test_credential_chain.py`

**Not** deleted, despite D1: `infrastructure/windows/windows_capability.py` and
the `OPENSSH_CLIENT` catalog entry. The client role still needs stock OpenSSH
(§12.1), so the capability manager keeps one live caller on Windows.

Delete within surviving files:

- `steps.py`: `ConfigureNgrokAuthtokenStep`, `EnsureRepositoryStep`
- `revert.py`: the `APT_REPO_ADDED` and `ngrok:authtoken` branches
- `catalog.py`: `NGROK`, `GPG`, `OPENSSH_SERVER`, `SSHD`, `NGROK_APT_*`
  (6 constants). `OPENSSH_CLIENT` is retained.
- `journal.py`: `ChangeKind.APT_REPO_ADDED`, `ChangeKind.WINDOWS_CAPABILITY_ADDED`
  (but see §16 — kept as deprecated members, not deleted)
- `toolkit.py` / `toolkits.py`: the `repository_provider` attribute.
  `_WINDOWS_CAPABILITY_PACKAGES` shrinks to `{"openssh-client"}`; the capability
  branch in `manager_for` stays.
- `context.py`: the `credentials` field
- `pyproject.toml`: the `httpx` dependency

---

## 6. Domain changes

### 6.1 `TunnelSpec` (rewritten)

```python
@dataclass(frozen=True, slots=True)
class TunnelSpec:
    """Parameters for one `tailcat serve` invocation."""

    serve: tuple[str, ...] = ("ssh",)       # "ssh", "no-auth-ssh", "8080,8443"
    key_name: str | None = "default"        # None => ephemeral (`--key=new`)
    allow: tuple[str, ...] = ()             # "nodekey:<hex>"; empty => address is the credential
    authorized_keys: tuple[str, ...] = ()   # paths and/or "user@github"
    region: str | None = None               # None => tailcat's `auto`
    bind: str | None = None
    forced_command: str | None = None       # `serve ... -- <cmd>`
```

Argv construction (single source of truth, in the adapter):

```
tailcat serve
  [--key=<key_name> | --key=new]
  [--allow=<k1>,<k2>]
  [--ssh-authorized-keys=<p1>,<p2>]      # only when serve == ("ssh",)
  [--region=<region>] [--bind=<bind>]
  <serve...>
  [-- <forced_command>]
```

### 6.2 `TunnelHandle` (rewritten)

```python
@dataclass(frozen=True, slots=True)
class TunnelHandle:
    address: str
    spec: TunnelSpec
    process: ManagedProcess

    @property
    def connect_command(self) -> str:
        return f"tailcat ssh {self.address}"
```

`public_host` / `public_port` / `ssh_command` are gone. Every presenter and the
`up` command are updated accordingly.

### 6.3 `ServiceSpec` gains a scope

```python
@dataclass(frozen=True, slots=True)
class ServiceSpec:
    key: str
    display_name: str
    scope: Scope = Scope.MACHINE
    windows_service_name: str | None = None
    systemd_unit: str | None = None
```

New catalog entry replacing `SSHD`:

```python
TUNNEL_SERVICE = ServiceSpec(
    key="tunnel",
    display_name="devtunnel tailcat tunnel",
    scope=Scope.USER,
    windows_service_name="DevtunnelTailcat",
    systemd_unit="devtunnel-tailcat",
)
```

### 6.4 `ChangeKind` additions

```python
BINARY_INSTALLED = "binary_installed"   # the tailcat executable
KEY_GENERATED    = "key_generated"      # a tailcat keypair
```

`FILE_CREATED`, `FILE_MODIFIED` and `DIR_CREATED` already exist and are finally
used (authorized_keys, the allowlist, install directories).

---

## 7. Port changes

### 7.1 `TunnelProviderPort` (rewritten)

The authtoken trio becomes a key trio with the identical journaling contract —
`generate_key` returns prior state, `remove_key` consumes it.

```python
class TunnelProviderPort(Protocol):
    # -- keys -----------------------------------------------------------
    def has_key(self, name: str) -> bool: ...
    def generate_key(self, name: str, *, client: bool = False,
                     region: str | None = None, fixed_region: bool = False) -> dict:
        """Run `tailcat genkey`. Returns prior-state details, including the
        address it printed."""
    def remove_key(self, prior_state: dict) -> None: ...
    def node_key(self, name: str) -> str:
        """This machine's `nodekey:<hex>`, for handing to a peer's allowlist."""
    def address_for(self, name: str) -> str | None:
        """The stable address of a saved key, without starting anything."""

    # -- lifecycle ------------------------------------------------------
    def start(self, spec: TunnelSpec, *, timeout: float = 15.0) -> TunnelHandle: ...
    def stop(self, handle: TunnelHandle) -> None: ...
```

`address_for` is what makes the persistent path deterministic: `genkey` prints
the address at creation time, devtunnel caches it in the journal record's
`details`, and `up`/`serve` can print the connection command *before* the
process has said anything.

### 7.2 `ProcessRunnerPort.ManagedProcess` — the one non-cosmetic port change

ngrok's address came over HTTP. tailcat writes it to **stderr**:

```
🐈 Server listening with saved key "default": tcXXXXXXXXX
🐈 Server listening with new address: tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu
```

So `ManagedProcess` needs output access it does not have:

```python
class ManagedProcess(Protocol):
    @property
    def pid(self) -> int: ...
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int: ...
    def read_stderr_line(self, timeout: float | None = None) -> str | None:
        """Next line of stderr, or None on timeout/EOF."""
```

Implementation notes:

- `SubprocessProcessRunner` spawns a daemon reader thread per stream feeding a
  `queue.Queue`; `read_stderr_line` is a `get(timeout=…)`. A naive
  `proc.stderr.readline()` blocks the caller and deadlocks the timeout path.
- `DryRunProcessRunner` returns a canned line with a fixed fake address so
  `--dry-run` renders a realistic connection command.
- Lines are decoded as UTF-8 with `errors="replace"` — the startup banner
  contains a non-ASCII emoji and Windows consoles are not reliably UTF-8.

Address extraction: match `\btc[A-Za-z0-9_-]{16,}\b` on a line containing
`listening`. Only used for ephemeral keys; persistent keys use `address_for`.

### 7.3 `PlatformToolkit`

- `repository_provider` attribute removed.
- `manager_for` loses its capability branch; it now returns Chocolatey/apt for
  every key (only `git` remains).
- Add `service_manager_for(scope: Scope) -> ServiceManagerPort` so a user-scope
  unit and a system-scope unit can coexist. `service_manager` stays as an alias
  for `service_manager_for(Scope.MACHINE)`.

---

## 8. New infrastructure adapters

### 8.1 `infrastructure/release/github_release.py`

Resolves, downloads and verifies a release artifact.

```python
class GitHubReleaseInstaller:
    def __init__(self, process, filesystem, *, repo="tailscale/tailcat",
                 version: str | None = None) -> None: ...

    def resolve_asset(self) -> ReleaseAsset: ...   # tag + asset name + URLs
    def is_installed(self, target_path: str) -> bool: ...
    def installed_version(self, target_path: str) -> str | None: ...
    def install(self, target_path: str) -> dict: ...   # -> details for the journal
    def uninstall(self, details: dict) -> None: ...
```

Asset naming (verified against v0.7.0):

```
tailcat_<ver>_linux_amd64.tar.gz    tailcat_<ver>_windows_amd64.zip
tailcat_<ver>_linux_arm64.tar.gz    tailcat_<ver>_windows_arm64.zip
tailcat_<ver>_linux_armv7.tar.gz    checksums.txt
```

Architecture mapping from `platform.machine()`:

| `machine()` | asset arch |
|---|---|
| `x86_64`, `AMD64` | `amd64` |
| `aarch64`, `arm64`, `ARM64` | `arm64` |
| `armv7l`, `armv6l` | `armv7` |

Anything else is a hard, named failure — never a silent fallback to amd64.

Version resolution: default to a **pinned** constant in `catalog.py`
(`TAILCAT_VERSION = "0.7.0"`), overridable by `--tailcat-version` or
`--tailcat-version=latest` (which queries the releases API). Pinning by default
keeps installs reproducible across a fleet.

Verification is mandatory: fetch `checksums.txt`, locate the line for the
resolved asset, compare SHA-256, and abort before anything is written if it does
not match. A `--no-verify` escape hatch is deliberately **not** provided.

Install directory (`Scope.USER` default):

| Platform | Default | With `--system` |
|---|---|---|
| Linux | `~/.local/share/devtunnel/bin/tailcat` | `/usr/local/bin/tailcat` |
| Windows | `%LOCALAPPDATA%\devtunnel\bin\tailcat.exe` | `%ProgramFiles%\devtunnel\tailcat.exe` |

devtunnel always invokes tailcat by **absolute path**, recorded in the journal
record's `details["path"]`, so no `PATH` mutation is required for devtunnel to
work. `--add-to-path` is an opt-in, separately journaled step for users who want
to run `tailcat` by hand.

Pre-existing tailcat: if `process.which("tailcat")` finds one, the step records
`SKIPPED_PREEXISTING` and devtunnel uses that binary. Consistent with the
project's existing promise never to touch what it did not install.

Download caching: artifacts land in `<state_dir>/cache/` so re-runs and
`--dry-run`-then-real sequences do not re-fetch.

### 8.2 `infrastructure/tailcat/tailcat_keys.py`

Wraps the key subcommands. Keys live at `~/.config/tailcat/keys/<name>.private.json`.

| Operation | Command |
|---|---|
| Generate (server) | `tailcat genkey --key=<name> [--region=<r>] [--fixed-region]` |
| Generate (client) | `tailcat genkey --client --key=<name>` |
| List | `tailcat genkey --list` |
| Delete | `tailcat genkey --delete --key=<name>` |
| Decode an address | `tailcat parse <address>` (JSON) |
| Self-contained form | `tailcat resolve` |

`genkey` prints the address (server) or `nodekey:<hex>` (client) on stdout;
both are captured into the journal record's `details`.

Defaults: server key `default`, client key `client-default` — these are the
names tailcat itself picks up automatically with no extra flags, so devtunnel
stays compatible with hand-run tailcat.

**Carry the one good piece of `ngrok_config.py` forward.** If install ever runs
elevated (`--with-git`, `--system`), `$HOME`/`~` resolves to root's home and the
key lands in the wrong place. Keep `FileSystemPort.real_user_home()` and
`take_ownership_for_real_user()`, and invoke the key commands with `HOME` set
accordingly. This bug class survives the provider swap unchanged.

### 8.3 `infrastructure/tailcat/tailcat_tunnel.py`

Satisfies `TunnelProviderPort` by composing `TailcatKeys` with process
lifecycle. `start()`:

1. If `spec.key_name` is set and a saved address exists, take it from
   `address_for` and skip output scraping entirely.
2. Spawn the argv from §6.1.
3. Poll `process.poll()` for early exit — and surface tailcat's own stderr in
   the error. The current ngrok code discards it, which makes failures opaque.
4. If the address is not already known, read stderr lines until the banner
   matches or `timeout` elapses.
5. On timeout: `terminate()`, raise with the last few stderr lines attached.

`stop()` keeps the existing terminate-then-wait-then-swallow shape.

### 8.4 User-scope service adapters

**`infrastructure/debian/systemd_user.py`** — writes
`~/.config/systemd/user/devtunnel-tailcat.service`, then
`systemctl --user daemon-reload`, `enable --now`, and `loginctl enable-linger <user>`
so the unit survives logout and reboot.

```ini
[Unit]
Description=devtunnel tailcat tunnel
After=network-online.target

[Service]
ExecStart=%h/.local/share/devtunnel/bin/tailcat serve --key=default \
  --ssh-authorized-keys=%h/.ssh/authorized_keys ssh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

**`infrastructure/windows/scheduled_task.py`** — `schtasks /Create /SC ONLOGON
/RL LIMITED /F`, `/Query` for state, `/Delete /F` to revert. No Administrator
required for a task in the current user's own context.

Both satisfy `ServiceManagerPort` as-is: `ServiceState(exists, running,
startup_automatic)` maps cleanly onto both backends, so `EnsureServiceStep` and
its reverter work unchanged.

`--system` keeps the existing `SystemdServiceManager` and `WindowsServiceManager`.

---

## 9. Steps and the install plan

### 9.1 New steps in `application/steps.py`

| Step | `ChangeKind` | `target` | Reverts by |
|---|---|---|---|
| `InstallTailcatBinaryStep` | `BINARY_INSTALLED` | `binary:tailcat` | Deleting the recorded path; no-op if pre-existing |
| `GenerateTailcatKeyStep` | `KEY_GENERATED` | `tailcatkey:<name>` | `genkey --delete --key=<name>`, or restoring a backed-up prior key |
| `EnsureSshKeypairStep` (client) | `FILE_CREATED` | `file:ssh_identity` | Deleting the keypair devtunnel generated; no-op if an existing key was adopted |
| `EnsureAuthorizedKeysStep` | `FILE_MODIFIED` | `file:authorized_keys` | Restoring the backup, or deleting if devtunnel created it |
| `EnsurePeerStep` (server) | `FILE_MODIFIED` | `peer:<name>` | Removing **both** the allowlist entry and the `authorized_keys` line, atomically |
| `AddToPathStep` (opt-in) | `CONFIG_KEY_SET` | `path:tailcat` | Restoring the prior `PATH` value |

All extend `JournaledStep`, so journaling, dry-run and the
`already_satisfied → skip` contract come for free. No change to the Template
Method itself.

`EnsureAuthorizedKeysStep` deserves care: under D1 this file is the entire
authentication boundary. It must back up any existing file, append rather than
overwrite, de-duplicate, and set mode `0600` (dir `0700`) on POSIX.

`EnsurePeerStep` is deliberately **one** step covering both halves of a peer's
identity rather than two independent ones. A half-reverted peer is a real
hazard: allow-listed but not authorized gives a tunnel that cannot log in, and
authorized but not allow-listed leaves a shell credential installed for someone
who can no longer reach the box — the second of which is the kind of residue
this project exists to prevent. One record, one revert, both halves.

### 9.2 `InstallSettings` (rewritten)

```python
@dataclass(frozen=True, slots=True)
class InstallSettings:
    role: Role = Role.SERVER            # SERVER | CLIENT
    with_git: bool = False              # D5
    git_identity: GitIdentity = GitIdentity()
    tailcat_version: str = catalog.TAILCAT_VERSION
    key_name: str = "default"
    region: str | None = None
    fixed_region: bool = False
    authorized_keys: tuple[str, ...] = ()   # paths, literal keys, or user@github
    allow: tuple[str, ...] = ()             # bare node keys
    peers: tuple[str, ...] = ()             # dtp1: bundles -> EnsurePeerStep
    ssh_identity: str | None = None         # client: adopt/generate at this path
    add_to_path: bool = False
    system_scope: bool = False
    skip_packages: frozenset[str] = frozenset()
```

### 9.3 Resulting plan

**`devtunnel install` (server, default — no elevation):**

```
Install tailcat
  - install-tailcat-binary     download + verify + unpack
Configure SSH access
  - ensure-authorized-keys     back up, append, chmod 0600
Configure tailcat identity
  - generate-tailcat-key       genkey --key=default   [prints + caches the address]
Configure peers                                        (only if --allow/--peer given)
  - ensure-peer                allowlist + authorized_keys, per peer
```

**`devtunnel install --client`:**

```
Install OpenSSH client                                 (only if ssh-keygen missing)
  - openssh-client             capability (Windows) / apt (Debian)
Install tailcat
  - install-tailcat-binary
Configure tailcat identity
  - generate-tailcat-key       genkey --client --key=client-default  [prints nodekey]
Configure SSH identity
  - ensure-ssh-keypair         adopt existing, else ssh-keygen -t ed25519
```

The client install ends by printing the pairing bundle (§12.3) — both public
keys in one copy-pasteable token, which is the whole point of the client role.

**`devtunnel install --with-git` (requires elevation):** prepends the Chocolatey
bootstrap (Windows) and the git package group, writing to the machine journal.

The elevation check in `InstallEnvironmentUseCase` moves from unconditional to
**plan-derived**: the use case asks the plan whether it contains any
`Scope.MACHINE` step and only then enforces `is_elevated()`. This is the change
that makes an unprivileged default install possible.

---

## 10. Journal and revert mapping

`StepReverter.for_record` after the rework:

| `ChangeKind` | `target` prefix | Reconstructed step |
|---|---|---|
| `PKGMGR_BOOTSTRAPPED` | — | `BootstrapPackageManagerStep` |
| `PACKAGE_INSTALLED` | `package:` | `EnsurePackageStep` (git only) |
| `BINARY_INSTALLED` | `binary:` | `InstallTailcatBinaryStep` |
| `KEY_GENERATED` | `tailcatkey:` | `GenerateTailcatKeyStep` |
| `SERVICE_STATE_CHANGED` | `service:` | `EnsureServiceStep` |
| `FILE_CREATED` | `file:ssh_identity` | `EnsureSshKeypairStep` |
| `FILE_MODIFIED` | `file:authorized_keys` | `EnsureAuthorizedKeysStep` |
| `FILE_MODIFIED` | `peer:` | `EnsurePeerStep` |
| `CONFIG_KEY_SET` | `gitconfig:` / `path:` | `SetGitConfigStep` / `AddToPathStep` |

Dispatch is by `(kind, target prefix)`, which is why `FILE_MODIFIED` can carry
both the authorized-keys record and the per-peer records without ambiguity.

Uninstall order (journal reversed) is therefore: peers → ssh identity → tailcat
key → authorized_keys → service → binary → openssh-client → git → Chocolatey.
The service must stop before the binary is deleted; reverse-chronological replay
gives that for free, because the service is registered after the binary is
installed. Peers unwinding first is likewise free and likewise correct: access
is revoked before the machinery that enforces it is removed.

---

## 11. CLI surface

```
devtunnel install [--client] [--with-git] [--git-name] [--git-email]
                  [--tailcat-version <v>|latest] [--key-name <n>]
                  [--authorized-keys <path|pubkey|user@github>]...
                  [--allow <nodekey>]... [--peer <bundle>]...
                  [--ssh-identity <path>] [--region <r>] [--fixed-region]
                  [--add-to-path] [--system] [--skip <key>]...
                  [--config <file>] [--non-interactive] [--dry-run] [--json]

devtunnel key show [--key-name <n>]        # this machine's nodekey:<hex>
devtunnel key list
devtunnel address [--key-name <n>]         # the stable tc… address

# -- pairing: both public keys, as one unit ------------------------------
devtunnel pair export [--out <file>] [--name <label>] [--qr]
devtunnel pair add <bundle>|--from <file> [--name <label>] [--yes]
devtunnel pair list [--json]
devtunnel pair remove <name|nodekey>
devtunnel pair fingerprint [<bundle>]      # compare out-of-band before adding

# -- lower-level escape hatches (one half at a time) ---------------------
devtunnel allow <nodekey>... | --remove <nodekey> | --list
devtunnel authorize <pubkey|path|user@github>... | --remove <pubkey>

devtunnel up [--serve ssh|no-auth-ssh|<ports>] [--key-name <n>]
             [--allow <nodekey>]... [--region <r>] [--bind <addr>]
             [--open] [--json]             # foreground; Ctrl+C to close

devtunnel serve --install-service [--system]
devtunnel serve --stop | --status | --remove-service

devtunnel connect <address> [--key-name <n>]   # tailcat ssh <address>

devtunnel status [--json]
devtunnel doctor
devtunnel uninstall [--keep <key>]... [--force] [--yes] [--dry-run] [--json]
```

Notable behaviour:

- `up` prints the connection command **before** the tunnel is confirmed up when
  the key is persistent, then confirms. Output is copy-pasteable:
  `devtunnel connect tcXXXX…` (with the raw `tailcat ssh tcXXXX…` underneath).
- `--open` means "no allowlist; the address is the credential." It prints a
  loud warning, matching tailcat's own framing.
- `--serve no-auth-ssh` requires `--open` to be passed explicitly, so a
  shell-to-anyone-with-the-address configuration can never be reached by accident.
- `install` prompts only to confirm the authorized-keys source when none is
  supplied and none can be inferred from `~/.ssh/*.pub`.

### `FileConfig` (rewritten)

```jsonc
{
  "role": "server",
  "with_git": false,
  "git_name": null,
  "git_email": null,
  "tailcat_version": "0.7.0",
  "key_name": "default",
  "region": null,
  "authorized_keys": ["~/.ssh/id_ed25519.pub"],
  "allow": ["nodekey:cfb6…"],
  "peers": ["dtp1:eyJ2IjoxLCJuYW1l…"],
  "ssh_identity": null,
  "add_to_path": false,
  "skip_packages": []
}
```

`ngrok_authtoken` is removed. Precedence collapses to **flag > config file >
prompt**; the env-var tier existed only for `NGROK_AUTHTOKEN` and goes with it.

---

## 12. Client identity and pairing

A client needs **two** public keys installed on the host before it can get a
shell, and devtunnel is responsible for creating, surfacing, transporting and
revoking both:

| Key | Created by | Gates | Lands on the host in |
|---|---|---|---|
| tailcat node key (`nodekey:<hex>`) | `tailcat genkey --client` | the **tunnel** — may this peer connect at all | the server's `--allow` list |
| SSH public key (`ssh-ed25519 AAAA…`) | `ssh-keygen` | the **shell** — which user may log in | the server's `authorized_keys` |

Either one alone is a broken state, and each fails differently: allow-listed but
not authorized is a tunnel that refuses to log in; authorized but not
allow-listed is a peer that cannot reach the box at all. They are created,
transported, installed and revoked **together** — see `EnsurePeerStep` in §9.1.

### 12.1 Why the client still needs OpenSSH

`tailcat ssh` is documented as wrapping the **stock ssh client** with a
ProxyCommand that pipes through tailcat — the same mechanism behind
`tailcat cp`, and the reason plain `scp`/`sftp` work against `serve files`.
Consequences for the client role:

- The SSH identity presented is whatever **stock ssh** selects: `~/.ssh/id_*`
  and `ssh-agent`, with ordinary OpenSSH semantics. tailcat does not carry its
  own SSH identity.
- `ssh` (to connect) and `ssh-keygen` (to create an identity) must be present.

So D1 removes OpenSSH from the **server** only. The client plan keeps a
conditional `openssh-client` step, planned when `which("ssh-keygen")` is empty.
On Windows this is the one remaining caller of `WindowsCapabilityManager`.

### 12.2 Creating the client's SSH keypair

`EnsureSshKeypairStep` (client role only):

1. **Adopt, don't clobber.** If a usable default identity already exists —
   `~/.ssh/id_ed25519`, else `id_ecdsa`, else `id_rsa` — use it and journal
   nothing. devtunnel never overwrites, re-comments or re-permissions a key the
   user already had, and `uninstall` must never delete one.
2. Otherwise generate:
   ```sh
   ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N "" -C "devtunnel <user>@<host>"
   ```
   Journaled as `FILE_CREATED` with `details["generated"] = true`; revert
   deletes both halves, and only when devtunnel created them.
3. `--ssh-identity <path>` selects or generates a dedicated key instead of the
   default one. This requires passing `-i` through to the stock ssh client on
   connect, and the exact passthrough mechanism is an M0 verification task
   (§20.5). Until that is confirmed, the default-identity path is the supported
   one.
4. Passphrase: generated keys use `-N ""` so unattended `serve`/`connect` work.
   `--ssh-key-passphrase` is offered for interactive use, with the obvious
   caveat that an agent is then required for non-interactive connects.

### 12.3 The pairing bundle

Both public values travel as one token, so the client hands over **one** thing
and the host consumes it with **one** command:

```
dtp1:<base64url(compact json)>
```

```json
{
  "v": 1,
  "name": "tommy@laptop",
  "nodekey": "nodekey:cfb6bfa77a0654d7450947fd6acef17d2cd848da1d30b2540b13dac272ddfd16",
  "sshkey": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI… devtunnel tommy@laptop",
  "created": "2026-10-02T14:21:07Z"
}
```

The `dtp1:` prefix makes the token self-identifying and lets `pair add` reject
malformed or future-version input by name rather than by stack trace. Parsing
is strict: unknown version → refuse; missing `nodekey` or `sshkey` → refuse;
`sshkey` must parse as a valid OpenSSH public key of an accepted type; `name`
is sanitised to `[A-Za-z0-9._@-]` because it is written into a comment field.

**A bundle contains only public material — it is not a secret.** But it *is* an
authorization request, so the host operator must confirm it came from who they
think. `devtunnel pair fingerprint` prints a short SHA-256 digest over both keys
for out-of-band comparison, and `pair add` shows the same fingerprint and
prompts unless `--yes` is passed:

```console
$ devtunnel pair fingerprint          # on the client
dtp fingerprint: 4f2a-91c3-de07-b85e
```

### 12.4 End to end

**Client:**

```console
$ devtunnel install --client
Installed tailcat 0.7.0
Generated SSH identity ~/.ssh/id_ed25519 (ed25519)
Generated tailcat client key "client-default"

Your pairing bundle — send this to whoever runs the host:

  dtp1:eyJ2IjoxLCJuYW1lIjoidG9tbXlAbGFwdG9wIiwibm9kZWtleSI6Im5vZGVrZXk6Y2Zi…

  tunnel key   nodekey:cfb6bfa7…
  shell key    ssh-ed25519 AAAAC3Nz… devtunnel tommy@laptop
  fingerprint  4f2a-91c3-de07-b85e

They run:  devtunnel pair add dtp1:…
```

**Host:**

```console
$ devtunnel pair add dtp1:eyJ2IjoxLCJuYW1l…
Peer "tommy@laptop"
  tunnel key   nodekey:cfb6bfa7…
  shell key    ssh-ed25519 AAAAC3Nz…
  fingerprint  4f2a-91c3-de07-b85e

Confirm this fingerprint with them out of band. Add this peer? [y/N] y
  + allowlist          nodekey:cfb6bfa7…
  + authorized_keys    ssh-ed25519 AAAAC3Nz…

Send them back:
  devtunnel connect tcomFwWCCcjS5nKNqAod034nWoJZW0LZqDhhC8U_dKdnDRYQ8uNGFpGQEu
```

**Client:**

```console
$ devtunnel connect tcomFwWCCcjS5nKN…
```

A running tunnel picks up allowlist changes only on restart, so `pair add`
detects a live `devtunnel serve` service and offers to restart it; for a
foreground `up`, it says plainly that the tunnel must be restarted.

### 12.5 Getting the bundle to the host

| Mechanism | Command | When |
|---|---|---|
| Copy-paste out of band | `pair export` → chat/email → `pair add` | Default. No trust assumptions, works everywhere |
| File | `pair export --out peer.json` → `pair add --from peer.json` | Scripted or bulk enrolment |
| GitHub, SSH half only | host runs `--authorized-keys tommy@github` | tailcat fetches `github.com/<user>.keys` natively; still needs the node key separately |
| QR code | `pair export --qr` | Pairing a machine you cannot paste into |

A fifth option — a time-boxed `devtunnel pair --listen` enrolment tunnel that
accepts exactly one bundle — is deferred (§18). It has a genuine chicken-and-egg
problem: accepting a bundle from an unknown peer means opening the tunnel to
unknown peers first, which is precisely what the allowlist exists to prevent.
If it is built, it must be one-shot, time-limited, fingerprint-confirmed, and
never the default.

### 12.6 Verifying the host, and revoking

The reverse direction matters too. The host's address is not secret, but a
client that connects to a *substituted* address leaks nothing except its
willingness to connect — still worth closing:

```console
$ devtunnel connect tcomFw… --expect nodekey:9c8d2e67…
```

runs `tailcat parse <address>`, compares `ServerPublic` against `--expect`, and
refuses on mismatch. `devtunnel pair add` prints the host's own node key
alongside the connect command so the client has something to pin.

Revocation is one command and removes both halves:

```console
$ devtunnel pair remove tommy@laptop
  - allowlist          nodekey:cfb6bfa7…
  - authorized_keys    ssh-ed25519 AAAAC3Nz…
```

`devtunnel uninstall` reverts every `peer:` record the same way. The failure
this guards against is a partial revoke leaving a working shell credential on
the box for someone who was supposed to lose access.

---

## 13. Security considerations

1. **`authorized_keys` is the whole boundary.** Under D1 there is no sshd config,
   no PAM, no `AllowUsers`. The step that writes this file must never silently
   widen access: always back up, always append, always de-duplicate, `0600`.
2. **`no-auth-ssh` is a loaded gun.** Gated behind an explicit `--open`, with a
   warning quoting tailcat's own wording: the address *is* the credential.
3. **Checksum verification is non-negotiable.** devtunnel downloads an executable
   and runs it. SHA-256 against `checksums.txt`, no `--no-verify` flag, abort
   before writing on mismatch.
4. **Pinned version by default.** `latest` is opt-in, so an upstream release
   cannot silently change what gets installed on a fleet.
5. **Key file permissions.** `~/.config/tailcat/keys/*.private.json` is a private
   key. Verify `0600` after `genkey`, and `0700` on the directory, especially
   after any elevated run.
6. **Journal contents.** Node keys, SSH *public* keys, addresses and bundles are
   public material and safe to journal. Private keys — `~/.ssh/id_*` and
   `~/.config/tailcat/keys/*.private.json` — are never read, never copied into
   the journal, and never included in a backup. `EnsureSshKeypairStep` journals
   the public half and the path only.
7. **The listener.** Service mode leaves a process accepting connections.
   `status` must state plainly whether a tunnel service is registered and running.
8. **A bundle is an authorization request, not a secret.** It carries only
   public keys, so it can safely cross a chat window — but anyone who can inject
   one into the host operator's clipboard gets both tunnel and shell access.
   Hence the mandatory fingerprint display and the confirmation prompt on
   `pair add`; `--yes` exists for automation and should be documented as the
   point where the operator takes on that verification themselves.
9. **Strict bundle parsing.** `pair add` consumes attacker-influenced input. It
   must validate the version, reject unknown fields, verify the SSH key parses
   as a supported type, and sanitise `name` before it is written into a comment
   field in `authorized_keys` — a newline smuggled through `name` would let a
   bundle inject a second, unreviewed key line.
10. **Partial revoke is the failure to engineer against.** `pair remove` and the
    uninstall path must remove the allowlist entry and the `authorized_keys`
    line as one unit, and must report loudly if only one half could be removed.
11. **Adopted keys are never destroyed.** If `EnsureSshKeypairStep` adopted a
    pre-existing identity, `uninstall` leaves it entirely alone — the same
    `SKIPPED_PREEXISTING` guarantee the project already makes for packages.

---

## 14. Testing plan

Unit tests continue to run against fakes with no subprocess, network or
filesystem access outside `tmp_path`.

**Update:**

- `tests/fakes/fake_ports.py` — `FakeTunnelProvider` grows the key methods;
  `FakeManagedProcess` grows a scripted `read_stderr_line`; `FakeToolkit` drops
  `repository_provider` and gains `service_manager_for`.
- `test_plan_builder.py` — rewritten around the four plan shapes (server,
  client, `--with-git`, `--system`).
- `test_steps.py`, `test_revert.py`, `test_install_uninstall_roundtrip.py` —
  retargeted onto the new steps and kinds.

**Add:**

- `test_github_release.py` — arch mapping across all six asset combinations;
  checksum mismatch aborts without writing; pre-existing binary records
  `SKIPPED_PREEXISTING`; unknown architecture fails by name.
- `test_tailcat_tunnel.py` — exact argv for every `TunnelSpec` permutation;
  persistent key skips scraping; ephemeral key parses the banner from stderr;
  early exit surfaces tailcat's stderr; timeout terminates and raises.
- `test_tailcat_keys.py` — genkey argv, address and node-key capture, `HOME`
  redirection under simulated elevation.
- `test_authorized_keys_step.py` — append not overwrite, de-duplicate, mode bits,
  revert restores byte-for-byte.
- `test_ssh_keypair_step.py` — adopts an existing `id_ed25519` and journals
  nothing; generates with the exact `ssh-keygen` argv when none exists; revert
  deletes a generated pair and **never** an adopted one; plans the
  `openssh-client` step only when `ssh-keygen` is absent.
- `test_pairing.py` — the bundle round-trips export→add; `dtp1:` prefix enforced;
  unknown version, missing field, malformed SSH key and a newline smuggled
  through `name` are each rejected by name; fingerprints match across both ends.
- `test_peer_step.py` — one record covers both halves; revert removes both; a
  failure removing the second half is reported, not swallowed; adding the same
  peer twice is idempotent.
- `test_journal_scope.py` — user/machine split, merged `status`, elevation
  required only for machine-scope reverts.

**Property to preserve:** the existing install→uninstall round-trip test is the
single most valuable test in the suite. It must pass for all four plan shapes.

**Manual verification matrix** (cannot be unit-tested): Windows 11 unprivileged,
Windows 11 elevated with `--with-git`, Ubuntu 24.04 unprivileged, Ubuntu with
`--system`, and one real two-machine SSH session across a NAT boundary.

---

## 15. Documentation changes

- `README.md` — rewritten: no authtoken, no elevation in the default path, the
  two-sided pairing walkthrough from §12 as the primary example.
- `docs/architecture.md` — the "Platform-specific fixes" section loses its ngrok
  and Chocolatey-capability entries and gains the stderr-address, checksum and
  user-scope-service notes. The pattern table keeps every row except Chain of
  Responsibility, which survives with one example instead of two (the credential
  chain is gone; the platform-detector chain remains).
- `docs/install-plan.md` — review for ngrok references.
- `scripts/install-on-azure-vm.ps1` — drops the authtoken parameter.
- `pyproject.toml` — description and the `httpx` dependency.

The architecture doc's framing needs an honest edit. "Fully reverses itself" was
compelling when devtunnel installed packages, an apt repository and a system
service. With a user-scope binary it is less impressive on its own — the claim
should be re-centred on the **service mode and the authorized_keys/allowlist
mutations**, which are the changes a user genuinely would not want left behind.

---

## 16. Migration for existing installs

A journal written by the current version may contain `apt_repo_added` and
`ngrok:authtoken` records whose reverters this plan deletes. Loading one would
raise `NoReverterRegisteredError` mid-uninstall.

Approach: **ship one legacy-revert shim; do not break old journals.**

- Keep `ChangeKind.APT_REPO_ADDED` and `WINDOWS_CAPABILITY_ADDED` as deprecated
  members so old journals still deserialize.
- Keep a `legacy_revert.py` holding the ngrok apt-repository and authtoken
  reverters, wired into `StepReverter` behind a comment pointing here.
- `devtunnel doctor` detects a legacy journal and prints: *"This machine has an
  ngrok-era devtunnel install. Run `devtunnel uninstall` with version 0.0.x
  before upgrading, or run it now — legacy records are still supported."*
- Delete the shim in the release after next.

This is roughly 60 lines of retained code, and it is cheaper than any alternative
that strands a user with an un-revertable apt repository on their machine.

---

## 17. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| `loginctl enable-linger` needs polkit or root on headless hosts | Service mode silently dies at logout | Detect failure, warn explicitly, offer `--system`; never report success on a unit that will not survive |
| Reading tailcat's stderr deadlocks or misses the banner | `up` hangs to timeout | Reader thread + queue; prefer `address_for` so the persistent path never scrapes at all |
| Upstream changes the banner wording or asset naming | `up` or install breaks | Pinned version by default; address parsing is a fallback, not the primary path; assert asset names in tests |
| tailcat is pre-1.0 (v0.7.0) and the CLI may still move | Churn | Pin, and keep all argv construction in one adapter method |
| Windows Defender / SmartScreen flags a downloaded exe | Install fails confusingly | Catch and explain; document the Scoop route (§18) as the workaround |
| Corporate egress blocks `github.com` release downloads | Install fails | `--tailcat-binary <path>` to install from a pre-staged file; honour `HTTPS_PROXY` |
| The project's reversibility story thins out | Loss of the tool's distinguishing value | D3 — put the weight on service mode; re-centre the docs (§15) |

---

## 18. Deferred, with the seam left open

Each of these was considered and consciously postponed. None requires rework of
what this plan builds.

- **`--ssh-mode=sshd`** — fronting system sshd with `tailcat serve 22`. Requires
  restoring `OPENSSH_*` to the catalog and `WindowsCapabilityManager`; both stay
  in git history with a pointer in `catalog.py`.
- **`--via-package-manager`** — Scoop (Windows) and `.deb` (Debian) installs for
  people who want the OS to own tailcat updates.
- **ngrok as a second provider** — rejected in D4; revisit only if a concrete
  need appears.
- **`devtunnel share` / `devtunnel recv`** — tailcat's `cp`, `recv` and
  `serve files`. `TunnelSpec.serve` is already a tuple to accommodate this.
- **DNS TXT publishing** — `devtunnel address --dns-txt` emitting a ready-to-paste
  record so peers can `tailcat ssh host.example.com`.
- **`devtunnel pair --listen`** — a one-shot, time-boxed enrolment tunnel that
  accepts a single bundle without copy-paste (§12.5). Deferred on its merits,
  not its difficulty: accepting a bundle from an unknown peer means opening the
  tunnel to unknown peers first.
- **Agent and multi-identity support** — `ssh-agent` discovery, and more than one
  SSH identity per client. The bundle format already carries one `sshkey`; a
  future `v2` could carry a list.
- **Port forwarding** — `devtunnel up --serve 8080,8443` already works through
  `TunnelSpec`; it needs CLI surface and docs only.

---

## 19. Work breakdown

| Milestone | Content | Exit criterion |
|---|---|---|
| **M0** | Branch; pin `TAILCAT_VERSION`; add the arch-mapping table and its tests; **confirm how `tailcat ssh` selects an SSH identity** and whether `-i`/a custom identity can be passed through (§20.5) | New tests pass; the identity question is answered in writing before §12.2 is built |
| **M1** | Domain + ports: `TunnelSpec`, `TunnelHandle`, `ManagedProcess.read_stderr_line`, `ServiceSpec.scope`, new `ChangeKind`s, fakes moved in step | Full suite green; no behaviour change yet |
| **M2** | `github_release.py` + `InstallTailcatBinaryStep` | Binary installs and reverts on both platforms |
| **M3** | `tailcat_keys.py` + `tailcat_tunnel.py` + `GenerateTailcatKeyStep` | `up` opens a real tunnel from a dev checkout |
| **M4** | `PlanBuilder` rewrite; delete ngrok/credentials/apt-repo; fix `StepReverter`; `EnsureAuthorizedKeysStep`, `EnsureSshKeypairStep`, `EnsurePeerStep`; bundle encode/decode + fingerprint; scope-aware elevation and journal | `install --client` → `pair add` → `connect` works between two machines; `uninstall` round-trips unprivileged and revokes both halves |
| **M5** | Service mode: systemd user unit, Scheduled Task, `--system` paths | Tunnel survives logout and reboot on both platforms |
| **M6** | CLI surface (`key`, `pair`, `allow`, `authorize`, `serve`, `connect`), `FileConfig`, presenters, README, architecture doc, legacy shim | Two-machine manual matrix passes; docs match reality |

M1–M4 deliver a working `devtunnel up`. M5 and M6 are separable into a second
pass if this needs to land incrementally.

---

## 20. Open questions

1. **Does devtunnel still install git at all?** D5 makes it opt-in. If the
   original use case — provisioning a dev box that has nothing — is the dominant
   one, the right answer may be to keep git in the default plan and accept
   elevation. This decision alone determines whether the Chocolatey/apt layer
   stays live or becomes vestigial.
2. **Should the tool keep the name `devtunnel`?** It no longer provisions a
   "dev tunnel" in the ngrok sense. Renaming is cheap now and expensive once
   people have it installed.
3. **One key, or one key per purpose?** The plan uses tailcat's own `default` /
   `client-default` names. A machine that is both server and client gets two
   keys and two identities; worth confirming that is the desired model.
4. **Windows arm64** is a published asset but is untested here. Include it in the
   manual matrix, or declare it unsupported?
5. **How does `tailcat ssh` select an SSH identity?** It wraps stock ssh via a
   ProxyCommand, so ordinary `~/.ssh/id_*` and agent resolution should apply —
   but the README documents no `-i`/`--identity` passthrough. If a custom
   identity cannot be passed through, `--ssh-identity` (§12.2) must either drop
   or be implemented by invoking `ssh` directly with devtunnel's own
   ProxyCommand. **Resolve in M0 before building §12.2.**
6. **Should `pair add` restart a running tunnel?** An allowlist change only takes
   effect on restart. Auto-restarting a service someone is currently connected
   through will drop their session. The plan currently offers; always-restart
   and never-restart are both defensible.
