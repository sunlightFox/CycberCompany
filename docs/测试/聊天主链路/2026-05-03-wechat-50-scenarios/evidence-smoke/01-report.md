# 微信真实入口场景测试报告

- 生成时间：2026-05-03T12:25:54.585121+00:00
- 用例数：1

| Case | Turn | 状态 | 出站 | 首 token ms | turn内ms | 出站ms | 质量提示 |
|---|---|---|---|---:|---:|---:|---|
| wechat-50-001 | `` | missing | missing |  |  |  | 差:no_turn,missing_observed_reply |

## 汇总

- 首 token p50/p95：p50=None, p95=None
- 用户体感总耗时 p50/p95：p50=None, p95=None
- turn 内耗时 p50/p95：p50=None, p95=None
- 入站轮询 p50/p95：p50=None, p95=None
- 出站投递 p50/p95：p50=None, p95=None
- 最慢 case：wechat-50-001 / N/A
- 最慢 span：N/A / N/A / N/A
- 慢点分组：{}
- 阅读型符号命中：0
- 质量判定分布：{"好": 0, "一般": 0, "差": 1, "未知": 0}

## 延迟口径

- 入站轮询耗时：T1 -> T2。
- 队列等待耗时：T3 -> T4。
- turn 内耗时：T4 -> T7。
- 出站投递耗时：T8 -> T9。
- 用户体感总耗时：T0 -> T10，需要人工在 manual-times JSONL 中填写。
