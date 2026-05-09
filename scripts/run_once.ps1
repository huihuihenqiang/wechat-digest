param(
    [string]$Date = (Get-Date -Format "yyyy-MM-dd"),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$ArgsList = @("-m", "wechat_digest", "digest-now", "--date", $Date, "--collect-once")
if ($DryRun) {
    $ArgsList += "--dry-run"
}

& .\.venv\Scripts\python.exe @ArgsList
