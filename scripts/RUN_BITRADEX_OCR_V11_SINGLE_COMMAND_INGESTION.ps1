param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$InputDir = "E:\bitradex\Bitradex prints",
    [string]$PackageDir = "",
    [string]$Report = "",
    [switch]$ApplyImport,
    [switch]$RunPhase5,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
$Cli = Join-Path $ProjectRoot "scripts\run_bitradex_ocr_v11_single_command_ingestion.py"
$CliArgs = @(
    $Cli,
    "--project-root", $ProjectRoot,
    "--input-dir", $InputDir,
    "--timeout-seconds", $TimeoutSeconds,
    "--json"
)

if ($PackageDir) {
    $CliArgs += @("--package-dir", $PackageDir)
}
if ($Report) {
    $CliArgs += @("--report", $Report)
}
if ($ApplyImport) {
    $CliArgs += "--apply-import"
} else {
    $CliArgs += "--dry-run"
}
if ($RunPhase5) {
    $CliArgs += "--run-phase5"
}

& python @CliArgs
exit $LASTEXITCODE
