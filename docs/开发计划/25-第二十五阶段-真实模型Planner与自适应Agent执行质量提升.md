# 第二十五阶段：真实模型 Planner 与自适应 Agent 执行质量提升

## 摘要

第二十五阶段聚焦“复杂任务计划质量”。当前第十九阶段已经让 `ModelPlanner` 从 degraded 变成 implemented，具备 candidate、verification、policy pruning、next action 和 failure recovery；但 `model_assist=false`，候选计划仍由规则计划转写而来。

本阶段目标是在候选计划层启用真实模型辅助规划，让复杂任务可以获得更好的步骤拆解、工具选择、Skill/MCP 候选选择、失败恢复和下一步推理，同时保持 candidate-only、安全修剪和可回放执行。

本阶段只做后端，不新增 UI。

## 阶段定位

第二十五阶段回答：

```text
复杂任务是否能由模型生成候选计划
模型生成的计划是否必须先验证和修剪
Agent 是否能根据观察动态改计划
工具失败后是否能生成更合理恢复路径
Skill/MCP 是否能作为候选能力参与模型规划
模型规划是否仍然受预算、权限、安全和审批控制
```

## 当前基线判断

| 能力 | 当前状态 | 缺口 |
|---|---|---|
| ModelPlanner | implemented | 候选为 deterministic surrogate |
| PlanVerifier | implemented | 已能验证 schema/risk/budget/capability |
| PolicyPruner | implemented | 已能移除危险命令、敏感路径、不可用能力 |
| AgentNextActionSelector | implemented | 决策记录存在，智能选择仍偏规则 |
| ToolFailureRecoveryPlanner | implemented | 恢复计划存在，策略深度可增强 |

## 阶段原则

1. 模型只生成候选计划，不能直接执行。
2. 所有模型候选必须经过 PlanVerifier 和 PolicyPruner。
3. 模型候选不能包含真实 secret、真实路径、真实 token、私钥、cookie。
4. 高风险计划必须插入 approval checkpoint。
5. Skill/MCP 候选必须经过 capability snapshot 和 policy preview。
6. 模型规划失败时，规则 planner fallback 必须稳定。

## 阶段范围

### 本阶段必须完成

```text
ModelPlanRequest schema
ModelPlanCandidateGenerator
模型辅助 step synthesis
模型辅助 capability selection
Observation-aware replanning
Tool failure recovery model assist
Plan quality scoring
planner eval dataset
```

### 本阶段不做

```text
不做完全自主后台代理
不允许模型直接执行 shell command
不允许模型直接改 Safety/Approval/Capability policy
不允许 Agent 无限循环
不新增前端任务回放页面
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 25.1 | ModelPlanRequest 契约 | 目标、上下文、能力、预算、风险摘要 |
| 25.2 | 候选计划生成器 | 真实模型候选、JSON schema、fallback |
| 25.3 | 计划质量评分 | coverage、safety、efficiency、recoverability |
| 25.4 | Observation-aware replanning | 观察驱动的 plan_delta |
| 25.5 | 工具失败模型辅助恢复 | alternate、ask_user、pause、partial |
| 25.6 | Skill/MCP 候选选择增强 | model-assisted ranking + policy preview |
| 25.7 | Agent 质量评测 | 成功率、安全率、预算、replay 完整性 |

## 小阶段 25.1：ModelPlanRequest 契约

### 目标

为真实模型 planner 提供最小、脱敏、可审计输入。

### 输入字段

```text
task_id
goal
dialogue_state_summary
intent_summary
mode_summary
context_summary
available_tool_summaries
skill_candidates
mcp_candidates
asset_handle_summaries
risk_policy_summary
budget
success_criteria
forbidden_actions
trace_id
```

### 禁止字段

```text
raw_secret
raw_token
raw_cookie
raw_private_key
raw_wallet_seed
real_sensitive_path
approval_token
```

### 验收

```text
输入经过 redaction
asset 只出现 handle summary
模型请求写 planner.model_call span
无可用模型时 fallback 到规则 planner
```

## 小阶段 25.2：候选计划生成器

### 目标

实现真实模型候选生成，并确保模型输出不能直接进入执行。

### 输出字段

```text
candidate_id
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
model_assist
```

### 输出校验

```text
JSON parse
Pydantic schema validation
allowed step types
allowed capability refs
no direct secret
no direct dangerous shell
budget bound
```

### 验收

```text
模型输出非法时 fallback
危险 step 被 PolicyPruner 移除
高风险 step 被转成 approval checkpoint
候选计划不等于最终计划
```

## 小阶段 25.3：计划质量评分

### 目标

对模型候选和规则候选做可解释评分，选择更安全、更可执行的计划。

### 评分维度

```text
goal_coverage
step_coherence
capability_fit
safety_compliance
budget_efficiency
missing_information_handling
recoverability
artifact_clarity
```

### 验收

```text
低分模型计划不会被选为最终计划
规则 fallback 可参与对比
评分进入 planner_decision
release report 可汇总 planner quality
```

## 小阶段 25.4：Observation-aware replanning

### 目标

让 Agent 在执行中根据观察结果调整计划。

### 触发条件

```text
tool_failed
tool_output_invalid
safety_blocked
approval_required
asset_missing
mcp_unready
skill_disabled
budget_near_limit
user_new_constraint
```

### 输出

```text
plan_delta
next_action_type
new_missing_information
revised_steps
stop_reason
confidence
```

### 验收

```text
工具失败后不盲目重复
预算临界时主动停止或压缩计划
用户新增约束后更新 plan_delta
replanning 写 replay evidence
```

## 小阶段 25.5：工具失败模型辅助恢复

### 目标

让失败恢复不仅是固定模板，而能根据上下文提出实际替代路径。

### 恢复策略

```text
retry_with_modified_args
switch_to_read_only_tool
switch_to_skill
switch_to_mcp
ask_user_for_asset
ask_user_for_scope
complete_partial
pause_with_retry_plan
stop_blocked
```

### 验收

```text
安全阻断不能被 recovery 绕过
审批缺失时只生成 approval path
不可用工具不会反复调用
恢复计划进入 ResponseComposer
```

## 小阶段 25.6：Skill/MCP 候选选择增强

### 目标

提升 Skill/MCP 在复杂任务中的选择质量。

### Ranking 因子

```text
goal_match
declared_permission_fit
required_asset_fit
eval_status
risk_level
member_scope
server_ready
untrusted_content_risk
```

### 验收

```text
模型 ranking 不能覆盖 policy deny
未启用 Skill 不进入执行
未 ready MCP 不进入执行
候选排序和排除原因可查询
```

## 小阶段 25.7：Agent 质量评测

### 目标

评估真实模型 planner 带来的质量提升和安全边界。

### 必测 case

```text
复杂研究任务
固定任务不进 agent
工具失败恢复
Skill 候选选择
MCP 候选不可用
高风险 step 审批
危险命令修剪
预算耗尽停止
模型超时 fallback
```

### 验收命令

```text
.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase25_model_planner_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

## 阶段总验收标准

第二十五阶段完成时必须满足：

```text
ModelPlanner 支持真实模型候选生成
候选计划必须经过 verifier/pruner/quality scoring
Agent 能基于 observation 重新规划
工具失败恢复不绕过安全边界
Skill/MCP ranking 有模型辅助但服从 policy
规则 fallback 始终可用
```

