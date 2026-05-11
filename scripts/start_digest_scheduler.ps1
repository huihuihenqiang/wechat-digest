$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$LogDir = Join-Path $Root "logs"
$DataDir = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
New-Item -ItemType Directory -Force -Path $DataDir | Out-Null

$PidFile = Join-Path $DataDir "digest-scheduler.pid"
$RootPattern = [regex]::Escape($Root)
$ExistingSchedulers = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.CommandLine -match $RootPattern -and
    $_.CommandLine -match "wechat-digest|wechat_digest" -and
    $_.CommandLine -match "\srun(?:\s|$)"
}
if ($ExistingSchedulers) {
    $existingPid = @($ExistingSchedulers)[0].ProcessId
    Set-Content -LiteralPath $PidFile -Value $existingPid -Encoding ASCII
    exit 0
}

if (Test-Path -LiteralPath $PidFile) {
    $existingPidText = (Get-Content -LiteralPath $PidFile -Raw -ErrorAction SilentlyContinue).Trim()
    if ($existingPidText -match '^\d+$') {
        $existingProcess = Get-CimInstance Win32_Process -Filter "ProcessId = $existingPidText" -ErrorAction SilentlyContinue
        if ($existingProcess -and $existingProcess.CommandLine -match "wechat-digest|wechat_digest") {
            exit 0
        }
    }
}

$Cli = Join-Path $Root ".venv\Scripts\wechat-digest.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Config = Join-Path $Root "config.yaml"
$EnvFile = Join-Path $Root ".env"
$Stdout = Join-Path $LogDir "digest-scheduler.out.log"
$Stderr = Join-Path $LogDir "digest-scheduler.err.log"

if (Test-Path -LiteralPath $Cli) {
    $FilePath = $Cli
    $Args = @("--config", $Config, "--env", $EnvFile, "run")
} elseif (Test-Path -LiteralPath $Python) {
    $FilePath = $Python
    $Args = @("-m", "wechat_digest", "--config", $Config, "--env", $EnvFile, "run")
} else {
    throw "Could not find wechat-digest executable or venv Python under $Root"
}

$process = Start-Process `
    -FilePath $FilePath `
    -ArgumentList $Args `
    -WorkingDirectory $Root `
    -WindowStyle Hidden `
    -RedirectStandardOutput $Stdout `
    -RedirectStandardError $Stderr `
    -PassThru

Set-Content -LiteralPath $PidFile -Value $process.Id -Encoding ASCII
