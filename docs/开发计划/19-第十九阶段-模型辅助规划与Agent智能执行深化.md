# 第十九阶段：模型辅助规划与 Agent 智能执行深化

## 摘要

第十九阶段聚焦“复杂聊天进入行动后的智能质量”。当前 Task Engine 已经支持 workflow、agent、supervisor 分流，AgentLoopRunner 也具备 observe、plan、act、evaluate、stop 的可回放闭环；但 Planner 仍以 rule-first 为主，ModelPlanner 在 runtime contracts 中明确为 degraded，复杂任务的步骤质量、探索策略和修正能力仍有限。

本阶段目标是在不放松 Safety、Approval、Asset Broker、Capability Graph、Trace 的前提下，引入模型辅助规划和模型辅助评估，让 Agent 不只是按规则跑步骤，而是能在受控边界内更好地拆任务、选择下一步、总结观察、修正计划。

本阶段只做后端，不新增 UI。

## 阶段定位

第十九阶段回答：

```text
复杂任务是否能生成更合理的计划
Agent 是否能根据观察调整下一步
工具失败后是否能提出替代路径
模型生成的计划是否会被安全策略修剪
高风险动作是否仍然先计划、后审批、再执行
Skill/MCP 是否作为能力候选参与规划，而不是被硬编码调用
所有循环是否仍可预算限制、可 replay、可 audit
```

## 当前基线判断

| 能力 | 当前完成度 | 主要问题 |
|---|---:|---|
| Task Planner | 约 80% | rule-first，模型辅助关闭 |
| Agent Loop | 约 80% | 闭环存在，但 next action 自适应不足 |
| Workflow | 约 82% | 固定任务稳定，但模板扩展有限 |
| Supervisor | 约 76% | 可进入多视角，但任务拆分深度有限 |
| Skill/MCP 协同 | 约 77% | 能纳入计划，但候选选择偏规则 |

## 阶段原则

1. 模型只生成候选计划，不直接执行。
2. 所有候选计划必须经过 schema validation、policy prune、Safety preflight。
3. 固定任务仍优先 workflow，不因模型可用而滥用 agent。
4. Agent 每轮必须有预算、stop_reason、observation 和 trace。
5. Skill/MCP 只能作为能力引用，具体执行仍走 ToolRuntime。
6. 模型规划失败不能阻断规则 fallback。

## 阶段范围

### 本阶段必须完成

```text
ModelPlanner service
PlanCandidate schema
PlanVerifier
PolicyPruner
Agent next-action selector
Observation summarizer
Tool failure recovery planner
Skill/MCP capability candidates
Agent evaluation rubric
Planner eval dataset
```

### 本阶段不做

```text
不做完全自主后台代理
不允许 agent 修改安全策略
不允许模型直接构造 shell command 执行
不允许模型直接读取 secret 或真实 asset
不新增前端任务回放页面
不取消现有 rule/workflow planner
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 19.1 | ModelPlanner 契约 | plan candidate、confidence、risk hints |
| 19.2 | PlanVerifier 与 PolicyPruner | schema、能力、安全、预算修剪 |
| 19.3 | Agent 下一步选择器 | observe 后选择 act / revise / stop |
| 19.4 | 工具失败恢复规划 | retry、alternate、ask user、stop |
| 19.5 | Skill/MCP 能力候选接入 | candidates、score、policy preview |
| 19.6 | Supervisor 任务拆分增强 | 多角色计划、合并、冲突处理 |
| 19.7 | Planner/Agent 评测 | 计划质量、边界、安全、可回放 |

## 小阶段 19.1：ModelPlanner 契约

### 目标

为模型辅助规划建立受控服务层，让模型输出只是一组结构化候选。

### 输入

```text
task_goal
dialogue_state
intent_decision
mode_decision
context_decision
available_tools
available_skills
available_mcp_tools
asset_handle_summaries
runtime_contracts
risk_policy_summary
budget
```

### 输出

```text
candidate_id
planner_type=model_planner
recommended_mode
steps
success_criteria
assumptions
missing_information
risk_hints
required_capabilities
required_assets
confidence
reasoning_summary
```

### 验收

```text
模型输出必须通过 Pydantic schema
模型输出不包含 secret 明文
模型输出不能直接带真实路径、token、cookie、私钥
ModelPlanner 可配置关闭
关闭后仍走 rule/workflow fallback
```

## 小阶段 19.2：PlanVerifier 与 PolicyPruner

### 目标

把模型候选计划变成可执行计划之前，先做结构、安全、能力、预算和权限修剪。

### 检查项

```text
schema_valid
mode_allowed
step_type_allowed
capability_available
asset_handle_allowed
risk_level_acceptable
approval_strategy_present
budget_within_limit
no_direct_secret
no_direct_shell_command_from_model
```

### 修剪策略

```text
remove_unavailable_skill
remove_unavailable_mcp_tool
replace_high_risk_step_with_approval_checkpoint
replace_ambiguous_action_with_clarification
downgrade_to_workflow_when_fixed
fallback_to_rule_plan_when_invalid
```

### 验收

```text
不可用 Skill/MCP 不进入执行步骤
高风险步骤必须插入 approval checkpoint
模型生成危险命令不会进入 ToolRuntime
PlanVerifier 写 planner_decision 和 trace span
```

## 小阶段 19.3：Agent 下一步选择器

### 目标

让 Agent 根据观察结果动态决定下一步，而不是只取下一个 pending step。

### 输入

```text
current_plan
last_observation
tool_result_summary
safety_decision
budget_state
failure_history
dialogue_state
```

### 输出

```text
next_action_type
selected_step_id
plan_delta
needs_user_input
needs_approval
stop_reason
confidence
reason_codes
```

### next_action_type

```text
act
revise_plan
ask_user
request_approval
retry_tool
switch_to_skill
switch_to_mcp
stop_success
stop_blocked
stop_budget
```

### 验收

```text
工具失败后不会盲目重复同一步
预算不足时停止并给 retry plan
需要用户输入时不继续执行
每轮选择写 agent.loop span
```

## 小阶段 19.4：工具失败恢复规划

### 目标

提升任务失败后的可恢复性，让用户看到真实失败点和下一步。

### 失败分类

```text
tool_unavailable
permission_denied
safety_blocked
approval_required
asset_unavailable
mcp_server_unready
skill_disabled
timeout
invalid_output
budget_exhausted
```

### 恢复动作

```text
retry_same_tool_once
switch_alternative_tool
ask_user_for_asset
ask_user_for_scope
request_approval
pause_task
complete_partial_result
create_retry_plan
```

### 验收

```text
失败任务有 recovery_plan
用户可见回复不暴露内部堆栈
安全阻断不能被 retry 绕过
失败恢复写 trace/audit/replay
```

## 小阶段 19.5：Skill/MCP 能力候选接入

### 目标

让 Skill 和 MCP 作为受控能力候选进入 Planner，而不是简单关键词匹配后直接执行。

### Skill 候选字段

```text
skill_id
bundle_id
match_score
declared_permissions
required_tools
required_assets
risk_level
policy_status
eval_status
```

### MCP 候选字段

```text
server_id
mcp_tool_id
registry_tool_name
tool_schema
risk_level
server_status
member_scope_status
untrusted_content_policy
```

### 验收

```text
Skill/MCP 候选进入 planner_decision
未启用或未授权能力不会被执行
MCP resource/prompt 默认按不可信内容处理
Skill manifest 权限声明和实际步骤要比对
```

## 小阶段 19.6：Supervisor 任务拆分增强

### 目标

让多视角任务可以根据目标拆出多个成员视角，并合并冲突结论。

### 拆分维度

```text
research
implementation
security
product
qa
release
```

### 合并输出

```text
member_findings
agreements
conflicts
open_questions
recommended_plan
risk_notes
```

### 验收

```text
supervisor 只在多视角复杂任务进入
成员能力受 Capability Graph 限制
冲突结论不被静默覆盖
合并结果进入 ResponseComposer
```

## 小阶段 19.7：Planner/Agent 评测

### 目标

用 eval 约束模型辅助规划，防止“看起来更聪明，但边界更松”。

### 必测 case

```text
固定任务仍走 workflow
探索任务进入 agent
高风险任务先计划再审批
模型生成危险命令被修剪
Skill 不可用时移出计划
MCP 未 ready 时移出计划
工具失败后生成 retry plan
预算耗尽后暂停
Supervisor 冲突合并
```

### 验收命令

```text
.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase19_model_planner_agent.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

## 阶段总验收标准

第十九阶段完成时必须满足：

```text
ModelPlanner 从 degraded 提升为 implemented 或 implemented_with_fallback
模型规划可关闭，关闭后规则路径仍稳定
模型候选计划必须经过 verifier/pruner
Agent 能根据 observation 动态选择下一步
工具失败、Skill 不可用、MCP 未 ready 都有恢复路径
高风险动作不能被模型规划绕过审批
Planner/Agent eval 覆盖 allow、deny、approval、degraded、failure
```
