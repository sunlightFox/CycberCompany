param(
  [ValidateSet("full", "smoke", "fast", "api", "security", "release")]
  [string] $Profile = "full"
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUTF8 = "1"

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
$gateSignalConfigPath = if ($env:CYCBER_GATE_SIGNAL_CONFIG) {
  $env:CYCBER_GATE_SIGNAL_CONFIG
} else {
  Join-Path $root "config\gate_signal_plane.json"
}

$reportRoot = Join-Path $root "data\check-reports"
New-Item -ItemType Directory -Force -Path $reportRoot | Out-Null

$runId = (Get-Date).ToUniversalTime().ToString("yyyyMMddTHHmmssZ")
$reportPath = Join-Path $reportRoot "check-$runId.json"
$script:checkStarted = (Get-Date).ToUniversalTime()
$script:checkResults = @()
$script:slowDurationLines = @()
$script:reportWritten = $false
$script:failureContext = $null

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

function New-SmokePytestArgs {
  $profile = Get-GateSignalProfile -Profile "smoke"
  $paths = @($profile.signal_suites | ForEach-Object { $_.path })
  return @("pytest") + $paths + @("--durations=20")
}

function Get-GateSignalPlaneConfig {
  if (-not (Test-Path $gateSignalConfigPath)) {
    throw "Gate signal plane config missing: $gateSignalConfigPath"
  }
  return Get-Content -Path $gateSignalConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
}

function Get-GateSignalProfile {
  param(
    [Parameter(Mandatory = $true)]
    [string] $Profile
  )

  $config = Get-GateSignalPlaneConfig
  $profiles = $config.profiles
  $entry = $null
  if ($null -ne $profiles) {
    $entry = $profiles.$Profile
  }
  if ($null -eq $entry) {
    throw "Gate signal plane profile not found: $Profile"
  }
  if ($null -eq $entry.signal_suites) {
    $entry.signal_suites = @()
  }
  return $entry
}

function Get-ProfileSignalSuites {
  param(
    [Parameter(Mandatory = $true)]
    [string] $Profile
  )

  if ($Profile -ne "smoke") {
    return @()
  }
  $entry = Get-GateSignalProfile -Profile $Profile
  return @($entry.signal_suites)
}

function Invoke-StaticChecks {
  Invoke-PythonModule -Name "ruff" -ModuleArgs @("ruff", "check", ".")
  Invoke-PythonModule -Name "mypy" -ModuleArgs @("mypy", ".")
}

function Resolve-ChatDocsDir {
  param(
    [Parameter(Mandatory = $true)]
    [string] $Suffix
  )

  $match = Get-ChildItem -Path (Join-Path $root "docs") -Directory -Recurse |
    Where-Object { $_.Name -eq $Suffix } |
    Select-Object -First 1
  if ($null -eq $match) {
    throw "Unable to resolve chat docs directory suffix: $Suffix"
  }
  return $match.FullName
}

function Resolve-ChatDocFile {
  param(
    [Parameter(Mandatory = $true)]
    [string] $DirSuffix,
    [Parameter(Mandatory = $true)]
    [string] $Pattern
  )

  $dir = Resolve-ChatDocsDir -Suffix $DirSuffix
  $match = Get-ChildItem -Path $dir -File |
    Where-Object { $_.Name -like $Pattern } |
    Select-Object -First 1
  if ($null -eq $match) {
    throw "Unable to resolve chat doc file in $DirSuffix matching $Pattern"
  }
  return $match.FullName
}

function New-CommandMatrix {
  $smokePaths = @((Get-GateSignalProfile -Profile "smoke").signal_suites | ForEach-Object { $_.path })
  $smokeBackend = ".venv\Scripts\python.exe -m pytest " + ($smokePaths -join " ") + " --durations=20"
  return [ordered]@{
    full = '.\scripts\check.ps1 -Profile full'
    smoke = '.\scripts\check.ps1 -Profile smoke'
    fast = '.\scripts\check.ps1 -Profile fast'
    api = '.\scripts\check.ps1 -Profile api'
    security = '.\scripts\check.ps1 -Profile security'
    release = '.\scripts\check.ps1 -Profile release'
    smoke_backend = $smokeBackend
    fast_backend = '.venv\Scripts\python.exe -m pytest tests apps\local-api\tests -m "not slow"'
    api_backend = '.venv\Scripts\python.exe -m pytest apps\local-api\tests -m "not slow"'
    eval_security = '.venv\Scripts\python.exe -m pytest tests\evals apps\local-api\tests -m "eval or security"'
    release_scale = '.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase29_release_scale_verification.py'
    release_real_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\chat-main-chain\2026-04-29\run_chat_main_chain*_cases.py'
    release_power_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\chat-main-chain\2026-04-30\run_chat_main_chain_power_cases.py'
    release_natural_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\chat-main-chain\2026-04-30\run_chat_natural_interaction_benchmark.py'
    release_quality_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\chat-main-chain\2026-04-30-quality\run_chat_main_chain_quality_cases.py'
    release_quality_chat_e2e_v2 = '.\scripts\check.ps1 -Profile release runs docs\chat-main-chain\2026-05-01-quality\run_chat_main_chain_quality_regression_cases.py'
    release_wechat_50_quality_e2e = '.\scripts\check.ps1 -Profile release runs docs\chat-main-chain\2026-05-03-wechat-50-scenarios\run_wechat_50_quality_latency.py --api http://127.0.0.1:8765 --output data\check-reports\wechat-50-quality'
    release_wechat_real_quality_e2e = '.\scripts\check.ps1 -Profile release runs docs\chat-main-chain\2026-05-03-wechat-real-scenarios\run_wechat_real_scenarios.py --api http://127.0.0.1:8765 --output data\check-reports\wechat-real-quality'
    release_full = '.\scripts\check.ps1 -Profile full'
  }
}

function Get-PytestSlowDurationLines {
  param(
    [Parameter(Mandatory = $true)]
    [string] $LogPath
  )

  if (-not (Test-Path $LogPath)) {
    return @()
  }

  try {
    return @(
      Get-Content -Path $LogPath -Encoding UTF8 -ErrorAction Stop |
        Where-Object { $_ -match "^\s*\d+(\.\d+)?s\s+" }
    )
  } catch {
    return @()
  }
}

function Get-MaturityDashboardSummary {
  param(
    [Parameter(Mandatory = $true)]
    [string] $OverallStatus,
    [Parameter(Mandatory = $true)]
    [object[]] $SignalSuites
  )

  $blockers = @()
  if ($Profile -eq "smoke") {
    if ($SignalSuites.Count -eq 0) {
      $blockers += [ordered]@{
        blocker_code = "phase105_smoke_signal_suites_missing"
        category = "governance_gap"
        severity = "P0"
        source_phase = "phase105_gate_signal_plane_governance"
        dimension = "quality"
        next_owner = "scripts/check.ps1"
        evidence_ref = [ordered]@{ phase = "phase105_gate_signal_plane_governance" }
        recommended_next_step = "reconcile gate_signal_plane.json and restore the smoke signal suite inventory"
      }
    }
    if ($OverallStatus -ne "passed") {
      $blockers += [ordered]@{
        blocker_code = "phase113_latest_smoke_report_not_passed"
        category = "runtime_fix"
        severity = "P1"
        source_phase = "phase113_check_matrix_execution_restored"
        dimension = "quality"
        next_owner = "scripts/check.ps1"
        evidence_ref = [ordered]@{
          phase = "phase113_check_matrix_execution_restored"
          profile = "smoke"
        }
        recommended_next_step = "re-run .\scripts\check.ps1 -Profile smoke and inspect the failing smoke command log"
      }
    }
  }

  $priorityQueue = @(
    $blockers |
      Sort-Object @{ Expression = { if ($_.severity -eq "P0") { 0 } elseif ($_.severity -eq "P1") { 1 } else { 2 } } }, dimension, blocker_code
  )
  $p0Blockers = @($priorityQueue | Where-Object { $_.severity -eq "P0" })
  return [ordered]@{
    phase116_contract_version = "phase116.maturity_dashboard.v1"
    dashboard_status = if ($p0Blockers.Count -gt 0) { "partial" } elseif ($priorityQueue.Count -gt 0) { "partial" } else { "ready" }
    top_blockers = @($priorityQueue | Select-Object -First 5)
    priority_queue_preview = @($priorityQueue | Select-Object -First 8)
    release_readiness = [ordered]@{
      status = if ($p0Blockers.Count -gt 0) { "no_go" } elseif ($priorityQueue.Count -gt 0) { "go_with_findings" } else { "ready" }
      p0_blocker_count = $p0Blockers.Count
    }
  }
}

function Write-CheckReport {
  param(
    [Parameter(Mandatory = $true)]
    [string] $OverallStatus
  )

  if ($script:reportWritten) {
    return
  }

  $completed = (Get-Date).ToUniversalTime()
  $signalSuites = @(
    Get-ProfileSignalSuites -Profile $Profile | ForEach-Object {
      [ordered]@{
        suite_key = [string]$_.suite_key
        path = [string]$_.path
        kind = [string]$_.kind
        phase_key = if ($null -ne $_.phase_key) { [string]$_.phase_key } else { $null }
      }
    }
  )
  $checkContractVersion = (Get-GateSignalPlaneConfig).check_contract_version
  $commands = @(
    $script:checkResults | ForEach-Object {
      [ordered]@{
        name = [string]$_.name
        args = @($_.args | ForEach-Object { [string]$_ })
        status = [string]$_.status
        exit_code = [int]$_.exit_code
        started_at = [string]$_.started_at
        completed_at = [string]$_.completed_at
        duration_seconds = [double]$_.duration_seconds
        log_path = [string]$_.log_path
      }
    }
  )
  $commandMatrix = [ordered]@{}
  foreach ($entry in (New-CommandMatrix).GetEnumerator()) {
    $commandMatrix[[string]$entry.Key] = [string]$entry.Value
  }
  $maturityDashboardSummary = Get-MaturityDashboardSummary -OverallStatus $OverallStatus -SignalSuites $signalSuites
  $report = [ordered]@{
    run_id = $runId
    root = $root
    status = $OverallStatus
    profile = $Profile
    check_contract_version = $checkContractVersion
    started_at = $script:checkStarted.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $script:checkStarted).TotalSeconds, 3)
    python = $pythonPath
    signal_suites = $signalSuites
    commands = $commands
    slow_test_report = [ordered]@{
      source = "pytest --durations=20"
      lines = @($script:slowDurationLines | ForEach-Object { [string]$_ })
    }
    command_matrix = $commandMatrix
    maturity_dashboard_summary = $maturityDashboardSummary
  }
  if ($null -ne $script:failureContext) {
    $failureContext = [ordered]@{}
    foreach ($entry in $script:failureContext.GetEnumerator()) {
      $failureContext[[string]$entry.Key] = if ($null -eq $entry.Value) {
        $null
      } else {
        [string]$entry.Value
      }
    }
    $report.failure_context = $failureContext
  }
  $report | ConvertTo-Json -Depth 12 | Set-Content -Path $reportPath -Encoding UTF8
  $script:reportWritten = $true
  Write-Host "Check report: $reportPath"
}

function Add-CheckResult {
  param(
    [Parameter(Mandatory = $true)]
    [string] $Name,
    [Parameter(Mandatory = $true)]
    [object[]] $Args,
    [Parameter(Mandatory = $true)]
    [int] $ExitCode,
    [Parameter(Mandatory = $true)]
    [datetime] $Started,
    [Parameter(Mandatory = $true)]
    [datetime] $Completed,
    [Parameter(Mandatory = $true)]
    [string] $LogPath
  )

  $script:checkResults += [ordered]@{
    name = $Name
    args = $Args
    status = if ($ExitCode -eq 0) { "passed" } else { "failed" }
    exit_code = $ExitCode
    started_at = $Started.ToString("o")
    completed_at = $Completed.ToString("o")
    duration_seconds = [Math]::Round(($Completed - $Started).TotalSeconds, 3)
    log_path = $LogPath
  }
}

function Complete-OrFail {
  param(
    [Parameter(Mandatory = $true)]
    [int] $ExitCode
  )

  if ($ExitCode -ne 0) {
    $lastFailure = $script:checkResults | Select-Object -Last 1
    $script:failureContext = [ordered]@{
      kind = "command_failure"
      command_name = if ($null -ne $lastFailure) { $lastFailure.name } else { $null }
      exit_code = $ExitCode
      report_profile = $Profile
    }
    Write-CheckReport -OverallStatus "failed"
    exit $ExitCode
  }
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

  Add-CheckResult -Name $Name -Args $ModuleArgs -ExitCode $exitCode -Started $started -Completed $completed -LogPath $logPath

  if ($Name -like "pytest*") {
    $script:slowDurationLines += @(Get-PytestSlowDurationLines -LogPath $logPath)
  }

  Complete-OrFail -ExitCode $exitCode
}

function Invoke-SmokeProfileSuite {
  $previous = $env:CYCBER_RUNNING_CHECK_SMOKE
  $hadPrevious = Test-Path Env:CYCBER_RUNNING_CHECK_SMOKE
  $env:CYCBER_RUNNING_CHECK_SMOKE = "1"
  try {
    Invoke-PythonModule -Name "pytest_smoke" -ModuleArgs (New-SmokePytestArgs)
  } finally {
    if ($hadPrevious) {
      $env:CYCBER_RUNNING_CHECK_SMOKE = $previous
    } else {
      Remove-Item Env:CYCBER_RUNNING_CHECK_SMOKE -ErrorAction SilentlyContinue
    }
  }
}

function Invoke-PythonScript {
  param(
    [Parameter(Mandatory = $true)]
    [string] $Name,
    [Parameter(Mandatory = $true)]
    [string] $ScriptPath,
    [string[]] $Arguments = @()
  )

  $logPath = Join-Path $reportRoot "$runId-$Name.log"
  $started = (Get-Date).ToUniversalTime()
  & $pythonPath $ScriptPath @Arguments 2>&1 | Tee-Object -FilePath $logPath
  $exitCode = $LASTEXITCODE
  $completed = (Get-Date).ToUniversalTime()

  Add-CheckResult -Name $Name -Args @($ScriptPath) + $Arguments -ExitCode $exitCode -Started $started -Completed $completed -LogPath $logPath
  Complete-OrFail -ExitCode $exitCode
}

function Invoke-IssueRegexGate {
  param(
    [Parameter(Mandatory = $true)]
    [string] $Name,
    [Parameter(Mandatory = $true)]
    [string] $FilePath,
    [Parameter(Mandatory = $true)]
    [string] $Pattern,
    [Parameter(Mandatory = $true)]
    [string] $ReasonCode
  )

  $logPath = Join-Path $reportRoot "$runId-$Name.log"
  $started = (Get-Date).ToUniversalTime()
  $openIssues = @()

  if (-not (Test-Path $FilePath)) {
    $openIssues += [ordered]@{ file = $FilePath; count = 1; reason = "missing_issue_file" }
  } else {
    $matches = Select-String -Path $FilePath -Pattern $Pattern -AllMatches
    $count = @($matches).Count
    if ($count -gt 0) {
      $openIssues += [ordered]@{ file = $FilePath; count = $count; reason = $ReasonCode }
    }
  }

  $completed = (Get-Date).ToUniversalTime()
  $exitCode = if ($openIssues.Count -eq 0) { 0 } else { 1 }
  ([ordered]@{
    status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    checked_files = @($FilePath)
    open_issues = $openIssues
  } | ConvertTo-Json -Depth 8) | Set-Content -Path $logPath -Encoding UTF8

  Add-CheckResult -Name $Name -Args @($FilePath) -ExitCode $exitCode -Started $started -Completed $completed -LogPath $logPath

  if ($exitCode -ne 0) {
    $script:failureContext = [ordered]@{
      kind = "issue_gate_failure"
      command_name = $Name
      reason_code = $ReasonCode
      report_profile = $Profile
    }
    Write-Host "$Name failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit $exitCode
  }
}

function Invoke-ChatMainChainIssueGate {
  $logPath = Join-Path $reportRoot "$runId-chat_e2e_issue_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $issueFiles = @(
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "05-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "08-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "11-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "15-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "18-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "21-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "24-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "27-*.md"),
    (Resolve-ChatDocFile -DirSuffix "2026-04-29" -Pattern "30-*.md")
  )
  $openIssues = @()

  foreach ($filePath in $issueFiles) {
    if (-not (Test-Path $filePath)) {
      $openIssues += [ordered]@{ file = $filePath; count = 1; reason = "missing_issue_file" }
      continue
    }
    $matches = Select-String -Path $filePath -Pattern "^##\s+CHAT-E2E-[A-Z0-9-]+" -AllMatches
    $count = @($matches).Count
    if ($count -gt 0) {
      $openIssues += [ordered]@{ file = $filePath; count = $count; reason = "open_issue_records" }
    }
  }

  $completed = (Get-Date).ToUniversalTime()
  $exitCode = if ($openIssues.Count -eq 0) { 0 } else { 1 }
  ([ordered]@{
    status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    checked_files = $issueFiles
    open_issues = $openIssues
  } | ConvertTo-Json -Depth 8) | Set-Content -Path $logPath -Encoding UTF8

  Add-CheckResult -Name "chat_e2e_issue_gate" -Args $issueFiles -ExitCode $exitCode -Started $started -Completed $completed -LogPath $logPath
  if ($exitCode -ne 0) {
    $script:failureContext = [ordered]@{
      kind = "issue_gate_failure"
      command_name = "chat_e2e_issue_gate"
      reason_code = "open_issue_records"
      report_profile = $Profile
    }
    Write-Host "Chat E2E issue gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit $exitCode
  }
}

function Invoke-PowerChatIssueGate {
  Invoke-IssueRegexGate `
    -Name "chat_e2e_power_issue_gate" `
    -FilePath (Resolve-ChatDocFile -DirSuffix "2026-04-30" -Pattern "08-*.md") `
    -Pattern "^##\s+CHAT-E2E-POWER-FIX" `
    -ReasonCode "open_power_issue_records"
}

function Invoke-NaturalChatIssueGate {
  $name = "chat_e2e_natural_issue_gate"
  # 11-自然聊天待优化结论.md
  $filePath = Resolve-ChatDocFile -DirSuffix "2026-04-30" -Pattern "11-*.md"
  $logPath = Join-Path $reportRoot "$runId-$name.log"
  $started = (Get-Date).ToUniversalTime()
  $openIssues = @()

  if (-not (Test-Path $filePath)) {
    $openIssues += [ordered]@{ file = $filePath; count = 1; reason = "missing_conclusion_file" }
  } else {
    $content = Get-Content -Path $filePath -Raw -Encoding UTF8
    if ($content -notmatch "PASS 12 / FAIL 0 / BLOCKED 0") {
      $openIssues += [ordered]@{ file = $filePath; count = 1; reason = "natural_runner_not_all_pass" }
    }
    $matches = Select-String -Path $filePath -Pattern '^\-\s+`NAT-' -AllMatches
    if (@($matches).Count -gt 0) {
      $openIssues += [ordered]@{ file = $filePath; count = @($matches).Count; reason = "open_natural_findings" }
    }
  }

  $completed = (Get-Date).ToUniversalTime()
  $exitCode = if ($openIssues.Count -eq 0) { 0 } else { 1 }
  ([ordered]@{
    status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    checked_files = @($filePath)
    open_issues = $openIssues
  } | ConvertTo-Json -Depth 8) | Set-Content -Path $logPath -Encoding UTF8

  Add-CheckResult -Name $name -Args @($filePath) -ExitCode $exitCode -Started $started -Completed $completed -LogPath $logPath
  if ($exitCode -ne 0) {
    $script:failureContext = [ordered]@{
      kind = "issue_gate_failure"
      command_name = $name
      reason_code = "natural_runner_not_all_pass"
      report_profile = $Profile
    }
    Write-Host "Natural chat E2E issue gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit $exitCode
  }
}

function Invoke-QualityChatIssueGate {
  Invoke-IssueRegexGate `
    -Name "chat_e2e_quality_issue_gate" `
    -FilePath (Resolve-ChatDocFile -DirSuffix "2026-04-30-quality" -Pattern "08-*.md") `
    -Pattern "^##\s+CHAT-E2E-QUALITY-FIX" `
    -ReasonCode "open_quality_issue_records"
}

function Invoke-Phase68PromptResidualGate {
  $logPath = Join-Path $reportRoot "$runId-phase68_prompt_residual_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $targets = @(
    (Join-Path $root "apps\local-api\app"),
    (Join-Path $root "services")
  )
  $patterns = @(
    "openclaw_hermes.v3",
    ([string][char[]](0x597D,0x7684,0xFF0C,0x6211,0x6765)),
    ([string][char[]](0x6211,0x6765,0x7EE7,0x7EED)),
    ([string][char[]](0x8BB0,0x4F4F,0x4E86,0x3002)),
    ([string][char[]](0x5904,0x7406,0x7ED3,0x679C,0x5982,0x4E0B)),
    ([string][char[]](0x4F5C,0x4E3A,0x20,0x41,0x49))
  )
  $hits = @()

  foreach ($target in $targets) {
    if (-not (Test-Path $target)) {
      continue
    }
    $matches = Get-ChildItem -Path $target -Recurse -File -Include *.py |
      Select-String -Pattern $patterns -SimpleMatch
    foreach ($match in $matches) {
      $hits += [ordered]@{
        path = $match.Path
        line = $match.LineNumber
        text = $match.Line.Trim()
      }
    }
  }

  $completed = (Get-Date).ToUniversalTime()
  $exitCode = if ($hits.Count -eq 0) { 0 } else { 1 }
  ([ordered]@{
    status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    hit_count = $hits.Count
    hits = $hits
  } | ConvertTo-Json -Depth 8) | Set-Content -Path $logPath -Encoding UTF8

  Add-CheckResult -Name "phase68_prompt_residual_gate" -Args $patterns -ExitCode $exitCode -Started $started -Completed $completed -LogPath $logPath
  if ($exitCode -ne 0) {
    $script:failureContext = [ordered]@{
      kind = "content_gate_failure"
      command_name = "phase68_prompt_residual_gate"
      reason_code = "phase68_prompt_residual_detected"
      report_profile = $Profile
    }
    Write-Host "Phase68 prompt residual gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit $exitCode
  }
}

function Invoke-Phase68VisibleLeakageGate {
  $logPath = Join-Path $reportRoot "$runId-phase68_visible_leakage_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $targets = @()
  $targets += @(Get-ChildItem -Path (Join-Path $reportRoot "wechat-50-quality-*") -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "02-summary.json" })
  $targets += @(Get-ChildItem -Path (Join-Path $reportRoot "wechat-real-quality-*") -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "02-summary.json" })
  $targets = @($targets | Select-Object -Unique)
  $hits = @()

  foreach ($target in $targets) {
    if (-not (Test-Path $target)) {
      continue
    }
    $json = Get-Content -Path $target -Raw -Encoding UTF8 | ConvertFrom-Json
    $visibleLeakageCount = 0
    if ($null -ne $json.quality -and $null -ne $json.quality.with_internal_visible_terms) {
      $visibleLeakageCount = [int]$json.quality.with_internal_visible_terms
    }
    if ($visibleLeakageCount -gt 0) {
      $hits += [ordered]@{
        path = $target
        visible_leakage_count = $visibleLeakageCount
      }
    }
  }

  $completed = (Get-Date).ToUniversalTime()
  $exitCode = if ($hits.Count -eq 0) { 0 } else { 1 }
  ([ordered]@{
    status = if ($exitCode -eq 0) { "passed" } else { "failed" }
    scanned_targets = $targets
    hit_count = $hits.Count
    hits = $hits
  } | ConvertTo-Json -Depth 8) | Set-Content -Path $logPath -Encoding UTF8

  Add-CheckResult -Name "phase68_visible_leakage_gate" -Args $targets -ExitCode $exitCode -Started $started -Completed $completed -LogPath $logPath
  if ($exitCode -ne 0) {
    $script:failureContext = [ordered]@{
      kind = "content_gate_failure"
      command_name = "phase68_visible_leakage_gate"
      reason_code = "phase68_visible_leakage_detected"
      report_profile = $Profile
    }
    Write-Host "Phase68 visible leakage gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit $exitCode
  }
}

try {
  switch ($Profile) {
    "smoke" {
      Invoke-SmokeProfileSuite
    }
    "fast" {
      Invoke-PythonModule -Name "pytest_fast" -ModuleArgs @(
        "pytest",
        "tests",
        "apps\local-api\tests",
        "-m",
        "not slow",
        "--durations=20"
      )
    }
    "api" {
      Invoke-PythonModule -Name "pytest_api" -ModuleArgs @(
        "pytest",
        "apps\local-api\tests",
        "-m",
        "not slow",
        "--durations=20"
      )
    }
    "security" {
      Invoke-PythonModule -Name "pytest_security" -ModuleArgs @(
        "pytest",
        "tests\evals",
        "apps\local-api\tests",
        "-m",
        "eval or security",
        "--durations=20"
      )
    }
    "release" {
      Invoke-StaticChecks
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

      $chatRunnerRoot = Resolve-ChatDocsDir -Suffix "2026-04-29"
      Invoke-PythonScript -Name "chat_e2e_base" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_cases.py")
      Invoke-PythonScript -Name "chat_e2e_extra" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_extra_cases.py")
      Invoke-PythonScript -Name "chat_e2e_deep" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_deep_cases.py")
      Invoke-PythonScript -Name "chat_e2e_stability" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_stability_cases.py")
      Invoke-PythonScript -Name "chat_e2e_recovery" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_recovery_cases.py")
      Invoke-PythonScript -Name "chat_e2e_knowledge" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_knowledge_cases.py")
      Invoke-PythonScript -Name "chat_e2e_multidimension" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_multidimension_cases.py")
      Invoke-PythonScript -Name "chat_e2e_task_execution" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_task_execution_cases.py")
      Invoke-PythonScript -Name "chat_e2e_browser_scenario" -ScriptPath (Join-Path $chatRunnerRoot "run_chat_main_chain_browser_scenario_cases.py")
      Invoke-ChatMainChainIssueGate

      $powerRunnerRoot = Resolve-ChatDocsDir -Suffix "2026-04-30"
      Invoke-PythonScript -Name "chat_e2e_power" -ScriptPath (Join-Path $powerRunnerRoot "run_chat_main_chain_power_cases.py")
      Invoke-PowerChatIssueGate
      Invoke-PythonScript -Name "chat_e2e_natural" -ScriptPath (Join-Path $powerRunnerRoot "run_chat_natural_interaction_benchmark.py")
      Invoke-NaturalChatIssueGate

      $qualityRunnerRoot = Resolve-ChatDocsDir -Suffix "2026-04-30-quality"
      Invoke-PythonScript -Name "chat_e2e_quality" -ScriptPath (Join-Path $qualityRunnerRoot "run_chat_main_chain_quality_cases.py")
      Invoke-QualityChatIssueGate

      $qualityRunnerRootV2 = Resolve-ChatDocsDir -Suffix "2026-05-01-quality"
      Invoke-PythonScript -Name "chat_e2e_quality_v2" -ScriptPath (Join-Path $qualityRunnerRootV2 "run_chat_main_chain_quality_regression_cases.py")

      $wechat50Root = Resolve-ChatDocsDir -Suffix "2026-05-03-wechat-50-scenarios"
      Invoke-PythonScript -Name "chat_e2e_wechat_50_quality" -ScriptPath (Join-Path $wechat50Root "run_wechat_50_quality_latency.py") -Arguments @(
        "--api",
        "http://127.0.0.1:8765",
        "--output",
        (Join-Path $reportRoot "wechat-50-quality-$runId")
      )

      $wechatRealRoot = Resolve-ChatDocsDir -Suffix "2026-05-03-wechat-real-scenarios"
      Invoke-PythonScript -Name "chat_e2e_wechat_real_quality" -ScriptPath (Join-Path $wechatRealRoot "run_wechat_real_scenarios.py") -Arguments @(
        "--api",
        "http://127.0.0.1:8765",
        "--output",
        (Join-Path $reportRoot "wechat-real-quality-$runId")
      )

      Invoke-Phase68PromptResidualGate
      Invoke-Phase68VisibleLeakageGate
    }
    default {
      Invoke-StaticChecks
      Invoke-PythonModule -Name "pytest_full" -ModuleArgs @("pytest", "--durations=20")
    }
  }

  Write-CheckReport -OverallStatus "passed"
} catch {
  if ($null -eq $script:failureContext) {
    $script:failureContext = [ordered]@{
      kind = "script_exception"
      exception_type = $_.Exception.GetType().FullName
      message = $_.Exception.Message
      command_name = $null
      report_profile = $Profile
    }
  }
  Write-CheckReport -OverallStatus "failed"
  throw
}
