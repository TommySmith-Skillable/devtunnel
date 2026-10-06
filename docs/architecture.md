# devtunnel architecture

## Why this exists

A prior approach ([`lab-dev-tools/windows-dev`](https://gitlab.com/lab-dev-tools/windows-dev))
provisioned the same environment with two parallel scripts (`setup.ps1`,
`setup.sh`). They work, but:

1. **No uninstall.** Nothing records what changed, so nothing can be reversed.
2. **No idempotency.** `setup.ps1` overwrites `~/.gitconfig` wholesale,
   destroying any existing git identity on every re-run.
3. **Duplicated logic across two languages** that silently drift (the Linux
   script installs `gpg`; the Windows one doesn't. Neither opens a tunnel).
4. **It never opens the tunnel.** Both stop after `ngrok config add-authtoken`.

devtunnel replaces both with one Python CLI built so that **every mutation is
journaled with its prior state before it happens**, and `uninstall` replays
that journal backwards — restoring exactly what was there before, not just
deleting things.

### What reversibility is actually worth now

The original claim — "it fully reverses itself" — was doing heavy lifting when
devtunnel installed packages, an apt repository and a system service. After the
tailcat migration the default install is a user-scope binary, a keypair and two
text files, and "we can delete the file we downloaded" is not an interesting
promise. It would be easy to keep repeating the old line; it would also be
overselling.

The claim is worth re-centring on the two mutations a user genuinely would not
want left behind:

1. **Service registration.** `devtunnel serve --install-service` leaves a
   systemd user unit or a Scheduled Task that starts a listener at logon, plus
   — on Linux — possibly `loginctl enable-linger` turned on for the account.
   That is persistent, invisible from a shell prompt, and the kind of thing
   people forget they did. The journal records the prior `ServiceState` and
   whether devtunnel was the one who enabled lingering, so revert puts both
   back rather than guessing.
2. **`authorized_keys` and the allowlist.** These are access grants, not files.
   A half-removed peer that still has a line in `authorized_keys` is a working
   shell credential sitting on the box for someone who was supposed to lose
   access, and nothing on the machine will ever remind anyone it is there.
   `EnsurePeerStep` is deliberately **one** record covering both halves so that
   revoking is atomic, and a failure to remove one half is reported rather than
   swallowed.

Everything else — the binary, the cache directory, a generated keypair — is
reverted too, because the machinery is uniform and it costs nothing to apply it
consistently. But it is not the argument. The argument is that an access grant
and a background listener should both be removable by a command that knows
exactly what it did.

## Layering (Clean Architecture)

Dependencies point inward only. `domain` imports nothing from this project;
`infrastructure` is imported solely by the composition root (`container.py`).

```
domain/            Pure value objects, the journal, the Step/Plan composite. No I/O.
  models.py          PlatformId, Scope, PackageSpec, ServiceSpec, TunnelSpec, Role
  journal.py         ChangeRecord/ChangeKind — the Memento
  plan.py            Step/StepGroup/Plan — the Composite
application/       Ports (interfaces), the Step library, PlanBuilder, use cases.
  pairing.py         the dtp1: bundle: encode, decode, fingerprint. Pure, no ports.
  allowlist.py       the on-disk node-key allowlist
  paths.py           every location devtunnel owns, resolved once per (platform, scope)
  journals.py        the USER/MACHINE journal split and MergedJournalRepository
  legacy_revert.py   reverters for ngrok-era journal records (see "Migration" below)
infrastructure/    Concrete adapters, imported only by the composition root.
  release/           GitHubReleaseInstaller: resolve, download, verify, unpack
  tailcat/           tailcat_keys.py (genkey/parse) + tailcat_tunnel.py (serve lifecycle)
  debian/            apt.py, systemd.py (system scope), systemd_user.py (user scope)
  windows/           chocolatey.py, sc_service.py (system), scheduled_task.py (user),
                     windows_capability.py, elevation.py
  journal/           JSON journal repository
config/            Optional JSON config file for unattended installs.
cli/               Typer commands + Rich/JSON presenters. No business logic.
container.py       Composition root: detects the platform, wires every adapter.
```

`TunnelHandle` is the one type that looks like it belongs in `domain/` and does
not. It lives in `application/ports/tunnel_provider.py` because it holds a live
`ManagedProcess`: the domain layer has no outward dependencies, and a handle to
a running child process is not a value object.

Gone with ngrok: `infrastructure/ngrok/`, `infrastructure/credentials/`,
`infrastructure/debian/apt_repository.py`, `ports/credentials.py` and
`ports/repository_provider.py`. The ports went with their adapters because
neither abstraction had a second implementation that was not a test fake.

## The journal: how uninstall stays safe

A `ChangeRecord` (`domain/journal.py`) is a **Memento** — it captures the
world's state *before* a change, written as `PENDING` before the mutation
happens (crash-safe), then flipped to `APPLIED` or `SKIPPED_PREEXISTING`.

```
PENDING -> APPLIED -> REVERTED | REVERT_FAILED
PENDING -> SKIPPED_PREEXISTING          # never reverted
```

`uninstall` walks the journal backwards and reverts every record that
`needs_revert`. Anything `SKIPPED_PREEXISTING` — something devtunnel found
already on the machine — is never touched. This is what makes it safe to
install onto a machine that already has git, and safe to run `uninstall`
twice.

### Two journals, one view

There is no longer a single journal, because there is no longer a single
privilege level. `Scope` decides where a record lands:

| Scope | Linux | Windows | Holds |
|---|---|---|---|
| `USER` | `~/.local/share/devtunnel/journal.json` | `%LOCALAPPDATA%\devtunnel\journal.json` | binary, keys, `authorized_keys`, allowlist, user service |
| `MACHINE` | `/var/lib/devtunnel/journal.json` | `%ProgramData%\devtunnel\journal.json` | git, system service |

The split is what makes an unprivileged install possible at all: a plan that
touches no system state must not write its record to a system-owned path
either, or the write itself would re-impose the elevation the rest of the
design just removed.

A `JournaledStep` never sees the split — it declares its scope and is handed
the matching repository. `status`, `doctor` and `uninstall` read a
`MergedJournalRepository`, which presents both files as one chronological
sequence and routes updates back to whichever journal actually holds the
record. Ordering is by `(performed_at, position within its own journal)`:
record ids carry a random suffix, so sorting by id would make the unwind order
of same-instant records differ between runs, and position is the one ordering
fact a single journal genuinely has.

## Patterns, and the problem each one solves

| Pattern | Where | Why |
|---|---|---|
| **Ports & Adapters** | `application/ports/*` | Core logic testable with fakes — see `tests/fakes/` |
| **Abstract Factory** | `application/ports/toolkit.py` + `infrastructure/toolkits.py` | Bundles one platform's consistent family (choco+sc vs apt+systemd). A new platform is one new class |
| **Command + Memento** | `Step.apply()`/`revert()` (`domain/plan.py`) + `ChangeRecord.prior_state` | The undo stack — this pair *is* the uninstall feature |
| **Composite** | `Plan`/`StepGroup` (`domain/plan.py`) | `walk()` flattens nested groups uniformly for both real execution and `--dry-run` |
| **Template Method** | `JournaledStep` (`application/steps.py`) | Fixes `detect -> skip-or-apply -> journal` so no step can forget to journal |
| **Builder** | `PlanBuilder` | Assembles the ordered plan from settings + toolkit; the plan is data, so `--dry-run` is free |
| **Factory Method** | `StepReverter` (`application/revert.py`) | Reconstructs the one Step that can undo a journal record, from the record alone |
| **Chain of Responsibility** | `PlatformDetector` chain (`infrastructure/platform_detect.py`) | Ordered fallback with a clear "nothing matched" failure. The credential chain was the second example; it went with the authtoken it existed to resolve |
| **Observer** | `EventBus` (`domain/events.py`) → `RichPresenter`/`JsonPresenter` | Progress reporting without the core knowing how (or whether) it's displayed |
| **Repository** | `JournalRepositoryPort` | Journal storage is swappable; tests use an in-memory fake |
| **Null Object** | `DryRunProcessRunner`, `AptAlreadyPresentBootstrap` | `--dry-run` and "apt needs no bootstrap" need no conditionals scattered through call sites |
| **Composite view** | `MergedJournalRepository` (`application/journals.py`) | Two scoped journals read as one chronological sequence, so uninstall's reverse replay is unchanged by the split |
| **Specification over the plan** | `plan_requires_elevation()` (`application/plan_builder.py`) | The elevation check is derived from the built plan rather than asserted up front, which is what lets the default install run unprivileged |

## Platform-specific fixes

- **`sudo` makes `~` resolve to root's home.** `--with-git` and `--system` can
  both put the process under `sudo`, at which point `os.path.expanduser` sends
  keys, config and systemd user units into `/root` where the invoking user's
  tooling will never look — and the install reports success having configured
  nothing. Every path goes through `FileSystemPort.real_user_home()` instead
  (`application/paths.py`), and anything written while elevated has its
  ownership corrected afterwards. This bug class predates tailcat and survived
  the provider swap unchanged.
- **The tunnel address arrives on stderr.** tailcat prints
  `Server listening with saved key "default": tc…` to stderr at startup;
  there is no status endpoint to poll. A naive `proc.stderr.readline()` blocks
  the caller indefinitely, so the timeout path deadlocks and `up` hangs instead
  of failing. `SubprocessProcessRunner` spawns a daemon reader thread per
  stream feeding a `queue.Queue`, and `read_stderr_line` is a
  `get(timeout=…)` — the one form of "wait, but not forever" that actually
  works on both platforms. Lines are decoded with `errors="replace"`, because
  the banner carries a non-ASCII glyph and Windows consoles are not reliably
  UTF-8. Scraping is the fallback, not the primary path: a saved key's address
  is already known from `address_for`, so the persistent path never reads the
  banner at all.
- **A downloaded executable is verified before anything is written.** devtunnel
  fetches a release archive and then runs what is inside it, so
  `GitHubReleaseInstaller` fetches `checksums.txt`, locates the line for the
  resolved asset and compares SHA-256 *before* the target path is touched; a
  mismatch aborts with nothing on disk. There is deliberately no `--no-verify`
  escape hatch — an escape hatch here is indistinguishable from not having the
  check. The version is pinned in `catalog.py` by default so an upstream
  release cannot silently change what lands on a fleet.
- **User-scope services, so the default path needs no elevation.** On Linux,
  `SystemdUserServiceManager` writes `~/.config/systemd/user/…` and runs
  `systemctl --user`. A user instance is torn down at logout unless the account
  is lingering, and `loginctl enable-linger` needs polkit or root — it fails
  routinely on headless hosts, while `systemctl --user is-enabled` goes on
  reporting `enabled`. Reporting a healthy service that will silently stop
  existing at logout is the worst outcome available, so the call runs with
  `check=False` and its failure is surfaced to the operator with `--system`
  offered as the alternative. A marker file records that *devtunnel* enabled
  lingering, so revert never disables it for a user who already had it. On
  Windows the equivalent is a Scheduled Task registered `/SC ONLOGON /RL
  LIMITED` — the least-privilege run level, so no elevation prompt and no
  Administrator.
- **Never `apt-get autoremove`.** Uninstall purges exactly the recorded
  package, never more.
- **Chocolatey has no uninstaller** (`--with-git` only). It is now reachable by
  one path rather than by every install, but that path still exists, and
  reversing a package *manager* is not reversing a package: there is no
  official uninstall command, so `ChocolateyBootstrap.teardown` deletes the
  install directory, clears the machine `ChocolateyInstall` variable and
  filters `*chocolatey*` entries out of the machine `PATH`.

  That last step is the weakest reversal in the codebase and worth naming as
  such. Everywhere else devtunnel captures prior state and restores it;
  `bootstrap()` records only the install directory, so teardown matches `PATH`
  entries by pattern instead — which would also strip a Chocolatey `PATH`
  entry the machine already had, and is the one place the project's
  "restore exactly what was there" rule is not actually enforced. It is
  tolerable only because the step is now opt-in and rare. If anything else is
  ever moved behind the package manager, capture the prior `PATH` at bootstrap
  first.

## Extending to a new Linux distribution

1. Add the new `PlatformId` member (`domain/models.py`).
2. Add a link to the `platform_detect.py` chain.
3. Write one new `PlatformToolkit` implementation in `infrastructure/` (its
   package manager, and a `service_manager_for(scope)` returning a user-scope
   and a machine-scope service manager).
4. Teach `application/paths.py` where that platform puts a user's state
   directory, if it differs from the POSIX layout.
5. Wire it into `container.py`.

Nothing in `domain/` changes, and nothing in `application/` beyond step 4.
The release installer, the tailcat adapter and every step are platform-neutral
already — the arch mapping in `infrastructure/release/github_release.py` is
keyed off `platform.machine()`, and an unrecognised architecture is a named
failure rather than a silent fallback to amd64.

## Migration from the ngrok era

A journal written by the pre-tailcat version can contain `apt_repo_added` and
`ngrok:authtoken` records whose reverters no longer exist, and loading one
mid-uninstall would fail with `NoReverterRegisteredError` — stranding a user
with an apt repository they cannot remove. `ChangeKind` keeps those members as
deprecated so old journals still deserialize, `application/legacy_revert.py`
keeps the reverters, and `devtunnel doctor` detects such a journal and says so.
The shim is scheduled for deletion in the release after next; it is retained
because roughly sixty lines of dead-ended code is cheaper than any alternative
that leaves residue on a machine.
