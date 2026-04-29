param(
  [ValidateSet("full", "fast", "api", "security", "release")]
  [string] $Profile = "full"
)

$ErrorActionPreference = "Stop"

$root = (Resolve-Path "$PSScriptRoot\..").Path
$paths = @(
  "$root\apps\local-api",
  "$root\packages\core-types",
  "$root\services\asset-broker",
  "$root\services\brain",
  "$root\services\capability-graph",
  "$root\services\chat-runtime",
  "$root\services\context-gateway",
  "$root\services\heart",
  "$root\services\memory",
  "$root\services\persona-engine",
  "$root\services\response-composer",
  "$root\services\safety",
  "$root\services\shell-runtime",
  "$root\services\skill-engine",
  "$root\services\task-engine",
  "$root\services\tools",
  "$root\services\trace"
)

$env:CYCBER_ROOT = $root
$env:PYTHONPATH = ($paths -join [System.IO.Path]::PathSeparator)
$reportRoot = Join-Path $root "data\check-reports"
New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null
$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$reportPath = Join-Path $reportRoot "check-$runId.json"
$script:checkStarted = (Get-Date).ToUniversalTime()
$script:checkResults = @()
$script:slowDurationLines = @()

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
  $pythonPath = $venvPython
} else {
  $python = Get-Command python -ErrorAction SilentlyContinue
  if ($null -eq $python) {
    $python = Get-Command py -ErrorAction Stop
  }
  $pythonPath = $python.Source
}

function Write-CheckReport {
  param(
    [string] $OverallStatus
  )

  $completed = (Get-Date).ToUniversalTime()
  $report = [ordered]@{
    run_id = $runId
    root = $root
    status = $OverallStatus
    profile = $Profile
    started_at = $script:checkStarted.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $script:checkStarted).TotalSeconds, 3)
    python = $pythonPath
    commands = $script:checkResults
    slow_test_report = @{
      source = "pytest --durations=20"
      lines = $script:slowDurationLines
    }
    command_matrix = @{
      full = '.\scripts\check.ps1 -Profile full'
      fast = '.\scripts\check.ps1 -Profile fast'
      api = '.\scripts\check.ps1 -Profile api'
      security = '.\scripts\check.ps1 -Profile security'
      release = '.\scripts\check.ps1 -Profile release'
      fast_backend = '.venv\Scripts\python.exe -m pytest tests apps\local-api\tests -m "not slow"'
      api_backend = '.venv\Scripts\python.exe -m pytest apps\local-api\tests -m "not slow"'
      eval_security = '.venv\Scripts\python.exe -m pytest tests\evals apps\local-api\tests -m "eval or security"'
      release_scale = '.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase29_release_scale_verification.py'
      release_full = '.\scripts\check.ps1 -Profile full'
    }
  }
  $report | ConvertTo-Json -Depth 12 | Set-Content -Path $reportPath -Encoding UTF8
  Write-Host "Check report: $reportPath"
}

function Invoke-PythonModule {
  param(
    [Parameter(Mandatory = $true)]
    [string] $Name,
    [Parameter(Mandatory = $true)]
    [string[]] $ModuleArgs
  )

  $logPath = Join-Path $reportRoot "$runId-$Name.log"
  $started = (Get-Date).ToUniversalTime()
  & $pythonPath -m @ModuleArgs 2>&1 | Tee-Object -FilePath $logPath
  $exitCode = $LASTEXITCODE
  $completed = (Get-Date).ToUniversalTime()
  $status = if ($exitCode -eq 0) { "passed" } else { "failed" }
  $script:checkResults += [ordered]@{
    name = $Name
    args = $ModuleArgs
    status = $status
    exit_code = $exitCode
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($Name -like "pytest*") {
    $script:slowDurationLines += @(
      Select-String -Path $logPath -Pattern "^\s*\d+(\.\d+)?s\s+" |
        Select-Object -ExpandProperty Line
    )
  }
  if ($exitCode -ne 0) {
    Write-CheckReport -OverallStatus "failed"
    exit $exitCode
  }
}

Invoke-PythonModule -Name "ruff" -ModuleArgs @("ruff", "check", ".")
Invoke-PythonModule -Name "mypy" -ModuleArgs @("mypy", ".")
switch ($Profile) {
  "fast" {
    Invoke-PythonModule -Name "pytest" -ModuleArgs @(
      "pytest",
      "tests",
      "apps\local-api\tests",
      "-m",
      "not slow",
      "--durations=20"
    )
  }
  "api" {
    Invoke-PythonModule -Name "pytest" -ModuleArgs @(
      "pytest",
      "apps\local-api\tests",
      "-m",
      "not slow",
      "--durations=20"
    )
  }
  "security" {
    Invoke-PythonModule -Name "pytest" -ModuleArgs @(
      "pytest",
      "tests\evals",
      "apps\local-api\tests",
      "-m",
      "eval or security",
      "--durations=20"
    )
  }
  "release" {
    Invoke-PythonModule -Name "pytest_phase29" -ModuleArgs @(
      "pytest",
      "apps\local-api\tests\test_phase29_release_scale_verification.py",
      "--durations=20"
    )
    Invoke-PythonModule -Name "pytest_eval_security" -ModuleArgs @(
      "pytest",
      "tests\evals",
      "apps\local-api\tests",
      "-m",
      "eval or security",
      "--durations=20"
    )
    Invoke-PythonModule -Name "pytest_release" -ModuleArgs @(
      "pytest",
      "apps\local-api\tests",
      "-m",
      "release",
      "--durations=20"
    )
  }
  default {
    Invoke-PythonModule -Name "pytest" -ModuleArgs @("pytest", "--durations=20")
  }
}
Write-CheckReport -OverallStatus "passed"
