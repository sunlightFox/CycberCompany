param(
  [ValidateSet("full", "smoke", "fast", "api", "security", "release")]
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
      smoke = '.\scripts\check.ps1 -Profile smoke'
      fast = '.\scripts\check.ps1 -Profile fast'
      api = '.\scripts\check.ps1 -Profile api'
      security = '.\scripts\check.ps1 -Profile security'
      release = '.\scripts\check.ps1 -Profile release'
      smoke_backend = '.venv\Scripts\python.exe -m pytest tests\test_response_composer_reasoning.py tests\test_phase2_routing_safety.py tests\test_phase32_cli_client.py tests\test_phase32_cli_commands.py tests\test_phase32_cli_redaction.py tests\test_phase32_cli_server_manager.py tests\test_phase32_cli_sse.py apps\local-api\tests\test_config.py apps\local-api\tests\test_db_migrations.py apps\local-api\tests\test_chat_trace_error.py'
      fast_backend = '.venv\Scripts\python.exe -m pytest tests apps\local-api\tests -m "not slow"'
      api_backend = '.venv\Scripts\python.exe -m pytest apps\local-api\tests -m "not slow"'
      eval_security = '.venv\Scripts\python.exe -m pytest tests\evals apps\local-api\tests -m "eval or security"'
      release_scale = '.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase29_release_scale_verification.py'
      release_real_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\测试\聊天主链路\2026-04-29\run_chat_main_chain*_cases.py'
      release_power_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\测试\聊天主链路\2026-04-30\run_chat_main_chain_power_cases.py'
      release_natural_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\测试\聊天主链路\2026-04-30\run_chat_natural_interaction_benchmark.py'
      release_quality_chat_e2e = '.\scripts\check.ps1 -Profile release runs docs\测试\聊天主链路\2026-04-30-quality\run_chat_main_chain_quality_cases.py'
      release_quality_chat_e2e_v2 = '.\scripts\check.ps1 -Profile release runs docs\测试\聊天主链路\2026-05-01-quality\run_chat_main_chain_quality_regression_cases.py'
      release_wechat_50_quality_e2e = '.\scripts\check.ps1 -Profile release runs docs\测试\聊天主链路\2026-05-03-wechat-50-scenarios\run_wechat_50_quality_latency.py --api http://127.0.0.1:8765 --output data\check-reports\wechat-50-quality'
      release_wechat_real_quality_e2e = '.\scripts\check.ps1 -Profile release runs docs\测试\聊天主链路\2026-05-03-wechat-real-scenarios\run_wechat_real_scenarios.py --api http://127.0.0.1:8765 --output data\check-reports\wechat-real-quality'
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
  $status = if ($exitCode -eq 0) { "passed" } else { "failed" }
  $script:checkResults += [ordered]@{
    name = $Name
    args = @($ScriptPath) + $Arguments
    status = $status
    exit_code = $exitCode
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($exitCode -ne 0) {
    Write-CheckReport -OverallStatus "failed"
    exit $exitCode
  }
}

function Invoke-ChatMainChainIssueGate {
  $logPath = Join-Path $reportRoot "$runId-chat_e2e_issue_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $issueFiles = @(
    "docs\测试\聊天主链路\2026-04-29\05-待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\08-扩展待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\11-深度待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\15-稳定性回归待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\18-恢复一致性待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\21-知识总结待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\24-多维场景待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\27-任务执行待修复问题.md",
    "docs\测试\聊天主链路\2026-04-29\30-浏览器专项待修复问题.md"
  )
  $openIssues = @()
  foreach ($relativePath in $issueFiles) {
    $path = Join-Path $root $relativePath
    if (-not (Test-Path $path)) {
      $openIssues += [ordered]@{ file = $relativePath; count = 1; reason = "missing_issue_file" }
      continue
    }
    $matches = Select-String -Path $path -Pattern "^##\s+CHAT-E2E-[A-Z0-9-]+" -AllMatches
    $count = @($matches).Count
    if ($count -gt 0) {
      $openIssues += [ordered]@{ file = $relativePath; count = $count; reason = "open_issue_records" }
    }
  }
  $completed = (Get-Date).ToUniversalTime()
  $status = if ($openIssues.Count -eq 0) { "passed" } else { "failed" }
  $summary = [ordered]@{
    status = $status
    checked_files = $issueFiles
    open_issues = $openIssues
  }
  $summary | ConvertTo-Json -Depth 8 | Set-Content -Path $logPath -Encoding UTF8
  $script:checkResults += [ordered]@{
    name = "chat_e2e_issue_gate"
    args = $issueFiles
    status = $status
    exit_code = if ($openIssues.Count -eq 0) { 0 } else { 1 }
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($openIssues.Count -gt 0) {
    Write-Host "Chat E2E issue gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit 1
  }
}

function Invoke-PowerChatIssueGate {
  $logPath = Join-Path $reportRoot "$runId-chat_e2e_power_issue_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $relativePath = "docs\测试\聊天主链路\2026-04-30\08-重型压力待修复问题.md"
  $path = Join-Path $root $relativePath
  $openIssues = @()
  if (-not (Test-Path $path)) {
    $openIssues += [ordered]@{ file = $relativePath; count = 1; reason = "missing_issue_file" }
  } else {
    $matches = Select-String -Path $path -Pattern "^##\s+CHAT-E2E-POWER-FIX" -AllMatches
    $count = @($matches).Count
    if ($count -gt 0) {
      $openIssues += [ordered]@{ file = $relativePath; count = $count; reason = "open_power_issue_records" }
    }
  }
  $completed = (Get-Date).ToUniversalTime()
  $status = if ($openIssues.Count -eq 0) { "passed" } else { "failed" }
  $summary = [ordered]@{
    status = $status
    checked_files = @($relativePath)
    open_issues = $openIssues
  }
  $summary | ConvertTo-Json -Depth 8 | Set-Content -Path $logPath -Encoding UTF8
  $script:checkResults += [ordered]@{
    name = "chat_e2e_power_issue_gate"
    args = @($relativePath)
    status = $status
    exit_code = if ($openIssues.Count -eq 0) { 0 } else { 1 }
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($openIssues.Count -gt 0) {
    Write-Host "POWER Chat E2E issue gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit 1
  }
}

function Invoke-NaturalChatIssueGate {
  $logPath = Join-Path $reportRoot "$runId-chat_e2e_natural_issue_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $relativePath = "docs\测试\聊天主链路\2026-04-30\11-自然聊天待优化结论.md"
  $path = Join-Path $root $relativePath
  $openIssues = @()
  if (-not (Test-Path $path)) {
    $openIssues += [ordered]@{ file = $relativePath; count = 1; reason = "missing_conclusion_file" }
  } else {
    $content = Get-Content -Path $path -Raw -Encoding UTF8
    if ($content -notmatch "PASS 12 / FAIL 0 / BLOCKED 0") {
      $openIssues += [ordered]@{ file = $relativePath; count = 1; reason = "natural_runner_not_all_pass" }
    }
    $matches = Select-String -Path $path -Pattern '^\-\s+`NAT-' -AllMatches
    if (@($matches).Count -gt 0) {
      $openIssues += [ordered]@{ file = $relativePath; count = @($matches).Count; reason = "open_natural_findings" }
    }
  }
  $completed = (Get-Date).ToUniversalTime()
  $status = if ($openIssues.Count -eq 0) { "passed" } else { "failed" }
  $summary = [ordered]@{
    status = $status
    checked_files = @($relativePath)
    open_issues = $openIssues
  }
  $summary | ConvertTo-Json -Depth 8 | Set-Content -Path $logPath -Encoding UTF8
  $script:checkResults += [ordered]@{
    name = "chat_e2e_natural_issue_gate"
    args = @($relativePath)
    status = $status
    exit_code = if ($openIssues.Count -eq 0) { 0 } else { 1 }
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($openIssues.Count -gt 0) {
    Write-Host "Natural chat E2E issue gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit 1
  }
}

function Invoke-QualityChatIssueGate {
  $logPath = Join-Path $reportRoot "$runId-chat_e2e_quality_issue_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $relativePath = "docs\测试\聊天主链路\2026-04-30-quality\08-高质量体验待修复问题.md"
  $path = Join-Path $root $relativePath
  $openIssues = @()
  if (-not (Test-Path $path)) {
    $openIssues += [ordered]@{ file = $relativePath; count = 1; reason = "missing_issue_file" }
  } else {
    $matches = Select-String -Path $path -Pattern "^##\s+CHAT-E2E-QUALITY-FIX" -AllMatches
    $count = @($matches).Count
    if ($count -gt 0) {
      $openIssues += [ordered]@{ file = $relativePath; count = $count; reason = "open_quality_issue_records" }
    }
  }
  $completed = (Get-Date).ToUniversalTime()
  $status = if ($openIssues.Count -eq 0) { "passed" } else { "failed" }
  $summary = [ordered]@{
    status = $status
    checked_files = @($relativePath)
    open_issues = $openIssues
  }
  $summary | ConvertTo-Json -Depth 8 | Set-Content -Path $logPath -Encoding UTF8
  $script:checkResults += [ordered]@{
    name = "chat_e2e_quality_issue_gate"
    args = @($relativePath)
    status = $status
    exit_code = if ($openIssues.Count -eq 0) { 0 } else { 1 }
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($openIssues.Count -gt 0) {
    Write-Host "Quality chat E2E issue gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit 1
  }
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
    "好的，我来",
    "我来继续",
    "记住了。",
    "处理结果如下",
    "作为 AI"
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
  $status = if ($hits.Count -eq 0) { "passed" } else { "failed" }
  ([ordered]@{
    status = $status
    hit_count = $hits.Count
    hits = $hits
  } | ConvertTo-Json -Depth 8) | Set-Content -Path $logPath -Encoding UTF8
  $script:checkResults += [ordered]@{
    name = "phase68_prompt_residual_gate"
    args = $patterns
    status = $status
    exit_code = if ($hits.Count -eq 0) { 0 } else { 1 }
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($hits.Count -gt 0) {
    Write-Host "Phase68 prompt residual gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit 1
  }
}

function Invoke-Phase68VisibleLeakageGate {
  $logPath = Join-Path $reportRoot "$runId-phase68_visible_leakage_gate.log"
  $started = (Get-Date).ToUniversalTime()
  $targets = @()
  $targets += @(Get-ChildItem -Path (Join-Path $reportRoot "wechat-50-quality-*") -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "02-summary.json" })
  $targets += @(Get-ChildItem -Path (Join-Path $reportRoot "wechat-real-quality-*") -Directory -ErrorAction SilentlyContinue | ForEach-Object { Join-Path $_.FullName "02-summary.json" })
  $hits = @()
  foreach ($target in $targets | Select-Object -Unique) {
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
  $status = if ($hits.Count -eq 0) { "passed" } else { "failed" }
  ([ordered]@{
    status = $status
    scanned_targets = $targets | Select-Object -Unique
    hit_count = $hits.Count
    hits = $hits
  } | ConvertTo-Json -Depth 8) | Set-Content -Path $logPath -Encoding UTF8
  $script:checkResults += [ordered]@{
    name = "phase68_visible_leakage_gate"
    args = $targets | Select-Object -Unique
    status = $status
    exit_code = if ($hits.Count -eq 0) { 0 } else { 1 }
    started_at = $started.ToString("o")
    completed_at = $completed.ToString("o")
    duration_seconds = [Math]::Round(($completed - $started).TotalSeconds, 3)
    log_path = $logPath
  }
  if ($hits.Count -gt 0) {
    Write-Host "Phase68 visible leakage gate failed. See: $logPath"
    Write-CheckReport -OverallStatus "failed"
    exit 1
  }
}

Invoke-PythonModule -Name "ruff" -ModuleArgs @("ruff", "check", ".")
Invoke-PythonModule -Name "mypy" -ModuleArgs @("mypy", ".")
switch ($Profile) {
  "smoke" {
    Invoke-PythonModule -Name "pytest_smoke" -ModuleArgs @(
      "pytest",
      "tests\test_response_composer_reasoning.py",
      "tests\test_phase2_routing_safety.py",
      "tests\test_phase32_cli_client.py",
      "tests\test_phase32_cli_commands.py",
      "tests\test_phase32_cli_redaction.py",
      "tests\test_phase32_cli_server_manager.py",
      "tests\test_phase32_cli_sse.py",
      "apps\local-api\tests\test_config.py",
      "apps\local-api\tests\test_db_migrations.py",
      "apps\local-api\tests\test_chat_trace_error.py",
      "--durations=20"
    )
  }
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
    $chatRunnerRoot = Join-Path $root "docs\测试\聊天主链路\2026-04-29"
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
    $powerRunnerRoot = Join-Path $root "docs\测试\聊天主链路\2026-04-30"
    Invoke-PythonScript -Name "chat_e2e_power" -ScriptPath (Join-Path $powerRunnerRoot "run_chat_main_chain_power_cases.py")
    Invoke-PowerChatIssueGate
    Invoke-PythonScript -Name "chat_e2e_natural" -ScriptPath (Join-Path $powerRunnerRoot "run_chat_natural_interaction_benchmark.py")
    Invoke-NaturalChatIssueGate
    $qualityRunnerRoot = Join-Path $root "docs\测试\聊天主链路\2026-04-30-quality"
    Invoke-PythonScript -Name "chat_e2e_quality" -ScriptPath (Join-Path $qualityRunnerRoot "run_chat_main_chain_quality_cases.py")
    Invoke-QualityChatIssueGate
    $qualityRunnerRootV2 = Join-Path $root "docs\测试\聊天主链路\2026-05-01-quality"
    Invoke-PythonScript -Name "chat_e2e_quality_v2" -ScriptPath (Join-Path $qualityRunnerRootV2 "run_chat_main_chain_quality_regression_cases.py")
    $wechat50Root = Join-Path $root "docs\测试\聊天主链路\2026-05-03-wechat-50-scenarios"
    Invoke-PythonScript -Name "chat_e2e_wechat_50_quality" -ScriptPath (Join-Path $wechat50Root "run_wechat_50_quality_latency.py") -Arguments @("--api", "http://127.0.0.1:8765", "--output", (Join-Path $reportRoot "wechat-50-quality-$runId"))
    $wechatRealRoot = Join-Path $root "docs\测试\聊天主链路\2026-05-03-wechat-real-scenarios"
    Invoke-PythonScript -Name "chat_e2e_wechat_real_quality" -ScriptPath (Join-Path $wechatRealRoot "run_wechat_real_scenarios.py") -Arguments @("--api", "http://127.0.0.1:8765", "--output", (Join-Path $reportRoot "wechat-real-quality-$runId"))
    Invoke-Phase68PromptResidualGate
    Invoke-Phase68VisibleLeakageGate
  }
  default {
    Invoke-PythonModule -Name "pytest" -ModuleArgs @("pytest", "--durations=20")
  }
}
Write-CheckReport -OverallStatus "passed"
