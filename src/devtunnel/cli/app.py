"""Thin Typer CLI: parse, delegate to a use case, render the result.

No business logic lives here -- every command builds an
:class:`~devtunnel.application.context.ExecutionContext` via
:func:`devtunnel.container.build_context`, resolves its inputs, and hands off
to one use case from :mod:`devtunnel.application.use_cases`.

The surface grew because a tailcat connection is two-sided in a way an ngrok
tunnel never was. ``install --client`` provisions the other end, ``pair``
carries both public keys across as one unit, and ``connect`` closes the loop.
``allow`` and ``authorize`` exist underneath ``pair`` as escape hatches for
operators who genuinely need one half at a time -- but ``pair`` is the command
the documentation leads with, because handling the two halves separately is
how a peer ends up half-enrolled or half-revoked.
"""

from __future__ import annotations

import json
import time

import typer
from rich.console import Console
from rich.table import Table

from devtunnel.application import catalog, pairing
from devtunnel.application.allowlist import Allowlist
from devtunnel.application.plan_builder import InstallSettings, PlanBuilder
from devtunnel.application.use_cases.diagnose import DiagnoseUseCase
from devtunnel.application.use_cases.install_environment import InstallEnvironmentUseCase
from devtunnel.application.use_cases.manage_peers import ManagePeersUseCase
from devtunnel.application.use_cases.report_status import ReportStatusUseCase
from devtunnel.application.use_cases.start_tunnel import StartTunnelUseCase
from devtunnel.application.use_cases.uninstall_environment import UninstallEnvironmentUseCase
from devtunnel.config.settings import FileConfig
from devtunnel.container import build_context
from devtunnel.domain.errors import DevtunnelError
from devtunnel.domain.models import GitIdentity, Role, TunnelSpec

app = typer.Typer(
    name="devtunnel",
    help="Provision (and fully remove) a tailcat + SSH dev-tunnel environment.",
    no_args_is_help=True,
)

key_app = typer.Typer(help="Inspect this machine's tailcat key material.")
pair_app = typer.Typer(help="Exchange and revoke peer identities.")
app.add_typer(key_app, name="key")
app.add_typer(pair_app, name="pair")

console = Console()


def _fail(message: str) -> typer.Exit:
    typer.secho(message, fg="red", err=True)
    return typer.Exit(code=1)


# --------------------------------------------------------------------------
# install / uninstall
# --------------------------------------------------------------------------


@app.command()
def install(
    client: bool = typer.Option(False, "--client", help="set this machine up as a client"),
    with_git: bool = typer.Option(False, "--with-git", help="also install git (needs elevation)"),
    git_name: str | None = typer.Option(None, "--git-name", help="git user.name to configure"),
    git_email: str | None = typer.Option(None, "--git-email", help="git user.email to configure"),
    tailcat_version: str | None = typer.Option(
        None, "--tailcat-version", help="pinned version, or 'latest'"
    ),
    key_name: str | None = typer.Option(None, "--key-name", help="tailcat key name"),
    authorized_keys: list[str] = typer.Option(
        [], "--authorized-keys", help="path, literal public key, or user@github"
    ),
    allow: list[str] = typer.Option([], "--allow", help="node key permitted to connect"),
    peer: list[str] = typer.Option([], "--peer", help="a dtp1: pairing bundle"),
    ssh_identity: str | None = typer.Option(None, "--ssh-identity", help="SSH key path to use"),
    region: str | None = typer.Option(None, "--region", help="tailcat region"),
    fixed_region: bool = typer.Option(False, "--fixed-region", help="pin the region"),
    add_to_path: bool = typer.Option(False, "--add-to-path", help="put tailcat on PATH"),
    system: bool = typer.Option(False, "--system", help="install machine-wide (needs elevation)"),
    skip: list[str] = typer.Option([], "--skip", help="package keys to skip"),
    config: str | None = typer.Option(None, "--config", help="path to a JSON config file"),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="never prompt; fail instead"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="show what would change, without changing it"
    ),
    json_output: bool = typer.Option(False, "--json", help="emit machine-readable progress events"),
) -> None:
    """Install tailcat and configure this machine's half of the connection."""

    file_cfg = FileConfig.load(config)
    role = Role.CLIENT if (client or file_cfg.role == "client") else Role.SERVER
    resolved_key = key_name or file_cfg.key_name or (
        catalog.DEFAULT_CLIENT_KEY if role is Role.CLIENT else catalog.DEFAULT_SERVER_KEY
    )

    ctx = build_context(
        dry_run=dry_run,
        non_interactive=non_interactive,
        json_output=json_output,
        system_scope=system,
        tailcat_version=tailcat_version or file_cfg.tailcat_version,
        key_name=resolved_key,
    )

    keys = tuple(authorized_keys) or tuple(file_cfg.authorized_keys)
    if role is Role.SERVER and not keys and not peer and not file_cfg.peers:
        keys = _prompt_for_authorized_keys(ctx, non_interactive)

    settings = InstallSettings(
        role=role,
        with_git=with_git or file_cfg.with_git,
        git_identity=GitIdentity(
            name=git_name or file_cfg.git_name, email=git_email or file_cfg.git_email
        ),
        tailcat_version=tailcat_version or file_cfg.tailcat_version,
        key_name=resolved_key,
        region=region or file_cfg.region,
        fixed_region=fixed_region or file_cfg.fixed_region,
        authorized_keys=keys,
        allow=tuple(allow) or tuple(file_cfg.allow),
        peers=tuple(peer) or tuple(file_cfg.peers),
        ssh_identity=ssh_identity or file_cfg.ssh_identity,
        add_to_path=add_to_path or file_cfg.add_to_path,
        system_scope=system,
        skip_packages=frozenset(skip) | frozenset(file_cfg.skip_packages),
    )

    use_case = InstallEnvironmentUseCase(ctx, PlanBuilder(ctx.toolkit, ctx.paths, ctx.process))
    try:
        report = use_case.execute(settings)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    if report.failed:
        raise typer.Exit(code=1)
    if dry_run:
        return

    if role is Role.CLIENT:
        _print_pairing_bundle(ctx, settings.key_name)
    else:
        _print_server_summary(ctx, settings.key_name)


@app.command()
def uninstall(
    force: bool = typer.Option(False, "--force", help="continue past a failed revert"),
    keep: list[str] = typer.Option([], "--keep", help="package keys to leave installed"),
    dry_run: bool = typer.Option(False, "--dry-run", help="show what would be reverted"),
    yes: bool = typer.Option(False, "--yes", help="skip the confirmation prompt"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Reverse every change devtunnel made, most recent first."""

    ctx = build_context(dry_run=dry_run, json_output=json_output)

    if not dry_run and not yes:
        if not ctx.prompter.confirm(
            "This will remove everything devtunnel installed on this machine, "
            "and revoke every peer. Continue?",
            default=False,
        ):
            raise typer.Exit(code=1)

    try:
        report = UninstallEnvironmentUseCase(ctx).execute(force=force, keep=frozenset(keep))
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    if dry_run:
        return
    if report.fully_reverted:
        typer.secho(
            "Uninstall complete. Run 'uv tool uninstall devtunnel' to remove the CLI itself.",
            fg="green",
        )
    else:
        raise _fail(
            f"{report.remaining_failures} change(s) could not be reverted; "
            "re-run 'devtunnel uninstall' to retry, or pass --force."
        )


# --------------------------------------------------------------------------
# keys and addresses
# --------------------------------------------------------------------------


@key_app.command("show")
def key_show(key_name: str | None = typer.Option(None, "--key-name")) -> None:
    """Print this machine's node key, for a peer's allowlist."""

    ctx = build_context()
    name = key_name or catalog.DEFAULT_CLIENT_KEY
    try:
        typer.echo(ctx.tunnel_provider.node_key(name))
    except Exception as exc:  # noqa: BLE001
        raise _fail(str(exc)) from exc


@key_app.command("list")
def key_list() -> None:
    """List the tailcat keys devtunnel knows about."""

    ctx = build_context()
    table = Table()
    table.add_column("Key")
    table.add_column("Address")
    for name in (catalog.DEFAULT_SERVER_KEY, catalog.DEFAULT_CLIENT_KEY):
        if ctx.tunnel_provider.has_key(name):
            table.add_row(name, ctx.tunnel_provider.address_for(name) or "-")
    console.print(table)


@app.command()
def address(key_name: str | None = typer.Option(None, "--key-name")) -> None:
    """Print the stable address peers connect to."""

    ctx = build_context()
    value = ctx.tunnel_provider.address_for(key_name or catalog.DEFAULT_SERVER_KEY)
    if not value:
        raise _fail("no saved address; run 'devtunnel install' first")
    typer.echo(value)


# --------------------------------------------------------------------------
# pairing
# --------------------------------------------------------------------------


@pair_app.command("export")
def pair_export(
    out: str | None = typer.Option(None, "--out", help="write the bundle to this file"),
    name: str | None = typer.Option(None, "--name", help="label to identify this machine"),
    key_name: str | None = typer.Option(None, "--key-name"),
) -> None:
    """Print this machine's pairing bundle: both public keys, as one token."""

    ctx = build_context()
    try:
        bundle = _local_bundle(ctx, key_name or catalog.DEFAULT_CLIENT_KEY, name)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    token = bundle.encode()
    if out:
        ctx.filesystem.write_text(out, token + "\n")
        typer.secho(f"Wrote {out}", fg="green")
    else:
        typer.echo(token)
    _print_bundle_detail(bundle)


@pair_app.command("add")
def pair_add(
    bundle_token: str | None = typer.Argument(None, metavar="BUNDLE"),
    from_file: str | None = typer.Option(None, "--from", help="read the bundle from a file"),
    yes: bool = typer.Option(False, "--yes", help="skip the fingerprint confirmation"),
) -> None:
    """Authorise a peer: allowlist + authorized_keys, as one operation."""

    ctx = build_context()
    token = bundle_token
    if from_file:
        token = (ctx.filesystem.read_text(from_file) or "").strip()
    if not token:
        raise _fail("pass a dtp1: bundle, or --from <file>")

    try:
        bundle = pairing.decode_bundle(token)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    typer.echo(f'Peer "{bundle.name}"')
    _print_bundle_detail(bundle)

    # A bundle carries only public material, but it *is* an authorisation
    # request: anyone who can put one in the operator's clipboard gets both
    # tunnel and shell access. The fingerprint is there to be checked out of
    # band, so the prompt is the default and --yes is where the operator
    # takes that verification on themselves.
    if not yes and not ctx.prompter.confirm(
        "Confirm this fingerprint with them out of band. Add this peer?", default=False
    ):
        raise typer.Exit(code=1)

    try:
        _, outcome = ManagePeersUseCase(ctx).add(token)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    typer.secho(f"  + allowlist          {bundle.nodekey_short}", fg="green")
    typer.secho(f"  + authorized_keys    {bundle.sshkey_short}", fg="green")
    if outcome.status.value == "skipped":
        typer.secho("  (this peer was already authorised)", fg="yellow")

    server_address = ctx.tunnel_provider.address_for(catalog.DEFAULT_SERVER_KEY)
    if server_address:
        typer.echo("\nSend them back:")
        typer.echo(f"  devtunnel connect {server_address}")

    # A running tunnel reads its allowlist once, at startup.
    typer.secho(
        "\nThe allowlist changed. Restart the tunnel for it to take effect "
        "('devtunnel serve --stop' then '--install-service', or Ctrl+C a foreground 'up').",
        fg="yellow",
    )


@pair_app.command("list")
def pair_list(json_output: bool = typer.Option(False, "--json")) -> None:
    """List every enrolled peer."""

    ctx = build_context()
    peers = ManagePeersUseCase(ctx).list_peers()

    if json_output:
        typer.echo(
            json.dumps(
                [
                    {
                        "name": p.name,
                        "nodekey": p.nodekey,
                        "sshkey": p.sshkey,
                        "fingerprint": p.fingerprint,
                        "created": p.created,
                    }
                    for p in peers
                ]
            )
        )
        return

    if not peers:
        typer.echo("No peers are enrolled.")
        return
    table = Table()
    table.add_column("Name")
    table.add_column("Node key")
    table.add_column("Fingerprint")
    for peer in peers:
        table.add_row(peer.name, peer.nodekey[:24] + "...", peer.fingerprint)
    console.print(table)


@pair_app.command("remove")
def pair_remove(identifier: str = typer.Argument(..., metavar="NAME_OR_NODEKEY")) -> None:
    """Revoke a peer: both halves, or a loud failure."""

    ctx = build_context()
    try:
        outcome = ManagePeersUseCase(ctx).remove(identifier)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    if outcome.status.value == "revert_failed":
        raise _fail(f"peer was only partially revoked: {outcome.message}")
    typer.secho(f"Revoked {identifier}: allowlist and authorized_keys.", fg="green")


@pair_app.command("fingerprint")
def pair_fingerprint(
    bundle_token: str | None = typer.Argument(None, metavar="BUNDLE"),
    key_name: str | None = typer.Option(None, "--key-name"),
) -> None:
    """Print the short digest to compare out of band before adding a peer."""

    if bundle_token:
        try:
            bundle = pairing.decode_bundle(bundle_token)
        except DevtunnelError as exc:
            raise _fail(str(exc)) from exc
        typer.echo(f"dtp fingerprint: {bundle.fingerprint}")
        return

    ctx = build_context()
    try:
        bundle = _local_bundle(ctx, key_name or catalog.DEFAULT_CLIENT_KEY, None)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc
    typer.echo(f"dtp fingerprint: {bundle.fingerprint}")


# --------------------------------------------------------------------------
# lower-level escape hatches
# --------------------------------------------------------------------------


@app.command()
def allow(
    node_keys: list[str] = typer.Argument(None),
    remove: str | None = typer.Option(None, "--remove", help="drop this node key"),
    list_only: bool = typer.Option(False, "--list", help="print the allowlist"),
) -> None:
    """Edit the tunnel allowlist directly.

    One half of a peer's identity. Prefer 'pair add'/'pair remove', which keep
    the allowlist and authorized_keys in step.
    """

    from devtunnel.application.revert import StepReverter
    from devtunnel.application.steps import EnsureAllowlistEntryStep
    from devtunnel.domain.journal import ChangeKind

    ctx = build_context()
    allowlist = Allowlist(ctx.filesystem, ctx.paths.allowlist_path)

    if list_only:
        for entry in allowlist.entries():
            typer.echo(entry)
        return

    if remove:
        # Revert the record rather than editing the file, so the journal and
        # the allowlist cannot disagree about who is admitted.
        wanted = pairing.normalise_nodekey(remove)
        records = [
            r
            for r in ctx.journal.load()
            if r.kind is ChangeKind.FILE_MODIFIED
            and r.target == f"allow:{wanted}"
            and r.needs_revert
        ]
        for record in records:
            StepReverter(ctx.platform).for_record(record).revert(ctx, record)
        if not records:
            # Added by hand, outside devtunnel. Still removable -- just not
            # something there was ever a record of.
            typer.echo("removed (untracked)" if allowlist.remove(wanted) else "absent")
            return
        typer.echo("removed")
        return

    for index, raw in enumerate(node_keys or []):
        node_key = pairing.normalise_nodekey(raw)
        step = EnsureAllowlistEntryStep(f"allow-{index}", f"Allow {node_key[:24]}...", node_key)
        try:
            step.apply(ctx)
        except DevtunnelError as exc:
            raise _fail(str(exc)) from exc
    typer.secho("Allowlist updated. Restart the tunnel for it to take effect.", fg="yellow")


@app.command()
def authorize(
    entries: list[str] = typer.Argument(None),
    remove: str | None = typer.Option(None, "--remove", help="drop this public key"),
) -> None:
    """Edit authorized_keys directly.

    The other half of a peer's identity. Prefer 'pair add'/'pair remove' --
    a key authorised here but never allow-listed is a shell credential sitting
    on the box for someone who cannot reach it, and nothing will remind you.
    """

    ctx = build_context()
    from devtunnel.application.steps import EnsureAuthorizedKeysStep

    if remove:
        raise _fail(
            "removing a single key by hand is not supported; use 'devtunnel pair remove', "
            "which revokes both halves, or edit authorized_keys yourself."
        )

    step = EnsureAuthorizedKeysStep(
        "authorize", "Install the authorized SSH keys", tuple(entries or ())
    )
    try:
        step.apply(ctx)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc
    typer.secho("authorized_keys updated.", fg="green")


# --------------------------------------------------------------------------
# tunnel
# --------------------------------------------------------------------------


@app.command()
def up(
    serve: str = typer.Option("ssh", "--serve", help="'ssh', 'no-auth-ssh', or ports"),
    key_name: str | None = typer.Option(None, "--key-name"),
    allow_keys: list[str] = typer.Option([], "--allow"),
    region: str | None = typer.Option(None, "--region"),
    bind: str | None = typer.Option(None, "--bind"),
    open_tunnel: bool = typer.Option(False, "--open", help="no allowlist: the address is the key"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Open a foreground tailcat tunnel. Ctrl+C closes it."""

    name = key_name or catalog.DEFAULT_SERVER_KEY
    ctx = build_context(json_output=json_output, key_name=name)
    use_case = StartTunnelUseCase(ctx)

    spec = TunnelSpec(
        serve=(serve,),
        key_name=name,
        allow=tuple(pairing.normalise_nodekey(k) for k in allow_keys),
        authorized_keys=(ctx.paths.authorized_keys_path,),
        region=region,
        bind=bind,
    )

    try:
        spec = use_case.resolve_spec(spec, open_tunnel=open_tunnel)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    if spec.is_open:
        typer.secho(
            "WARNING: no peers are allow-listed. Anyone who learns this address can "
            "connect -- the address IS the credential.",
            fg="red",
        )

    # Print the connection command before the tunnel is confirmed up: with a
    # persistent key the address is already known, so there is no reason to
    # make the user wait for a banner to find out where to connect.
    known = ctx.tunnel_provider.address_for(name) if name else None
    if known:
        typer.secho(f"devtunnel connect {known}", fg="green")
        typer.echo(f"  (raw: tailcat ssh {known})")

    try:
        handle = use_case.start(spec)
    except (DevtunnelError, RuntimeError, TimeoutError) as exc:
        raise _fail(str(exc)) from exc

    if not known:
        typer.secho(f"devtunnel connect {handle.address}", fg="green")
        typer.echo(f"  (raw: {handle.connect_command})")
    typer.echo("Press Ctrl+C to close the tunnel.")
    try:
        while handle.process.poll() is None:
            time.sleep(0.5)
        typer.secho("tailcat exited unexpectedly.", fg="red", err=True)
    except KeyboardInterrupt:
        pass
    finally:
        use_case.stop(handle)
        typer.echo("Tunnel closed.")


@app.command()
def connect(
    target: str = typer.Argument(..., metavar="ADDRESS"),
    key_name: str | None = typer.Option(None, "--key-name"),
    ssh_identity: str | None = typer.Option(
        None, "--ssh-identity", help="(unsupported upstream -- see below)"
    ),
    expect: str | None = typer.Option(
        None, "--expect", help="refuse unless the address decodes to this node key"
    ),
) -> None:
    """Open an SSH session to a host address over tailcat.

    ``tailcat ssh`` wraps the stock ssh client behind a ProxyCommand, so the
    identity offered is whatever OpenSSH would pick on its own: ``~/.ssh/id_*``
    and ``ssh-agent``, with ordinary semantics. tailcat documents no ``-i``
    passthrough and no way to forward arguments to the underlying client, so
    devtunnel cannot select a non-default identity here -- and refuses rather
    than connecting as the wrong one and leaving the user to debug a
    publickey failure that devtunnel caused.
    """

    ctx = build_context(key_name=key_name or catalog.DEFAULT_CLIENT_KEY)

    if ssh_identity:
        raise _fail(
            "tailcat ssh offers no way to pass an SSH identity through to the stock "
            "ssh client, so --ssh-identity cannot be honoured on connect. Either use "
            "the default identity, or add this key to ssh-agent and let OpenSSH "
            "select it."
        )

    if expect:
        # The host's address is not secret, but connecting to a substituted
        # one is still worth refusing. tailcat can decode an address, so the
        # check is cheap and purely local.
        parsed = ctx.process.run([ctx.paths.binary_path, "parse", target], check=False)
        if not parsed.ok:
            raise _fail(f"could not decode the address to check --expect: {parsed.stderr.strip()}")
        wanted = pairing.normalise_nodekey(expect).removeprefix("nodekey:")
        if wanted not in parsed.stdout:
            raise _fail(f"address does not match --expect {expect}; refusing to connect")

    raise typer.Exit(code=_run_foreground([ctx.paths.binary_path, "ssh", target]))


@app.command()
def serve(
    install_service: bool = typer.Option(False, "--install-service", help="register the service"),
    remove_service: bool = typer.Option(False, "--remove-service", help="unregister it"),
    stop: bool = typer.Option(False, "--stop", help="stop the running service"),
    service_status: bool = typer.Option(False, "--status", help="report the service state"),
    system: bool = typer.Option(False, "--system", help="a system service (needs elevation)"),
    key_name: str | None = typer.Option(None, "--key-name"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Run the tunnel as a persistent, journaled service.

    This is where the journal keeps earning its place now that the install
    plan has shrunk: a registered service that restarts at logon is exactly
    the kind of change a user would not want left behind, and every bit of it
    is reversible.
    """

    ctx = build_context(
        json_output=json_output,
        system_scope=system,
        key_name=key_name or catalog.DEFAULT_SERVER_KEY,
    )
    service = catalog.TUNNEL_SERVICE_SYSTEM if system else catalog.TUNNEL_SERVICE
    manager = ctx.toolkit.service_manager_for(service.scope)

    if service_status:
        state = manager.get_state(service)
        typer.echo(
            f"exists={state.exists} running={state.running} "
            f"automatic={state.startup_automatic}"
        )
        return

    if stop:
        from devtunnel.application.ports.service_manager import ServiceState

        manager.apply_state(service, ServiceState(True, running=False, startup_automatic=False))
        typer.secho("Tunnel service stopped.", fg="green")
        return

    if remove_service:
        # Revert only the service's own record. Handing this to the full
        # uninstall use case would unwind the whole journal -- binary, keys,
        # peers and all -- which is emphatically not what "--remove-service"
        # asks for.
        from devtunnel.application.revert import StepReverter
        from devtunnel.domain.journal import ChangeKind

        records = [
            r
            for r in ctx.journal.load()
            if r.kind is ChangeKind.SERVICE_STATE_CHANGED
            and r.target == f"service:{service.key}"
            and r.needs_revert
        ]
        if not records:
            raise _fail("no tunnel service is registered by devtunnel")

        reverter = StepReverter(ctx.platform)
        for record in reversed(records):
            outcome = reverter.for_record(record).revert(ctx, record)
            if outcome.status.value == "revert_failed":
                raise _fail(f"the service could not be removed: {outcome.message}")
        typer.secho("Tunnel service removed.", fg="green")
        return

    if not install_service:
        raise _fail("pass one of --install-service, --stop, --status or --remove-service")

    from devtunnel.application.steps import EnsureServiceStep

    step = EnsureServiceStep("tunnel-service", f"Register {service.display_name}", service)
    try:
        step.apply(ctx)
    except DevtunnelError as exc:
        raise _fail(str(exc)) from exc

    # loginctl enable-linger needs polkit or root on a headless host. A unit
    # that will silently die at logout must never be reported as healthy.
    linger_error = getattr(manager, "last_linger_error", None)
    if linger_error:
        typer.secho(
            f"WARNING: {linger_error}\n"
            "The tunnel will NOT survive logout. "
            "Re-run with --system for a service that does.",
            fg="red",
            err=True,
        )
    else:
        typer.secho("Tunnel service registered and running.", fg="green")


# --------------------------------------------------------------------------
# status / doctor
# --------------------------------------------------------------------------


@app.command()
def status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show what devtunnel has installed, and whether a tunnel is listening."""

    ctx = build_context()
    report = ReportStatusUseCase(ctx).execute()

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "platform": report.platform.value,
                    "journal_location": report.journal_location,
                    "elevated": report.elevated,
                    "listening": report.listening,
                    "address": report.address,
                    "records": [
                        {
                            "id": r.id,
                            "kind": r.kind.value,
                            "target": r.target,
                            "status": r.status.value,
                            "scope": r.details.get("scope"),
                        }
                        for r in report.records
                    ],
                }
            )
        )
        return

    console.print(f"Platform: {report.platform.value}")
    console.print(f"Journal: {report.journal_location}")
    console.print(f"Elevated: {report.elevated}")
    console.print(f"Address: {report.address or '-'}")
    if report.listening:
        console.print("[green]Tunnel service: registered and RUNNING (accepting connections)")
    elif report.tunnel_service.exists:
        console.print("[yellow]Tunnel service: registered but not running")
    else:
        console.print("Tunnel service: not registered")

    if not report.records:
        console.print("Nothing installed yet.")
        return
    table = Table()
    table.add_column("Target")
    table.add_column("Kind")
    table.add_column("Scope")
    table.add_column("Status")
    for record in report.records:
        table.add_row(
            record.target,
            record.kind.value,
            record.details.get("scope", "-"),
            record.status.value,
        )
    console.print(table)


@app.command()
def doctor() -> None:
    """Check for drift between the journal and the machine's real state."""

    ctx = build_context()
    report = DiagnoseUseCase(ctx).execute()

    if report.legacy_records:
        typer.secho(report.legacy_notice, fg="yellow")
    if report.clean:
        typer.secho("No drift detected.", fg="green")
        return
    for finding in report.findings:
        typer.secho(f"! {finding.target}: {finding.message}", fg="yellow")
    raise typer.Exit(code=1)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _run_foreground(argv: list[str]) -> int:
    import subprocess

    # Deliberately NOT through ProcessRunnerPort: an interactive ssh session
    # needs the real terminal, and the port's contract is to capture output.
    return subprocess.call(argv)


def _local_bundle(ctx, key_name: str, label: str | None):
    import socket

    node_key = ctx.tunnel_provider.node_key(key_name)
    identity = ctx.paths.default_ssh_identity()
    public = (ctx.filesystem.read_text(f"{identity}.pub") or "").strip()
    if not public:
        raise DevtunnelError(
            f"no SSH public key at {identity}.pub; run 'devtunnel install --client' first"
        )
    name = label or f"{_current_user()}@{socket.gethostname()}"
    return pairing.build_bundle(name=name, nodekey=node_key, sshkey=public)


def _current_user() -> str:
    import os

    return (
        os.environ.get("SUDO_USER")
        or os.environ.get("USER")
        or os.environ.get("USERNAME")
        or "user"
    )


def _print_bundle_detail(bundle) -> None:
    typer.echo(f"  tunnel key   {bundle.nodekey_short}")
    typer.echo(f"  shell key    {bundle.sshkey_short}")
    typer.echo(f"  fingerprint  {bundle.fingerprint}")


def _print_pairing_bundle(ctx, key_name: str) -> None:
    try:
        bundle = _local_bundle(ctx, key_name, None)
    except DevtunnelError as exc:
        typer.secho(str(exc), fg="yellow", err=True)
        return
    typer.echo("\nYour pairing bundle -- send this to whoever runs the host:\n")
    typer.secho(f"  {bundle.encode()}\n", fg="cyan")
    _print_bundle_detail(bundle)
    typer.echo("\nThey run:  devtunnel pair add dtp1:...")


def _print_server_summary(ctx, key_name: str) -> None:
    value = ctx.tunnel_provider.address_for(key_name)
    typer.secho("Install complete.", fg="green")
    if value:
        typer.echo(f"Your address: {value}")
    typer.echo("Next: 'devtunnel pair add <bundle>' to authorise a client, then 'devtunnel up'.")


def _prompt_for_authorized_keys(ctx, non_interactive: bool) -> tuple[str, ...]:
    """Ask once, and only when nothing can be inferred.

    Under D1 ``authorized_keys`` is the entire authentication boundary, so an
    install that silently configures none produces a tunnel nobody can log in
    through. Inferring from ``~/.ssh/*.pub`` covers the common case; the prompt
    covers the rest; ``--non-interactive`` gets neither and is told plainly.
    """

    import os

    for candidate in ("id_ed25519.pub", "id_ecdsa.pub", "id_rsa.pub"):
        path = os.path.join(ctx.paths.ssh_dir, candidate)
        if ctx.filesystem.exists(path):
            typer.secho(f"Using {path} as the authorized key.", fg="cyan")
            return (path,)

    if non_interactive:
        typer.secho(
            "No authorized keys were supplied and none could be inferred; "
            "the tunnel will accept no logins. Pass --authorized-keys.",
            fg="yellow",
            err=True,
        )
        return ()

    answer = ctx.prompter.ask_text(
        "Which public key should be allowed to log in? (path, key, or user@github)",
        default="",
    ).strip()
    return (answer,) if answer else ()


def main() -> None:
    app()


if __name__ == "__main__":
    main()
