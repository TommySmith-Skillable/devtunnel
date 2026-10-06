<#
.SYNOPSIS
    Installs uv, devtunnel and tailcat on a remote Azure VM via Invoke-AzVMRunCommand.
.DESCRIPTION
    Runs headless (SYSTEM, no desktop) on the target VM: installs uv, installs
    devtunnel from its tagged source archive, then has devtunnel download and
    verify tailcat and generate the host's tunnel key. No git required, and no
    account or authtoken of any kind — tailcat's identity is a keypair made on
    the VM.

    Scope: Invoke-AzVMRunCommand runs as SYSTEM, so this script installs
    machine-wide (`--system`) and the tunnel is a machine service, not a
    user's. That is deliberate. A user-scope install run as SYSTEM would put
    the key, the allowlist and authorized_keys under
    C:\Windows\system32\config\systemprofile, where a later interactive
    `devtunnel` run as a normal user would never find them — provisioned and
    apparently fine, but unusable by the person who logs in. Machine scope
    keeps every piece in one consistent, discoverable place.

    If you instead want the tunnel owned by a specific interactive user, do
    not use this script for the install step: run `devtunnel install` on the
    VM as that account, which needs no elevation at all.
#>

$resourceGroup = "@lab.CloudResourceGroup(ResourceGroup1).Name"
$vmName = "41-622 VM"

# The public key allowed to log in. tailcat serves SSH itself, so
# authorized_keys is the *entire* authentication boundary — an install with
# nothing here produces a tunnel nobody can log in through. Accepts a literal
# key, a path on the VM, or "user@github".
$authorizedKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAA... you@laptop"

$remoteScript = @'
# Runs as SYSTEM, non-interactive — no desktop, no UI.

# Install uv (standalone installer, prebuilt binary)
irm https://astral.sh/uv/install.ps1 | iex

# uv updates the persistent PATH, not this session — pick it up manually
$uvBin = Join-Path $env:USERPROFILE ".local\bin"
$env:PATH = "$uvBin;$env:PATH"

# Install devtunnel from the tagged archive (git-free)
uv tool install https://github.com/TommySmith-Skillable/devtunnel/archive/refs/tags/v0.0.1.tar.gz

# Same PATH caveat applies to devtunnel.exe
uv tool update-shell
$env:PATH = "$uvBin;$env:PATH"

# Install tailcat and generate this host's key. --system because this runs as
# SYSTEM and the tunnel belongs to the machine (see the Scope note above);
# --non-interactive because there is no console to prompt at; and
# --authorized-keys because without it the install has nothing to infer from
# and would configure no logins at all.
devtunnel install --system --non-interactive --authorized-keys '__AUTHORIZED_KEY__'

# The tc... address the client connects to. Hand this to whoever is pairing.
devtunnel address
'@

# The here-string is single-quoted so the remote script's own $env: references
# survive verbatim; substitute the one local value by placeholder instead.
$remoteScript = $remoteScript.Replace('__AUTHORIZED_KEY__', $authorizedKey)

$runCommandResult = Invoke-AzVMRunCommand `
    -ResourceGroupName $resourceGroup `
    -VMName $vmName `
    -CommandId "RunPowerShellScript" `
    -ScriptString $remoteScript

$runCommandResult.Value | ForEach-Object { Write-Host $_.Message }

# Next steps, from your own machine:
#   devtunnel install --client          -> prints a dtp1: pairing bundle
# then on the VM (RunCommand or a console):
#   devtunnel pair add dtp1:... --yes          -> allowlist + authorized_keys
#   devtunnel serve --install-service --system  -> a real service, survives reboot
# and back on your machine:
#   devtunnel connect tc...
#
# Run the VM-side commands the same way this script does (RunCommand, as
# SYSTEM) so they act on the same machine-scope install. Mixing an
# interactive `devtunnel pair add` with a --system install would write the
# peer into the interactive user's profile, and the machine's tunnel would
# never see it.
