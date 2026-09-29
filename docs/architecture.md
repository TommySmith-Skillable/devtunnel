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

## Layering (Clean Architecture)

Dependencies point inward only. `domain` imports nothing from this project;
`infrastructure` is imported solely by the composition root (`container.py`).

```
domain/            Pure value objects, the journal, the Step/Plan composite. No I/O.
application/       Ports (interfaces), the Step library, PlanBuilder, use cases.
infrastructure/    Concrete adapters: Chocolatey, apt, systemd, PowerShell, ngrok, JSON journal.
config/            Optional JSON config file for unattended installs.
cli/               Typer commands + Rich/JSON presenters. No business logic.
container.py       Composition root: detects the platform, wires every adapter.
```

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

Stored system-wide (`%ProgramData%\devtunnel\journal.json` on Windows,
`/var/lib/devtunnel/journal.json` on Debian), since installs require
elevation.

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
| **Chain of Responsibility** | `CredentialChain` (env→flag→prompt); `PlatformDetector` chain | Ordered fallback with a clear "nothing matched" failure |
| **Observer** | `EventBus` (`domain/events.py`) → `RichPresenter`/`JsonPresenter` | Progress reporting without the core knowing how (or whether) it's displayed |
| **Repository** | `JournalRepositoryPort` | Journal storage is swappable; tests use an in-memory fake |
| **Null Object** | `DryRunProcessRunner`, `AptAlreadyPresentBootstrap` | `--dry-run` and "apt needs no bootstrap" need no conditionals scattered through call sites |

## Platform-specific fixes

- **`/etc/apt/keyrings` may not exist** on older Debian — created on demand,
  tracked via `details["created_dir"]` so uninstall only removes it if
  devtunnel made it (`infrastructure/debian/apt_repository.py`).
- **`sudo` + ngrok config.** Running elevated makes `$HOME`/`~` resolve to
  root's home; the ngrok CLI is invoked with `HOME` pointed at the real
  user's home (`infrastructure/ngrok/ngrok_config.py`), and the resulting
  file's ownership is corrected afterward.
- **Chocolatey has no official uninstaller.** Reversal is implemented
  deliberately: delete the install directory, clear the `ChocolateyInstall`
  env var, strip the `PATH` entry (`infrastructure/windows/chocolatey.py`).
- **Never `apt-get autoremove`.** Uninstall purges exactly the recorded
  package, never more.
- **Binary GPG output.** `gpg --dearmor` writes binary bytes; routed through
  a temp file with `-o <path>` rather than captured as `subprocess` text
  stdout, which would corrupt it.

## Extending to a new Linux distribution

1. Add the new `PlatformId` member (`domain/models.py`).
2. Add a link to the `platform_detect.py` chain.
3. Write one new `PlatformToolkit` implementation in `infrastructure/` (its
   package manager, service manager, and repository provider if needed).
4. Wire it into `container.py`.

Nothing in `domain/` or `application/` changes.
