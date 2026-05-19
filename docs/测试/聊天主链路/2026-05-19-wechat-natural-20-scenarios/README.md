# 微信 20 轮自然度专项测试

目标：从小耀已绑定的微信真实渠道发消息，连续执行 20 轮多场景对话，重点检查回复是否自然、像人话、不带系统腔、不生硬，同时保留后端链路证据。

本目录复用真实微信入口采证脚本能力，额外提供一套更偏“聊天自然度”的 20 轮专项 case。

## 测试重点

- 自然度：像微信聊天，不像系统播报。
- 口语感：少模板腔、少生硬提示词堆砌。
- 连续性：能接上上一轮，不跳话题。
- 诚实边界：不能装作已执行，不能泄漏内部字段。
- 微信感：短句、顺接、结论先行，但不过度卖萌。

## 推荐执行方式

1. 启动本地 API，并确保小耀微信渠道已绑定。
2. 在微信私聊中按 `01-测试用例-微信20轮自然度.md` 的顺序逐条发送消息。
3. 可选：把人工观察时间写入 `manual-times.jsonl`。
4. 跑采证脚本：

```powershell
python docs/测试/聊天主链路/2026-05-19-wechat-natural-20-scenarios/run_xiaoyao_wechat_natural_20_scenarios.py --api http://127.0.0.1:8765 --output docs/测试/聊天主链路/2026-05-19-wechat-natural-20-scenarios/evidence --manual-times docs/测试/聊天主链路/2026-05-19-wechat-natural-20-scenarios/manual-times.jsonl
```

## 人工评分建议

每轮建议补一份 10 分人工分，重点看：

- 2 分：是否先回应当前问题
- 2 分：是否自然，不像系统提示
- 2 分：是否有微信聊天口吻，不生硬
- 2 分：是否延续上下文
- 2 分：是否诚实表达边界/不确定性

低于 8 分视为低分，需要进 `fix_queue`。

## 输出

- `evidence/01-report.md`：自动采证报告
- `evidence/02-summary.json`：汇总
- `evidence/03-gap-list.json`：缺口
- `evidence/04-fix-queue.json`：修复队列
- `evidence/05-rerun-list.json`：建议重跑 case
