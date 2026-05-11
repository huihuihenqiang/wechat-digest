$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
& (Join-Path $Root "scripts\stop_digest_scheduler.ps1")

$openclaw = Join-Path $env:APPDATA "npm\openclaw.cmd"
if (Test-Path -LiteralPath $openclaw) {
    & $openclaw gateway stop
}

Start-Sleep -Milliseconds 500
$listeners = Get-NetTCPConnection -LocalPort 18789 -State Listen
foreach ($listener in $listeners) {
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $($listener.OwningProcess)"
    if ($processInfo -and $processInfo.CommandLine -match "openclaw|node") {
        Stop-Process -Id $listener.OwningProcess -Force
        Write-Host "Stopped OpenClaw Gateway process: $($listener.OwningProcess)"
    }
}

Write-Host "WeChat Digest background services are stopped."
