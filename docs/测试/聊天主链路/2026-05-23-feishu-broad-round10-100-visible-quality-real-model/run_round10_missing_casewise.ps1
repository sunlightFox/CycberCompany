$ErrorActionPreference = "Continue"

$runner = Join-Path $PSScriptRoot "run_feishu_broad_round10_100_visible_quality_real_model_cases.py"
$evidence = Join-Path $PSScriptRoot "evidence"
$progressPath = Join-Path $evidence "missing_casewise_progress.json"
New-Item -ItemType Directory -Force -Path $evidence | Out-Null

$items = @()
for ($i = 1; $i -le 100; $i++) {
    $caseId = "FBR10-100-{0:D3}" -f $i
    $resultPath = Join-Path $evidence "casewise_$($caseId)_result.json"
    if (Test-Path $resultPath) {
        continue
    }

    $stdoutPath = Join-Path $evidence "missing_$($caseId).stdout.txt"
    $stderrPath = Join-Path $evidence "missing_$($caseId).stderr.txt"
    $startedAt = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
    $exitCode = $null
    try {
        & python $runner --case-id $caseId --merge-existing > $stdoutPath 2> $stderrPath
        $exitCode = $LASTEXITCODE
    }
    catch {
        $exitCode = -1
        $_ | Out-File -FilePath $stderrPath -Append -Encoding utf8
    }

    $items += [ordered]@{
        case_id = $caseId
        exit_code = $exitCode
        started_at = $startedAt
        ended_at = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
        result_exists = Test-Path $resultPath
    }
    [ordered]@{
        run_label = "FBR10-100-VISIBLE-REAL-20260523"
        mode = "missing-casewise-powershell"
        updated_at = Get-Date -Format "yyyy-MM-ddTHH:mm:ss"
        completed = $items.Count
        items = $items
    } | ConvertTo-Json -Depth 5 | Set-Content -Path $progressPath -Encoding utf8
}
