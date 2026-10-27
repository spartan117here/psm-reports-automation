<#
.SYNOPSIS
    Pothys Swarna Mahal - Daily Reporting Automation Runner
    Designed for Windows Task Scheduler and manual administrative execution.

.DESCRIPTION
    Executes the 4-stage daily reporting automation pipeline:
    1. SS & SV Enrollment Data Download & Reconciliation
    2. DigiGold & DigiSilver Synchronization
    3. Scheme Closing Report Synchronization
    4. Verified Executive Screenshot Generation

.PARAMETER Mode
    Execution mode: 'dry-run' (default), 'mock', or 'live'.
    NOTE: 'live' mode is blocked if STRICT_READ_ONLY is active.

.PARAMETER ReportDate
    Target report date in YYYY-MM-DD format (defaults to yesterday).

.EXAMPLE
    powershell.exe -ExecutionPolicy Bypass -File .\scripts\run_daily_report.ps1 -Mode dry-run
#>

[CmdletBinding()]
param (
    [ValidateSet("dry-run", "mock", "live")]
    [string]$Mode = "dry-run",

    [ValidateSet("1", "2", "3", "2-3", "all")]
    [string]$Phase = "all",

    [string]$ReportDate = "",

    [string]$OutputDir = ""
)

$ErrorActionPreference = "Stop"

# 1. Resolve Project Root and Paths
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -Path $ProjectRoot

$LogsDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path $LogsDir)) {
    New-Item -ItemType Directory -Path $LogsDir | Out-Null
}

$Timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$LogFile = Join-Path $LogsDir "scheduler_$Timestamp.log"

function Write-Log {
    param ([string]$Message, [string]$Level = "INFO")
    $LogEntry = "[$((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))] [$Level] $Message"
    Write-Host $LogEntry
    Add-Content -Path $LogFile -Value $LogEntry
}

Write-Log "================================================================="
Write-Log "POTHYS SWARNA MAHAL DAILY REPORTING AUTOMATION RUNNER"
Write-Log "================================================================="
Write-Log "Working Directory: $ProjectRoot"
Write-Log "Log File:          $LogFile"
Write-Log "Execution Mode:    $($Mode.ToUpper())"
Write-Log "Pipeline Phase:    $($Phase.ToUpper())"

# 2. Locate Python Executable
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $VenvPython) {
    $PythonExe = $VenvPython
} else {
    $PythonExe = (Get-Command "python" -ErrorAction SilentlyContinue).Source
}
if (-not $PythonExe) {
    Write-Log "CRITICAL: Python executable not found in PATH or .venv." "ERROR"
    exit 1
}
Write-Log "Python Executable: $PythonExe"

# 3. Check Safety Guards for Production Writes
$StrictReadOnly = [Environment]::GetEnvironmentVariable("STRICT_READ_ONLY")
if ($StrictReadOnly -eq $null -or $StrictReadOnly -eq "") {
    # Check .env file if present (root or scripts/ directory)
    $EnvPath = Join-Path $ProjectRoot ".env"
    if (-not (Test-Path $EnvPath)) {
        $EnvPath = Join-Path $PSScriptRoot ".env"
    }
    if (Test-Path $EnvPath) {
        $EnvContent = Get-Content $EnvPath
        foreach ($Line in $EnvContent) {
            if ($Line -match "^\s*STRICT_READ_ONLY\s*=\s*(.+)$") {
                $StrictReadOnly = $Matches[1].Trim()
                break
            }
        }
    }
}

if ($Mode -eq "live") {
    Write-Log "CRITICAL BLOCK: Live production execution is permanently blocked by policy." "ERROR"
    Write-Log "Live mutations are prohibited across all scheduled tasks and script runners." "ERROR"
    Write-Log "All executions must use -Mode dry-run or -Mode mock." "ERROR"
    exit 1
}

# 4. Construct Command Arguments
$CliArgs = @("-m", "app", "run-daily", "--phase", $Phase)

if ($Mode -eq "dry-run") {
    $CliArgs += "--dry-run"
} elseif ($Mode -eq "mock") {
    $CliArgs += "--mock"
}

if ($ReportDate -ne "") {
    $CliArgs += @("--report-date", $ReportDate)
}

if ($OutputDir -ne "") {
    $CliArgs += @("--output-dir", $OutputDir)
}

Write-Log "Executing: python $($CliArgs -join ' ')"

# 5. Execute Pipeline and Capture Exit Code
try {
    $prevEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & $PythonExe $CliArgs 2>&1 | Tee-Object -FilePath $LogFile -Append
    $ExitCode = $LASTEXITCODE
    $ErrorActionPreference = $prevEAP
} catch {
    Write-Log "Exception occurred during pipeline execution: $_" "ERROR"
    $ExitCode = 1
}

Write-Log "Pipeline exited with code: $ExitCode"
if ($ExitCode -eq 0) {
    Write-Log "DAILY REPORTING AUTOMATION PIPELINE COMPLETED SUCCESSFULLY."
} else {
    Write-Log "DAILY REPORTING AUTOMATION PIPELINE FAILED (FAIL-CLOSED SAFEGUARD ACTIVE)." "ERROR"
}

exit $ExitCode
