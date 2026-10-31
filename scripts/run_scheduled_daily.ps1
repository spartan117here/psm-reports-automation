# Windows Task Scheduler Launcher for Daily Reporting Automation Pipeline
# Executes the independent daily reporting pipeline at 11:31 AM IST daily.

$ErrorActionPreference = "Continue"

# Force UTF-8 encoding across streams to prevent wide-character spacing in logs
$OutputEncoding = [System.Text.Encoding]::UTF8
if ([Console]::OutputEncoding) {
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
}

$projectRoot = "c:\Users\spart\OneDrive\Documents\psm-reports-automation-main"
Set-Location -Path $projectRoot

# Ensure logs directory exists
$logDir = Join-Path $projectRoot "logs\scheduled"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logFile = Join-Path $logDir "daily_pipeline_$timestamp.log"
$latestLog = Join-Path $logDir "latest.log"

$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$scriptPath = Join-Path $projectRoot "scripts\run_independent_daily_pipeline.py"

$banner = @"
================================================================================
WINDOWS TASK SCHEDULER: DAILY REPORTING PIPELINE
Timestamp:   $(Get-Date -Format "yyyy-MM-dd HH:mm:ss K")
User:        $($env:USERDOMAIN)\$($env:USERNAME)
Computer:    $($env:COMPUTERNAME)
Directory:   $projectRoot
Python:      $pythonExe
Script:      $scriptPath
Log File:    $logFile
================================================================================
"@

Write-Output $banner
$banner | Out-File -FilePath $logFile -Encoding utf8

if (-not (Test-Path $pythonExe)) {
    $err = "CRITICAL: Python executable not found at $pythonExe"
    Write-Error $err
    $err | Out-File -FilePath $logFile -Append -Encoding utf8
    exit 1
}

if (-not (Test-Path $scriptPath)) {
    $err = "CRITICAL: Script not found at $scriptPath"
    Write-Error $err
    $err | Out-File -FilePath $logFile -Append -Encoding utf8
    exit 1
}

# Forward any arguments from Task Scheduler
$pipelineArgs = $args

# Execute Python pipeline, capturing stdout and stderr cleanly in UTF-8
try {
    $rawOut = & $pythonExe $scriptPath @pipelineArgs 2>&1
    $exitCode = $LASTEXITCODE
    
    foreach ($line in $rawOut) {
        Write-Output $line
        $line | Out-File -FilePath $logFile -Append -Encoding utf8
    }
} catch {
    $err = "EXCEPTION OCCURRED: $_"
    Write-Error $err
    $err | Out-File -FilePath $logFile -Append -Encoding utf8
    $exitCode = 1
}

$footer = @"

================================================================================
SCHEDULED EXECUTION FINISHED
Completion Time: $(Get-Date -Format "yyyy-MM-dd HH:mm:ss K")
Exit Code:       $exitCode
Status:          $($(if ($exitCode -eq 0) { "SUCCESS" } else { "FAILED" }))
================================================================================
"@

Write-Output $footer
$footer | Out-File -FilePath $logFile -Append -Encoding utf8

try {
    Copy-Item -Path $logFile -Destination $latestLog -Force
} catch {
    # Ignore lock
}

exit $exitCode
