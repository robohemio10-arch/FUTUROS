param(
    [switch]$OpenDashboard,
    [int]$SignalRefreshSeconds = 1500,
    [int]$SignalValidityMinutes = 45,
    [int]$FreqtradeProcessingWaitSeconds = 90,
    [int]$FeedbackWaitSeconds = 120,
    [switch]$SkipFeedback
)

$scriptDir = Split-Path -Parent $PSCommandPath
$start24 = Join-Path $scriptDir "START_PAPER_24H.ps1"

& $start24 `
    -SessionHours 168 `
    -SignalRefreshSeconds $SignalRefreshSeconds `
    -SignalValidityMinutes $SignalValidityMinutes `
    -FreqtradeProcessingWaitSeconds $FreqtradeProcessingWaitSeconds `
    -FeedbackWaitSeconds $FeedbackWaitSeconds `
    -OpenDashboard:$OpenDashboard `
    -SkipFeedback:$SkipFeedback
