$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

Write-Host "Starting WeChat digest scheduler. Keep this window open."
Write-Host "The digest time is configured in config.yaml -> digest.time."
& .\.venv\Scripts\python.exe -m wechat_digest run
