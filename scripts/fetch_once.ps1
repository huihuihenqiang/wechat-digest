$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Config = Join-Path $Root "config.yaml"
$EnvFile = Join-Path $Root ".env"
$Cli = Join-Path $Root ".venv\Scripts\wechat-digest.exe"
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$DefaultRecipient = -join ([char[]](0x6587, 0x4ef6, 0x4f20, 0x8f93, 0x52a9, 0x624b))

function Read-WithDefault {
    param(
        [Parameter(Mandatory = $true)][string]$Prompt,
        [Parameter(Mandatory = $true)][string]$Default
    )
    $value = Read-Host "$Prompt [$Default]"
    if ([string]::IsNullOrWhiteSpace($value)) {
        return $Default
    }
    return $value.Trim()
}

$Group = Read-Host "Group chat name"
if ([string]::IsNullOrWhiteSpace($Group)) {
    throw "Group chat name cannot be empty."
}
$Group = $Group.Trim()

$DateText = Read-WithDefault -Prompt "Date: today / yesterday / YYYY-MM-DD" -Default "today"
$MaxScrolls = Read-WithDefault -Prompt "Max scroll rounds" -Default "200"
$Delivery = Read-WithDefault -Prompt "Delivery: openclaw / wechat / print" -Default "openclaw"

if (-not ($MaxScrolls -match '^\d+$')) {
    throw "Max scroll rounds must be a non-negative integer."
}
if ($Delivery -notin @("openclaw", "wechat", "print")) {
    throw "Delivery must be openclaw, wechat, or print."
}

$Recipient = ""
if ($Delivery -eq "wechat") {
    $Recipient = Read-WithDefault -Prompt "Send to" -Default $DefaultRecipient
}

$ArgsList = @(
    "--config", $Config,
    "--env", $EnvFile,
    "fetch-once",
    "--group", $Group,
    "--date", $DateText,
    "--max-scrolls", $MaxScrolls,
    "--delivery", $Delivery
)
if ($Delivery -eq "wechat") {
    $ArgsList += @("--recipient", $Recipient)
}

if (Test-Path -LiteralPath $Cli) {
    & $Cli @ArgsList
} elseif (Test-Path -LiteralPath $Python) {
    & $Python -m wechat_digest @ArgsList
} else {
    throw "Could not find wechat-digest executable or venv Python under $Root"
}
