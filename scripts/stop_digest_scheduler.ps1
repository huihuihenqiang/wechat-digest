$ErrorActionPreference = "SilentlyContinue"

$Root = Split-Path -Parent $PSScriptRoot
$DataDir = Join-Path $Root "data"
$PidFile = Join-Path $DataDir "digest-scheduler.pid"
$Stopped = New-Object System.Collections.Generic.List[int]
$RootPattern = [regex]::Escape($Root)

function Stop-OneProcess {
    param([Parameter(Mandatory = $true)][int]$ProcessIdToStop)

    if ($ProcessIdToStop -eq $PID) {
        return
    }
    $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessIdToStop"
    if (-not $processInfo) {
        return
    }
    if (-not (
        $processInfo.CommandLine -and
        $processInfo.CommandLine -match $RootPattern -and
        $processInfo.CommandLine -match "wechat-digest|wechat_digest" -and
        $processInfo.CommandLine -match "\srun(?:\s|$)"
    )) {
        return
    }
    Stop-Process -Id $ProcessIdToStop -Force
    $Stopped.Add($ProcessIdToStop) | Out-Null
}

$targetPids = New-Object System.Collections.Generic.HashSet[int]
if (Test-Path -LiteralPath $PidFile) {
    $pidText = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    if ($pidText -match '^\d+$') {
        $targetPids.Add([int]$pidText) | Out-Null
    }
}

$processes = Get-CimInstance Win32_Process | Where-Object {
    $_.CommandLine -and
    $_.ProcessId -ne $PID -and
    $_.CommandLine -match $RootPattern -and
    $_.CommandLine -match "wechat-digest|wechat_digest" -and
    $_.CommandLine -match "\srun(?:\s|$)"
}

foreach ($processInfo in $processes) {
    $targetPids.Add([int]$processInfo.ProcessId) | Out-Null
}

$orderedPids = $targetPids | Sort-Object -Descending
foreach ($processId in $orderedPids) {
    Stop-OneProcess -ProcessIdToStop ([int]$processId)
}

Remove-Item -LiteralPath $PidFile -Force

if ($Stopped.Count -gt 0) {
    Write-Host "Stopped local digest scheduler process(es): $($Stopped -join ', ')"
} else {
    Write-Host "No local digest scheduler process was running."
}
