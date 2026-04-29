# 第二十二阶段：Persona、Heart 长期一致性与主链路体验质量闭环

## 摘要

第二十二阶段聚焦“长期相处的稳定体验”。当前 PersonaHeartService 已经具备 persona profile、tone policy、disclosure policy、heart snapshot、context summary；ResponseComposer 也能消费 persona/heart 信号生成结构化回复。但完成度分析显示，Persona/Heart 仍偏规则触发，更多影响语气策略，还没有形成长期一致、可评测、可回放的关系状态与体验质量闭环。

本阶段目标是在不影响事实判断、安全判断和权限判断的前提下，提升人格一致性、情绪响应、关系温度、表达风格和失败恢复体验。

本阶段只做后端，不新增 UI。

## 阶段定位

第二十二阶段回答：

```text
系统是否能长期保持同一人格风格
用户焦虑、生气、赶时间、深入讨论时，回应是否稳定适配
Persona 是否只影响表达，不影响权限和安全
Heart 是否能记录状态变化，但不越界拟人
高风险场景是否降低拟人强化，清楚说明 AI 身份和边界
回复质量是否可被 eval，而不是只凭主观感觉
```

## 当前基线判断

| 能力 | 当前完成度 | 主要问题 |
|---|---:|---|
| Persona profile | 约 76% | 可配置，但长期一致性评测不足 |
| Heart state | 约 74% | 规则触发，状态演化有限 |
| ResponseComposer | 约 85% | 结构化强，但表达质量仍可深化 |
| 高风险语气 | 约 82% | 已有边界策略，需更多 eval |
| 体验评测 | 约 78% | 第 17 阶段有主链路 eval，缺风格专项指标 |

## 阶段原则

1. Persona/Heart 只影响表达姿态，不改变事实、安全、权限、审批结果。
2. 高影响事务中降低拟人强化，保持清楚 AI 身份。
3. 陪伴语气不能覆盖风险提示。
4. Persona 配置不能包含 permission/safety bypass 类字段。
5. 长期状态必须有 source、confidence、trace。
6. 所有体验优化必须可回归评测。

## 阶段范围

### 本阶段必须完成

```text
Persona consistency profile
Heart state transition model
Relationship state summary
Tone policy resolver
Risk tone override
Response quality rubric
Persona/Heart eval dataset
Longitudinal conversation replay
```

### 本阶段不做

```text
不新增前端
不做真人身份伪装
不让 Persona 修改 SafetyDecision
不让 Heart 修改 CapabilityDecision
不做心理医疗诊断
不记录不必要的敏感情绪数据
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 22.1 | Persona 一致性 profile | 风格、边界、披露、禁用策略 |
| 22.2 | Heart 状态转移模型 | mood、urgency、pace、relationship |
| 22.3 | TonePolicyResolver | persona + heart + risk + task |
| 22.4 | ResponseComposer 质量规则增强 | 简洁、温度、边界、失败恢复 |
| 22.5 | 长期对话 replay 评测 | 多轮一致性、转向、情绪变化 |
| 22.6 | 高风险与陪伴边界评测 | 安全提示优先、不过度拟人 |

## 小阶段 22.1：Persona 一致性 profile

### 目标

让 Persona 不只是静态 summary，而是可持续约束表达风格的后端策略。

### Profile 字段

```text
persona_profile_id
member_id
style_principles
tone_policy
disclosure_policy
risk_tone_policy
forbidden_claims
allowed_modes
mode_switch_rules
shell_label_mapping
consistency_markers
updated_at
```

### forbidden_claims

```text
claiming_human_identity
claiming_hidden_tool_access
claiming_unapproved_execution
claiming_secret_access
overriding_safety_policy
```

### 验收

```text
Persona profile 可查询、可更新、可 trace
Persona policy 禁止权限/安全覆盖字段
Persona summary 进入上下文但不暴露后台组织结构
聊天页语义仍不显示组织/壳信息
```

## 小阶段 22.2：Heart 状态转移模型

### 目标

让 Heart 从单轮规则判断升级为跨轮状态演化。

### 状态字段

```text
snapshot_id
member_id
mood
urgency
user_state
preferred_pace
relationship_temperature
companionship_intensity
deescalation_required
deescalation_boundary
risk_tone_override
confidence
source_turn_id
trace_id
```

### 转移因子

```text
current_text_signal
previous_heart_state
dialogue_state
task_failure
user_correction
approval_or_safety_scene
memory_preference
```

### 验收

```text
连续焦虑输入会提升 deescalation_required
用户要求简洁会降低 verbosity
任务失败后语气更负责但不虚假承诺
Heart 不改变 SafetyDecision
```

## 小阶段 22.3：TonePolicyResolver

### 目标

把 persona、heart、risk、task、response scenario 合并为统一表达策略。

### 输入

```text
persona_summary
heart_summary
risk_level
safety_decision
intent_decision
mode_decision
task_status
failure_state
approval_state
```

### 输出

```text
tone_mode
conciseness
warmth
directness
technical_depth
humor_allowed
anthropomorphic_level
disclosure_required
safety_notice_required
reason_codes
```

### 验收

```text
高风险场景 anthropomorphic_level 降低
approval 场景语气清楚且等待确认
失败场景说明失败点和下一步
普通闲聊仍保持轻量自然
```

## 小阶段 22.4：ResponseComposer 质量规则增强

### 目标

让结构化回复不只“格式正确”，还要“体验稳定”。

### 质量规则

```text
answer_directness
context_continuity
internal_leakage_absent
capability_boundary_honesty
safe_next_step
failure_recoverability
persona_consistency
heart_appropriateness
```

### ResponsePlan 增强字段

```text
tone_mode
quality_markers
boundary_notice
continuity_refs
deescalation_notice
user_next_step
```

### 验收

```text
普通聊天仍可简洁 plain_text
复杂任务返回 summary + artifact_refs + next_step
approval 场景有明确确认文案
不能暴露内部模块名或 prompt
```

## 小阶段 22.5：长期对话 replay 评测

### 目标

用多轮回放验证人格和关系状态，而不是单轮 snapshot。

### 必测 case

```text
连续五轮方案讨论
用户临时赶时间
用户纠正语气偏好
用户焦虑后恢复平静
任务失败后继续协作
高风险审批场景
用户要求系统假装真人
```

### 指标

```text
persona_consistency_score
tone_adaptation_score
boundary_honesty_rate
deescalation_success_rate
over_anthropomorphic_violation_count
internal_leakage_count
```

### 验收

```text
长期 replay 结果可保存为 eval evidence
人格一致性低于阈值时阻断封版报告 go
高风险边界违规为 0
```

## 小阶段 22.6：高风险与陪伴边界评测

### 目标

确保“温暖”不削弱“安全”。

### 必测场景

```text
删除文件请求
外部提交请求
账号登录请求
钱包签名请求
用户要求忽略安全提醒
用户情绪激动要求立即执行
用户要求透露 secret
```

### 验收命令

```text
.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase22_persona_heart_experience.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

## 阶段总验收标准

第二十二阶段完成时必须满足：

```text
Persona 长期一致性有 profile、resolver、eval
Heart 支持跨轮状态转移和 trace
TonePolicyResolver 合并 persona/heart/risk/task 场景
ResponseComposer 输出质量可评测
高风险场景安全提示优先于陪伴语气
Persona/Heart 不改变 Safety、Approval、Capability 结果
```
