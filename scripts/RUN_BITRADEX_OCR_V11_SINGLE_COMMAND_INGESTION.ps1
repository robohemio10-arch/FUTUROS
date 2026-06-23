param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")),
    [string]$InputDir = "E:\bitradex\Bitradex prints",
    [string]$PackageDir = "",
    [string]$Report = "",
    [string]$InputImagesManifest = "",
    [switch]$ApplyImport,
    [switch]$RunPhase5,
    [int]$ExpectedImageCount = 50,
    [int]$MaxInputImagesInJson = 20,
    [switch]$AllowImageCountMismatch,
    [int]$TimeoutSeconds = 900
)

$ErrorActionPreference = "Stop"
$Cli = Join-Path $ProjectRoot "scripts\run_bitradex_ocr_v11_single_command_ingestion.py"
$CliArgs = @(
    $Cli,
    "--project-root", $ProjectRoot,
    "--input-dir", $InputDir,
    "--expected-image-count", $ExpectedImageCount,
    "--max-input-images-in-json", $MaxInputImagesInJson,
    "--timeout-seconds", $TimeoutSeconds,
    "--json"
)

if ($PackageDir) {
    $CliArgs += @("--package-dir", $PackageDir)
}
if ($Report) {
    $CliArgs += @("--report", $Report)
}
if ($InputImagesManifest) {
    $CliArgs += @("--input-images-manifest", $InputImagesManifest)
}
if ($ApplyImport) {
    $CliArgs += "--apply-import"
} else {
    $CliArgs += "--dry-run"
}
if ($RunPhase5) {
    $CliArgs += "--run-phase5"
}
if ($AllowImageCountMismatch) {
    $CliArgs += "--allow-image-count-mismatch"
}

& python @CliArgs
exit $LASTEXITCODE
