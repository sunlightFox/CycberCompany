# 微信 50 场景质量与耗时回归

这是面向真实微信的 50 场景高频回归子集，覆盖：

- 闲聊与自然对话
- 复杂多轮与上下文延续
- 记忆召回与纠错
- 人格、情绪与边界
- 办公写作与总结
- 浏览器、终端、Skill、桌面能力
- 审批、高风险与失败恢复
- 微信投递和慢链路回收

## 运行

```powershell
python docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/run_wechat_50_quality_latency.py --api http://127.0.0.1:8765 --output docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/evidence --manual-times docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/manual-times.jsonl
```

## 输出

- `00-preflight.json`
- `01-report.md`
- `02-summary.json`
- `03-gap-list.json`
- `04-fix-queue.json`
- `05-rerun-list.json`
- `latency.jsonl`
- `quality-scores.jsonl`
- `quality-scores.jsonl` 中会额外记录 `gate_status`、prompt 版本覆盖和残留扫描结果。

## 说明

- 每条回复都会记录质量判定、门禁状态、标签和优化建议。
- 每个场景都统计首 token、turn 总耗时、工具耗时、微信投递耗时和 trace 完整性。
- `fail`、`latency_slow` 或缺失 turn/trace/投递 的场景会进入修复队列和复测列表。
