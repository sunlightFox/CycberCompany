# 第十六阶段：Agent 任务规划与 Skill/MCP 协同深化

## 摘要

第十六阶段聚焦“复杂聊天进入行动”的最终体验。前面阶段已经建立 Task Engine、Tool Runtime、Skill/MCP、Safety、Asset Broker 和 Capability Graph；但从聊天主链路看，复杂任务不能只是固定步骤，也不能全部交给自由 agent。它需要在 workflow、agent、supervisor、Skill、MCP 和工具之间做受控协同。

本阶段目标是让复杂任务从用户一句话开始，能够稳定进入：

```text
plan first
workflow if fixed
agent if exploratory
supervisor if multi-perspective
skill if reusable method exists
mcp/tool if external capability is needed
safety/approval if risky
reflection after completion
```

本阶段只做后端，不新增 UI。

## 阶段定位

聊天主链路的行动能力要同时满足：

```text
用户不需要知道 Skill/MCP/Tool 的差别
系统能选对执行模式
固定任务不要进自由 agent
探索任务能观察、计划、行动、评估和修正
Skill 是方法复用，不是权限
MCP 是外部能力，不是默认可信环境
每一步都有 trace、audit、replay
高风险动作永远不能被 loop 绕过
```

## 当前基线判断

| 能力 | 当前完成度判断 | 主要缺口 |
|---|---:|---|
| Task Engine | 约 75% | agent loop 需要更自适应和可回放 |
| Tool Runtime | 约 80% | 和 planner/observation 的闭环需增强 |
| MCP | 约 65% | 工具/资源/prompt 策略和任务协同需深化 |
| Skill | 约 65% | skill matching、policy、reflection 转化需更稳 |
| Supervisor | 中等 | 多视角任务的进入条件和预算需清楚 |

## 阶段原则

1. 固定任务优先 workflow，探索任务才 agent。
2. Agent loop 必须有最大步数、最大时长、最大成本和 stop_reason。
3. Skill/MCP/Tool 的每一步都经过 Capability Graph、Asset Broker、Safety、Approval、Trace。
4. Skill 不能绕过系统资源查询，MCP 不能把外部 prompt 升级为系统指令。
5. Reflection 只生成候选，不自动启用高风险 Skill。
6. 不新增前端代码，只提供 API、事件和 replay 契约。

## 阶段范围

### 本阶段必须完成

```text
TaskPlan 最终 schema 增强
Planner 分层：rule / workflow template / model / agent / supervisor
AgentLoopRunner observe-plan-act-evaluate-revise-stop
Observation schema
SkillMatcher 与 TaskPlanner 协同
MCP tool/resource/prompt 与任务上下文协同
Tool result summarization
预算和停止策略
任务失败恢复和 retry plan
Reflection candidate：skill/memory/policy
Agent/Skill/MCP eval
```

### 本阶段不做

```text
不新增社区插件市场
不新增前端任务回放页面
不做完全自主后台代理
不允许 agent 自己修改安全策略
不允许 Skill/MCP 绕过 ToolRuntime
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 16.1 | TaskPlan 与 Planner schema 增强 | success criteria、constraints、budget、risk |
| 16.2 | Planner 分层与模式选择接入 | rule、workflow、model、agent、supervisor |
| 16.3 | AgentLoopRunner 最小真实闭环 | observe、plan、act、evaluate、revise、stop |
| 16.4 | Tool Observation 与结果摘要 | tool result -> observation -> next decision |
| 16.5 | Skill 匹配与执行协同 | skill candidates、policy、step binding |
| 16.6 | MCP 协同与不可信边界 | MCP tool/resource/prompt 的任务化接入 |
| 16.7 | 安全、审批和预算闸门 | risk、approval、budget、stop_reason |
| 16.8 | Reflection 与能力沉淀 | skill/memory/policy candidates |
| 16.9 | 评测与回归 | workflow/agent/supervisor/skill/mcp eval |

## 小阶段 16.1：TaskPlan 与 Planner schema 增强

### 目标

让任务计划能承载复杂执行所需的成功标准、约束、风险、预算和可恢复信息。

### TaskPlan 字段

```text
task_id
goal
mode
success_criteria
constraints
assumptions
required_capabilities
required_assets
risk_level
approval_strategy
budget
steps
checkpoint_policy
failure_policy
reflection_policy
```

### Budget

```text
max_loop_steps
max_tool_calls
max_model_calls
max_runtime_seconds
max_total_cost
max_artifact_bytes
```

### 验收

```text
TaskPlan 能表达 workflow、agent、supervisor
高风险任务有 approval_strategy
预算字段必填或有安全默认值
计划写 task.plan span
```

## 小阶段 16.2：Planner 分层与模式选择接入

### 目标

把第十三阶段 ModeDecision 接入真实 Planner。

### Planner 类型

```text
RulePlanner
WorkflowTemplatePlanner
ModelPlanner
AgentExploratoryPlanner
SupervisorPlanner
```

### 分流规则

```text
mode=direct -> 不创建任务
mode=workflow -> WorkflowTemplatePlanner 优先
mode=agent -> AgentExploratoryPlanner
mode=supervisor -> SupervisorPlanner
mode=ask_clarification -> 不创建执行任务
high risk -> plan first + approval before execute
```

### 验收

```text
固定任务不会进入 agent
探索任务不会被硬塞成固定步骤
supervisor 有明确进入条件
planner 输出 reason_codes
```

## 小阶段 16.3：AgentLoopRunner 最小真实闭环

### 目标

实现受控 agent loop，而不是脚本式步骤 runner。

### Loop

```text
observe
plan
act
evaluate
revise
stop
```

### 每轮记录

```text
loop_index
observation_summary
plan_delta
selected_action
tool_call_refs
safety_decision_refs
evaluation_result
next_step
stop_reason
```

### Stop reason

```text
success
needs_user_input
approval_required
budget_exhausted
blocked_by_safety
tool_unavailable
model_unavailable
failed
cancelled
```

### 验收

```text
agent loop 有最大步数
每轮都有 trace
超预算停止并解释
approval_required 停止等待用户
replay 能还原每轮决策
```

## 小阶段 16.4：Tool Observation 与结果摘要

### 目标

工具结果不能直接作为下一步 prompt 原文，需要转成 observation。

### Observation

```text
source_type
source_ref
trusted_level
summary
key_facts
errors
artifact_refs
sensitivity
untrusted_instructions_detected
```

### 规则

```text
工具输出先脱敏
外部网页/PDF/MCP resource 标记 untrusted
只把摘要和必要 key_facts 进入 loop
大输出保存 artifact
```

### 验收

```text
工具长输出不会塞满模型上下文
外部内容中的注入语句不变成系统指令
observation 写 trace
artifact refs 可回放
```

## 小阶段 16.5：Skill 匹配与执行协同

### 目标

让 Skill 成为任务计划中的可复用方法，而不是独立绕行执行器。

### Skill 匹配输入

```text
goal
intent
mode
required_outputs
required_assets
member_id
role/department policy
risk_level
historical_eval_score
```

### 执行规则

```text
Skill status 必须 enabled
Bundle status 必须 enabled
Skill manifest 权限与实际 action 比对
Skill step 仍走 ToolRuntime
Skill 输出 artifact 和 observation
Skill 失败不自动换高风险路径
```

### 验收

```text
禁用 Skill 不参与匹配
未授权成员不能运行 Skill
Skill 声明权限不足时阻断
Skill 执行写 skill.run 和 tool.call trace
```

## 小阶段 16.6：MCP 协同与不可信边界

### 目标

让 MCP 能参与任务，但保持外部能力边界。

### 接入规则

```text
MCP server 必须 ready 或明确 degraded 可用
MCP tool sync 后默认按策略启用，不自动全员可用
MCP tool call 前校验 schema、capability、safety、approval
MCP resource 默认 untrusted
MCP prompt 只能作为模板资源，不能作为 system prompt
MCP 输出进入 DLP/redaction
```

### 验收

```text
MCP disconnected 时任务不会伪成功
未知或高风险 MCP tool 需要禁用或 approval
MCP resource 注入无效
MCP 调用有 mcp.call、tool.call、safety.evaluate trace
```

## 小阶段 16.7：安全、审批和预算闸门

### 目标

防止 agent loop、Skill 或 MCP 因多步执行绕过风险控制。

### 闸门

```text
pre-plan safety
pre-action capability
pre-action asset resolve
pre-action safety
approval gate
post-output DLP
budget gate
stop gate
```

### 验收

```text
R5+ 动作默认 approval 或 deny
用户拒绝审批后 loop 不换路绕过
预算耗尽后停止
安全阻断写 audit
```

## 小阶段 16.8：Reflection 与能力沉淀

### 目标

任务结束后生成可审核候选，让系统越用越聪明。

### Candidate 类型

```text
memory_candidate
skill_candidate
workflow_template_candidate
policy_improvement_candidate
failure_pattern_candidate
```

### 规则

```text
候选必须有 source task/trace
高风险 skill_candidate 默认 disabled
候选不自动写入高敏感记忆
失败复盘记录原因和可恢复建议
```

### 验收

```text
成功任务能生成 skill/workflow 候选
失败任务能生成 failure pattern
候选有 source 和 confidence
候选不会自动启用高风险执行
```

## 小阶段 16.9：评测与回归

### Eval 分类

```text
workflow_selection
agent_loop_success
agent_budget_stop
supervisor_selection
skill_match_relevance
skill_policy_enforcement
mcp_tool_policy
tool_observation_safety
approval_non_bypass
reflection_candidate_quality
```

### 验收

```text
固定任务走 workflow 的 eval 通过
探索任务进入 agent loop 的 eval 通过
高风险任务不绕过 approval
MCP 注入和 Skill 越权测试通过
pytest、ruff、mypy 保持通过
```

## 总体验收标准

第十六阶段完成时必须满足：

```text
TaskPlan 能表达最终态计划、预算、风险、成功标准
Planner 分层可运行
AgentLoopRunner 有真实 observe/plan/act/evaluate/revise/stop
Skill 匹配和执行受权限、资产、安全约束
MCP 工具/资源/prompt 按不可信边界接入
所有执行步骤有 trace、audit、replay
Reflection 生成候选但不自动启用高风险能力
```

## 不允许通过验收的情况

```text
所有复杂任务都进入 agent
agent loop 无最大步数或 stop_reason
Skill 绕过 ToolRuntime 或 Asset Broker
MCP prompt 覆盖系统指令
用户拒绝审批后系统换路径执行
工具输出未经脱敏直接进入模型上下文
任务失败伪装成功
```

## 与前后阶段关系

第十六阶段把第十三阶段的模式选择转成真实任务执行，把第十五阶段的记忆和知识作为观察来源，把第十四阶段的 Response Composer 作为执行结果出口。第十七阶段会对完整聊天主链路进行综合评测和封版体验验收。

