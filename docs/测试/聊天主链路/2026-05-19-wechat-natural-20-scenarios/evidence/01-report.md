# 微信真实入口场景测试报告

- 生成时间：2026-05-19T06:24:38.361217+00:00
- 用例数：1

| Case | Result | Turn | 状态 | 出站 | 首 token ms | turn内ms | 出站ms | 质量提示 | 门禁 |
|---|---|---|---|---|---:|---:|---:|---|---|
| wx-natural-001 | fail | `` | missing | missing |  |  |  | 差:no_turn,missing_observed_reply | fail |

## 逐条回放

### wx-natural-001 · 开场打招呼
- 发送：wx-natural-001：小耀，先正常跟我打个招呼，别做任何操作。
- 回复：N/A
- 结果：fail（no_turn, turn_missing, no_delivery, quality_bad, missing_reply）
- 质量：差 / no_turn, missing_observed_reply
- 门禁：fail / missing_reply, no_turn, prompt_version_missing, quality_block
- 附件理解：understanding=n/a, understood=0, degraded=0, memory=0
- 修订：no；红线：pass
- Reply source：missing

## Shadow Policy Advisory

| Case | Gate | Scene | Compare | Diffs | Candidate | Target | Blockers |
|---|---|---|---|---|---|---|---|
| wx-natural-001 | False (missing) | none | False | none | False | none | none |
## 汇总

- 首 token p50/p95：p50=None, p95=None
- 用户体感总耗时 p50/p95：p50=None, p95=None
- turn 内耗时 p50/p95：p50=None, p95=None
- 入站轮询 p50/p95：p50=None, p95=None
- 出站投递 p50/p95：p50=None, p95=None
- 最慢 case：wx-natural-001 / N/A
- 最慢 span：N/A / N/A / N/A
- 慢点分组：{}
- 阅读型符号命中：0
- 损坏修复触发次数：0
- prompt 版本覆盖：{"voice_policy_v4_coverage": 0.0, "prompt_assembly_v4_coverage": 0.0, "voice_policy_version_counts": {"missing": 1}, "prompt_assembly_version_counts": {"missing": 1}}
- 门禁分布：{"fail": 1}
- 结果分布：{"pass": 0, "warn": 0, "fail": 1}
- 质量判定分布：{"好": 0, "一般": 0, "差": 1, "未知": 0}
- Shadow policy 汇总：{"comparison_enabled_count": 0, "promotion_candidate_count": 0, "policy_diff_field_counts": {}, "promotion_target_counts": {"none": 1}, "promotion_blocker_counts": {}}
- Promotion readiness：{"ready_targets": [], "blocked_targets": ["casual_chat_opening", "followthrough_opening"], "readiness_reasons": {"casual_chat_opening": ["comparison_enabled_count_below_threshold", "promotion_candidate_count_below_threshold", "promotion_candidate_rate_below_threshold", "target_not_seen"], "followthrough_opening": ["comparison_enabled_count_below_threshold", "promotion_candidate_count_below_threshold", "promotion_candidate_rate_below_threshold", "target_not_seen"]}}

## 延迟口径

- 入站轮询耗时：T1 -> T2。
- 队列等待耗时：T3 -> T4。
- turn 内耗时：T4 -> T7。
- 出站投递耗时：T8 -> T9。
- 用户体感总耗时：T0 -> T10，需要人工在 manual-times JSONL 中填写。
