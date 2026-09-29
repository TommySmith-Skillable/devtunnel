"""Thin Typer CLI: parse, delegate to a use case, render the result.

No business logic lives here -- every command builds an
:class:`~devtunnel.application.context.ExecutionContext` via
:func:`devtunnel.container.build_context`, resolves its inputs, and hands off
to one use case from :mod:`devtunnel.application.use_cases`.
"""

from __future__ import annotations

import json
import time

import typer
from rich.console import Console
from rich.table import Table

from devtunnel.application.plan_builder import InstallSettings, PlanBuilder
from devtunnel.application.use_cases.diagnose import DiagnoseUseCase
from devtunnel.application.use_cases.install_environment import InstallEnvironmentUseCase
from devtunnel.application.use_cases.report_status import ReportStatusUseCase
from devtunnel.application.use_cases.start_tunnel import StartTunnelUseCase
from devtunnel.application.use_cases.uninstall_environment import UninstallEnvironmentUseCase
from devtunnel.config.settings import FileConfig
from devtunnel.container import build_context
from devtunnel.domain.errors import DevtunnelError
from devtunnel.domain.models import GitIdentity, TunnelSpec

app = typer.Typer(
    name="devtunnel",
    help="Provision (and fully remove) a git + ngrok + SSH dev-tunnel environment.",
    no_args_is_help=True,
)


@app.command()
def install(
    git_name: str | None = typer.Option(None, "--git-name", help="git user.name to configure"),
    git_email: str | None = typer.Option(None, "--git-email", help="git user.email to configure"),
    authtoken: str | None = typer.Option(None, "--authtoken", help="ngrok authtoken"),
    configure_authtoken: bool = typer.Option(
        True, "--configure-authtoken/--skip-authtoken", help="Configure ngrok's authtoken now"
    ),
    skip: list[str] = typer.Option([], "--skip", help="package keys to skip (git, ngrok, ...)"),
    config: str | None = typer.Option(None, "--config", help="path to a JSON config file"),
    non_interactive: bool = typer.Option(
        False, "--non-interactive", help="never prompt; fail instead"
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="show what would change, without changing it"
    ),
    json_output: bool = typer.Option(False, "--json", help="emit machine-readable progress events"),
) -> None:
    """Install git, ngrok and OpenSSH, and enable sshd."""

    ctx = build_context(dry_run=dry_run, non_interactive=non_interactive, json_output=json_output)
    file_cfg = FileConfig.load(config)

    identity = GitIdentity(
        name=git_name or file_cfg.git_name,
        email=git_email or file_cfg.git_email,
    )

    token: str | None = None
    if configure_authtoken:
        token = ctx.credentials.resolve(
            what="the ngrok authtoken",
            env_var="NGROK_AUTHTOKEN",
            flag_value=authtoken or file_cfg.ngrok_authtoken,
            non_interactive=non_interactive,
        )

    settings = InstallSettings(
        git_identity=identity,
        ngrok_authtoken=token,
        skip_packages=frozenset(skip) | frozenset(file_cfg.skip_packages),
    )

    use_case = InstallEnvironmentUseCase(ctx, PlanBuilder(ctx.toolkit))
    try:
        report = use_case.execute(settings)
    except DevtunnelError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=1) from exc

    if report.failed:
        raise typer.Exit(code=1)
    if not dry_run:
        typer.secho("Install complete. Run 'devtunnel up' to open the SSH tunnel.", fg="green")


@app.command()
def up(
    port: int = typer.Option(22, "--port", help="local port to tunnel (the SSH port)"),
    region: str | None = typer.Option(None, "--region", help="ngrok region, e.g. 'eu'"),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Open a foreground ngrok TCP tunnel to the local SSH daemon."""

    ctx = build_context(json_output=json_output)
    use_case = StartTunnelUseCase(ctx)

    try:
        handle = use_case.start(TunnelSpec(local_port=port, region=region))
    except (RuntimeError, TimeoutError) as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=1) from exc

    typer.secho(f"Tunnel is up: {handle.ssh_command}", fg="green")
    typer.echo("Press Ctrl+C to close the tunnel.")
    try:
        while handle.process.poll() is None:
            time.sleep(0.5)
        typer.secho("ngrok exited unexpectedly.", fg="red", err=True)
    except KeyboardInterrupt:
        pass
    finally:
        use_case.stop(handle)
        typer.echo("Tunnel closed.")


@app.command()
def status(json_output: bool = typer.Option(False, "--json")) -> None:
    """Show what devtunnel has installed on this machine."""

    ctx = build_context()
    report = ReportStatusUseCase(ctx).execute()

    if json_output:
        typer.echo(
            json.dumps(
                {
                    "platform": report.platform.value,
                    "journal_location": report.journal_location,
                    "elevated": report.elevated,
                    "records": [
                        {
                            "id": r.id,
                            "kind": r.kind.value,
                            "target": r.target,
                            "status": r.status.value,
                        }
                        for r in report.records
                    ],
                }
            )
        )
        return

    console = Console()
    console.print(f"Platform: {report.platform.value}")
    console.print(f"Journal: {report.journal_location}")
    console.print(f"Elevated: {report.elevated}")
    if not report.records:
        console.print("Nothing installed yet.")
        return
    table = Table()
    table.add_column("Target")
    table.add_column("Kind")
    table.add_column("Status")
    for record in report.records:
        table.add_row(record.target, record.kind.value, record.status.value)
    console.print(table)


@app.command()
def doctor() -> None:
    """Check for drift between the journal and the machine's real state."""

    ctx = build_context()
    report = DiagnoseUseCase(ctx).execute()

    if report.clean:
        typer.secho("No drift detected.", fg="green")
        return
    for finding in report.findings:
        typer.secho(f"! {finding.target}: {finding.message}", fg="yellow")
    raise typer.Exit(code=1)


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
            "This will remove everything devtunnel installed on this machine. Continue?",
            default=False,
        ):
            raise typer.Exit(code=1)

    use_case = UninstallEnvironmentUseCase(ctx)
    try:
        report = use_case.execute(force=force, keep=frozenset(keep))
    except DevtunnelError as exc:
        typer.secho(str(exc), fg="red", err=True)
        raise typer.Exit(code=1) from exc

    if dry_run:
        return
    if report.fully_reverted:
        typer.secho(
            "Uninstall complete. Run 'uv tool uninstall devtunnel' to remove the CLI itself.",
            fg="green",
        )
    else:
        typer.secho(
            f"{report.remaining_failures} change(s) could not be reverted; "
            "re-run 'devtunnel uninstall' to retry, or pass --force.",
            fg="red",
            err=True,
        )
        raise typer.Exit(code=1)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
