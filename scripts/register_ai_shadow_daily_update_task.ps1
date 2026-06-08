[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [string]$ProjectRoot = "E:\FUTUROS",
    [string]$TaskName = "SmartCripto_AI_Daily_Update",
    [string]$DailyTime = "00:00",
    [ValidateSet("Limited", "Highest")]
    [string]$RunLevel = "Limited",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

function Convert-ToIsoLocal {
    param([datetime]$Value)
    return $Value.ToString("yyyy-MM-ddTHH:mm:ssK")
}

$ResolvedProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$DailyScript = Join-Path $ResolvedProjectRoot "scripts\RUN_DAILY_AI_SHADOW_UPDATE.ps1"

if (-not (Test-Path -LiteralPath $DailyScript)) {
    throw "Daily AI Shadow update script not found: $DailyScript"
}

$ParsedDailyTime = [datetime]::ParseExact($DailyTime, "HH:mm", [Globalization.CultureInfo]::InvariantCulture)
$Execute = "powershell.exe"
$Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$DailyScript`""
$Trigger = New-ScheduledTaskTrigger -Daily -At $ParsedDailyTime
$Action = New-ScheduledTaskAction -Execute $Execute -Argument $Arguments -WorkingDirectory $ResolvedProjectRoot
$Settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel $RunLevel
$Task = New-ScheduledTask -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal

$Payload = [ordered]@{
    status = if ($DryRun) { "dry_run" } else { "ok" }
    reason = if ($DryRun) { "scheduler_registration_dry_run" } else { "scheduler_registered_or_updated" }
    task_name = $TaskName
    execute = $Execute
    arguments = $Arguments
    working_directory = $ResolvedProjectRoot
    daily_time = $DailyTime
    daily_script = $DailyScript
    run_level = $RunLevel
    paper_only = $true
    shadow_only = $true
    live_trading_enabled = $false
    order_submission_enabled = $false
    real_order_submission_enabled = $false
    sends_orders = $false
    exchange_private_access = $false
    changes_risk = $false
    model_promoted = $false
    generated_at = Convert-ToIsoLocal (Get-Date)
}

if ($DryRun) {
    $Payload | ConvertTo-Json -Depth 8
    return
}

if ($PSCmdlet.ShouldProcess($TaskName, "Register or update Daily AI Shadow scheduler task")) {
    Register-ScheduledTask -TaskName $TaskName -InputObject $Task -Force | Out-Null
    $Payload | ConvertTo-Json -Depth 8
}
