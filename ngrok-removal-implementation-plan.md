# Implementation Plan: Remove all traces of ngrok

## 1. Goal & premise

The tailcat migration is complete and **no ngrok-era installs exist in the
field**, so the deliberately-retained legacy-revert shim no longer earns its
keep. This plan removes every ngrok reference — active code, the legacy shim,
historical comments, tests, and docs — leaving the codebase describing only
what it does today.

## 2. Guiding principle

The legacy shim was load-bearing *only* to revert old journals. Once we accept
no such journals exist, the shim and its two deprecated `ChangeKind` members
can go. The one behavioral change: a journal record with an unrecognized
kind/target now raises `NoReverterRegisteredError` instead of being routed to a
legacy reverter — which is correct, because such a record can no longer be
produced.

## 3. Work breakdown

### Phase A — Delete the legacy shim (active code)

| File | Action |
|---|---|
| `src/devtunnel/application/legacy_revert.py` | **Delete entire file.** |
| `src/devtunnel/domain/journal.py` | Remove the deprecated-kinds comment block and the `APT_REPO_ADDED` / `WINDOWS_CAPABILITY_ADDED` members (lines ~44–51). |

### Phase B — Rewire the reverter

`src/devtunnel/application/revert.py`:
- Change import `from devtunnel.application import catalog, legacy_revert` →
  `from devtunnel.application import catalog`.
- Drop `PlatformId` from the `devtunnel.domain.models` import (becomes unused).
- Remove the legacy fallback in `for_record` (the
  `legacy_revert.legacy_step_for(...)` block); `for_record` now returns the
  live step or raises.
- Remove the `platform` constructor parameter and `self._platform` (it existed
  solely to route legacy Windows-capability records).
- Update the comment on the unknown-package `return None` (currently "may still
  be an ngrok-era package") to state it simply yields
  `NoReverterRegisteredError`.

Update the 3 construction sites to drop the argument:
- `src/devtunnel/application/use_cases/uninstall_environment.py:53` —
  `StepReverter(ctx.platform)` → `StepReverter()`
- `src/devtunnel/cli/app.py:431` — same
- `src/devtunnel/cli/app.py:656` — same

### Phase C — Uninstall elevation logic

`src/devtunnel/application/use_cases/uninstall_environment.py` `_needs_elevation`:
remove the `scope is None` legacy branch that references `APT_REPO_ADDED` /
`WINDOWS_CAPABILITY_ADDED`. Since every record written by the current version
stamps `scope`, simplify to read the stamp directly, keeping a safe default
(`PKGMGR_BOOTSTRAPPED`/machine-scope packages) if ever absent. Update the
docstring to drop the ngrok-era rationale.

### Phase D — Doctor / diagnosis

`src/devtunnel/application/use_cases/diagnose.py`:
- Remove the `legacy_revert` import.
- `DiagnosisReport`: remove the `legacy_records` field, the `legacy_notice`
  property, and the `and not self.legacy_records` term in `clean`.
- `DiagnoseUseCase.execute`: remove the `legacy` counter and the `is_legacy`
  branch; `DiagnosisReport(findings, legacy)` → `DiagnosisReport(findings)`.
- Trim the module docstring's "one new job after the migration" paragraph.

`src/devtunnel/cli/app.py` `doctor()`: remove the `if report.legacy_records:`
block (lines ~762–763).

### Phase E — Tests

`tests/unit/application/test_revert.py`:
- Remove the `legacy_revert` import and the now-unused `PlatformId` import.
- Delete the five legacy tests: apt-repository, authtoken, ngrok-package,
  openssh-server-capability, sshd-service.
- Keep `test_reverter_raises_for_an_unrecognised_target`; optionally add
  `test_an_unknown_package_has_no_reverter` asserting `NoReverterRegisteredError`
  for `package:<unknown>`.

`tests/unit/domain/test_journal.py:16`: rename the sample target
`"package:ngrok"` → `"package:git"` (cosmetic; not a functional dependency).

### Phase F — Historical comments ("all traces")

Reword these to describe the behavior/bug directly, dropping the ngrok
comparison while preserving the rationale. The factual error in the first one
must be fixed regardless:

- `src/devtunnel/infrastructure/windows/chocolatey.py:2` — **factually wrong**:
  "(git, ngrok)" → "(git)".
- `src/devtunnel/application/catalog.py:10`
- `src/devtunnel/application/context.py:9`
- `src/devtunnel/application/paths.py:19`
- `src/devtunnel/application/plan_builder.py:51`
- `src/devtunnel/config/settings.py:3`
- `src/devtunnel/application/ports/process_runner.py:46`
- `src/devtunnel/application/ports/tunnel_provider.py:11`
- `src/devtunnel/infrastructure/tailcat/tailcat_keys.py:8` (lines 8–19, 152)
- `src/devtunnel/infrastructure/tailcat/tailcat_tunnel.py:8`
- `src/devtunnel/infrastructure/release/github_release.py:4`
- `src/devtunnel/infrastructure/toolkits.py:10`
- `src/devtunnel/infrastructure/debian/systemd_user.py:27`
- `src/devtunnel/infrastructure/filesystem.py:5`
- `src/devtunnel/infrastructure/process/dry_run_runner.py:71`
- `src/devtunnel/cli/app.py:8`
- `tests/fakes/fake_ports.py:8`
- `tests/unit/infrastructure/test_tailcat_keys.py:234`

> **Judgment call:** a few comments explain *why* code looks odd by reference to
> the ngrok era (e.g. the "elevation bug carried forward" in `tailcat_keys.py`,
> the stderr-scraping note in `tailcat_tunnel.py`). The bug explanations stand
> on their own once reworded; the "carried forward from ngrok" framing is lost.
> Confirm whether the historical lineage should be removed or just the word.

### Phase G — Docs

- `docs/architecture.md`: delete the "Migration from the ngrok era" section
  (lines ~230–238); reword "Why this exists" (line 14) and the platform-specific
  fixes that cite ngrok; update the layering list that mentions
  `legacy_revert.py` (line 69) and `ChangeKind` deprecation (line 236).
- `README.md`: remove the "Upgrading from the ngrok-era version" note
  (lines ~241, 272).
- `docs/tailcat-migration-plan.md`: **decision point** — this entire doc is
  "replace ngrok with tailcat." Either (a) delete it now that the migration is
  done, or (b) keep it as a historical record. "Remove all traces" implies (a).
  Recommend deleting it; flag for confirmation.
- `pyproject.toml`: description mentions no ngrok — no change.

## 4. Risks & mitigations

| Risk | Mitigation |
|---|---|
| An unrecognized-kind record now hard-raises `NoReverterRegisteredError`. | Intended. No current writer produces the removed kinds; covered by the retained "unrecognised target" test. |
| Removing a `StrEnum` member breaks deserialization of a stored journal containing it. | Premise: no such journals exist. If any did, `JsonJournalRepository` would raise on load — the single assumption the whole change rests on. |
| `StepReverter` signature change missed at a call site. | Only 3 sites (verified); `ruff`/tests catch a miss. |
| Deleting the migration-plan doc loses project history. | Confirm before deleting (decision point above). |

## 5. Verification

1. `uv run ruff check .` — catches unused imports (`legacy_revert`,
   `PlatformId`) and dead params.
2. `uv run ruff format --check .`
3. `uv run pytest -q` — full suite green after the legacy tests are removed.
4. `rg -i ngrok` returns **zero** matches across the repo — the acceptance gate
   for "all traces removed."
5. Manual smoke: `devtunnel doctor` and `devtunnel uninstall --dry-run` on a
   current-version journal still behave identically.

## 6. Out of scope

- No change to the live install/uninstall/pairing behavior.
- No change to `ChangeKind` members still in use.
- No new functionality.

## 7. Open confirmations before implementation

1. Delete `docs/tailcat-migration-plan.md` entirely vs. keep as history.
2. Strip the "carried forward from ngrok" *lineage* framing in the `tailcat_*`
   comments, not just the word.
