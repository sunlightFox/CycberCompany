$ErrorActionPreference = 'Continue'
$auth = Get-Content -Raw (Join-Path $env:USERPROFILE '.codex\auth.json') | ConvertFrom-Json
$env:OPENAI_API_KEY = $auth.OPENAI_API_KEY
$root = 'c:\Users\Administrator\Desktop\CycberCompany'
Set-Location $root
foreach ($i in 54..100) {
  $case = 'FDU18-100-{0:D3}' -f $i
  $out = ".run/round18_individual_${case}.stdout.txt"
  $err = ".run/round18_individual_${case}.stderr.txt"
  python -X utf8 docs/测试/聊天主链路/2026-05-24-feishu-daily-use-round18-100-real-model/run_feishu_daily_use_round18_100_real_model_cases.py --casewise --case-id $case --case-retries 1 --case-timeout 180 > $out 2> $err
}
