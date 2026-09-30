<#
.SYNOPSIS
    Installs uv and devtunnel on a remote Azure VM via Invoke-AzVMRunCommand.
.DESCRIPTION
    Runs headless (SYSTEM, no desktop) on the target VM: installs uv, then
    installs devtunnel from its tagged source archive. No git required.
#>

$resourceGroup = "@lab.CloudResourceGroup(ResourceGroup1).Name"
$vmName = "41-622 VM"

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
'@

$runCommandResult = Invoke-AzVMRunCommand `
    -ResourceGroupName $resourceGroup `
    -VMName $vmName `
    -CommandId "RunPowerShellScript" `
    -ScriptString $remoteScript

$runCommandResult.Value | ForEach-Object { Write-Host $_.Message }
