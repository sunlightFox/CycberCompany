# 第九十一阶段实施计划：ChatRuntime 物理拆分与宿主瘦身收尾

## Summary

本阶段目标是把聊天主链的大宿主继续做薄，并把治理真相源升级为：

- `file ownership`
- `size budget`
- `growth gate`

阶段完成后，聊天主链需要持续朝以下宿主关系收敛：

`chat facade shell -> ChatRuntime / ChatTurnExecutionOrchestrator / dedicated helper modules`

## Governance Targets

- `chat.py <= 2800`
- `natural_chat.py <= 950`
- `brain_decision.py <= 950`
- `wechat_gateway.py <= 1600`
- `feishu_gateway.py <= 900`

## Ownership Targets

- `chat.py`: `facade_shell_only`
- `natural_chat.py`: `runtime_surface_only`
- `brain_decision.py`: `decision_orchestrator_only`
- `wechat_gateway.py`: `provider_shell_only`
- `feishu_gateway.py`: `provider_shell_only`

## Diagnostics

`/api/system/chat-mainline-readiness`、`/api/system/runtime-topology`、release summary 统一输出：

- `size_budget_lines`
- `current_size_lines`
- `growth_gate`
- `ownership_split_status`

## Notes

- 本阶段允许 readiness 为 `partial`，前提是门禁已落地并能稳定暴露预算超限与 ownership residue。
- 不改 `/api/chat/*` 对外功能接口。
