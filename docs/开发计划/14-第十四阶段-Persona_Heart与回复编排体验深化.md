# 第十四阶段：Persona、Heart 与回复编排体验深化

## 摘要

第十四阶段把“系统会回答”升级为“系统以正确的人格、情绪节奏和产品结构回答”。它聚焦三件事：

```text
Persona Engine：稳定身份、语气策略、披露策略、壳标签映射
Heart Service：情绪识别、关系温度、紧急程度、陪伴强度、降温边界
Response Composer：把模型、任务、工具、记忆、安全和人格信号编排成稳定用户回复
```

本阶段只做后端服务、schema、API、trace、测试和 eval，不新增前端实现。

## 阶段定位

聊天体验的最终质量不只来自模型。模型可以生成文字，但产品必须控制：

```text
什么时候简洁
什么时候展开
什么时候温暖
什么时候克制
什么时候用表格
什么时候给行动按钮契约
什么时候明确等待确认
什么时候降低拟人感并强调 AI 身份
```

Persona / Heart 负责“该用什么姿态”，Response Composer 负责“最终呈现什么结构”。Safety 和 Capability 的判断永远高于语气。

## 当前基线判断

| 模块 | 当前完成度判断 | 主要缺口 |
|---|---:|---|
| Persona Engine | 约 45% | 可配置性、披露策略、上下文摘要不够完整 |
| Heart Service | 约 45% | 情绪/关系/紧急度仍偏 deterministic |
| Response Composer | 约 55% | structured payload 覆盖不足，任务/失败/approval 场景需补齐 |
| Persona/Heart 注入 | 偏弱 | ChatService 和 ContextGateway 的接入还需统一 |

## 阶段原则

1. Heart 只影响表达姿态，不影响事实和安全判断。
2. Persona 只定义身份、语气、披露和壳映射，不获得额外权限。
3. Response Composer 是唯一用户可见回复出口。
4. 高风险、高影响、审批和失败场景必须降温，保持清楚、克制、可审计。
5. 不使用情感强化来掩盖限制、风险或 AI 身份。
6. 聊天页仍不显示组织、壳、部门树或后台结构。

## 阶段范围

### 本阶段必须完成

```text
PersonaProfile 最终 schema
TonePolicy / DisclosurePolicy / RiskTonePolicy
HeartSignal schema
HeartService 情绪和紧急度识别
PersonaEngine 模式选择和摘要生成
ContextGateway persona/heart 注入
ResponsePlan 扩展和全路径使用
approval/failure/task/memory/tool 场景编排
高风险降温策略
persona/heart/composer eval
```

### 本阶段不做

```text
不新增人格编辑 UI
不新增前端卡片组件
不把 Persona 变成权限系统
不让 Heart 决定是否允许高风险动作
不新增新壳类型
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 14.1 | PersonaProfile 与策略 schema | persona、tone、disclosure、risk tone |
| 14.2 | HeartSignal 与情绪状态 | mood、urgency、relationship、deescalation |
| 14.3 | Context Gateway 注入 | persona/heart summary 进入 ContextPacket |
| 14.4 | ResponsePlan 最终扩展 | title、summary、sections、actions、notices |
| 14.5 | 场景化回复编排 | direct、task、tool、approval、failure、memory |
| 14.6 | 高风险降温与 AI 披露 | 安全场景克制表达和清楚边界 |
| 14.7 | API、trace 与审计 | persona/heart/composer 查询与证据链 |
| 14.8 | 评测与回归 | 语气、结构、安全、泄密、越界 eval |

## 小阶段 14.1：PersonaProfile 与策略 schema

### 目标

把 Persona 从静态文案升级为可配置、可查询、可注入上下文的后端策略对象。

### PersonaProfile

建议字段：

```text
persona_profile_id
member_id
display_name
base_traits
communication_style
tone_policy
disclosure_policy
risk_tone_policy
shell_label_mapping_ref
allowed_modes
default_mode
created_at
updated_at
```

### TonePolicy

```text
conciseness
warmth
humor
directness
formality
proactiveness
technical_depth
```

### DisclosurePolicy

```text
ai_identity_disclosure
capability_boundary_disclosure
uncertainty_disclosure
memory_usage_notice
tool_usage_notice
```

### RiskTonePolicy

```text
approval_scene_tone = clear_and_calm
security_block_scene_tone = firm_and_explanatory
failure_scene_tone = accountable_and_actionable
high_impact_scene_tone = low_anthropomorphic
```

### 验收

```text
persona profile 可创建、读取、更新
profile 不包含权限放行字段
壳标签映射只引用 ShellRuntime，不写死公司语义
ContextGateway 能生成 persona summary
```

## 小阶段 14.2：HeartSignal 与情绪状态

### 目标

让 Heart 输出结构化信号，而不是固定“温和、专业、结论先行”。

### HeartSignal

建议字段：

```json
{
  "mood": "anxious",
  "urgency": "medium",
  "user_state": "needs_reassurance",
  "relationship_temperature": "familiar",
  "preferred_pace": "step_by_step",
  "companionship_level": 0.62,
  "deescalation_required": false,
  "risk_tone_override": null,
  "confidence": 0.76
}
```

### 识别输入

```text
当前用户输入
最近消息摘要
conversation working state
persona profile
安全风险信号
用户历史偏好
```

### 验收

```text
焦虑、愤怒、赶时间、开心、普通中性输入能产生不同 HeartSignal
HeartSignal 有 confidence
高风险场景可产生 deescalation_required
Heart 不修改 SafetyDecision
```

## 小阶段 14.3：Context Gateway 注入

### 目标

把 Persona 和 Heart 作为上下文摘要注入模型，但不泄露后台组织结构和内部实现。

### 注入内容

```text
当前成员显示名
简短 persona 摘要
当前 tone hints
disclosure hints
heart summary
risk tone hints
```

### 禁止注入

```text
完整 persona 配置 JSON
组织结构
壳层世界观解释
内部策略优先级
secret 或私有系统设置
```

### 验收

```text
ContextPacket 包含 persona_summary 和 heart_summary
聊天页仍不显示组织和壳
模型上下文不出现内部策略原文
persona/heart 注入有 trace span
```

## 小阶段 14.4：ResponsePlan 最终扩展

### 目标

把 ResponsePlan 打磨成未来 UI 稳定可消费的结构化契约。

### ResponsePlan 字段

```text
title
summary
plain_text
sections
tables
code_blocks
action_buttons
approval_prompt
task_status
artifact_refs
safety_notice
memory_notice
tool_notice
follow_up_options
tone_metadata
redaction_summary
trace_refs
```

### 输出形态

```text
plain text fallback
structured payload
stream delta
final completed payload
UI action contract
```

### 验收

```text
普通 direct 回复有 plain_text
复杂回复有 sections 或 tables
任务回复有 task_status 和 artifact_refs
approval 回复有 approval_prompt
安全阻断有 safety_notice
```

## 小阶段 14.5：场景化回复编排

### 目标

确保所有用户可见路径都经过 composer，而不是模型裸输出或 service 固定字符串散落。

### 场景矩阵

| 场景 | Composer 输出要求 |
|---|---|
| direct 成功 | 简洁或结构化 plain_text，符合 persona/heart |
| 复杂对话 | sections、summary、follow_up_options |
| 任务创建 | task_status、计划摘要、可回放引用 |
| 任务完成 | 完成摘要、artifact_refs、下一步 |
| 工具失败 | 失败点、已尝试动作、恢复建议 |
| approval required | approval_prompt、风险说明、等待用户确认 |
| safety deny | safety_notice、原因、可替代路径 |
| memory written | memory_notice，必要时可见 |
| memory conflict | 说明可能冲突并请求确认或标记候选 |

### 验收

```text
所有用户可见出口都可定位到 response.compose span
失败场景不暴露异常栈
approval 场景不使用诱导性措辞
任务场景不丢 artifact refs
```

## 小阶段 14.6：高风险降温与 AI 披露

### 目标

在支付、转账、删除、外发、系统修改、钱包签名、法律/医疗/金融高影响内容等场景中降低拟人强化，保持边界清楚。

### 降温规则

```text
不使用亲密关系施压
不使用“相信我直接做”式措辞
明确说明需要确认或不能执行
明确说明风险类别
提供可逆替代步骤
必要时披露 AI 身份和能力边界
```

### 验收

```text
高风险审批文案克制、清楚、可审计
Heart 的温暖语气不能覆盖 safety_notice
Persona 不改变 approval_required 或 deny
eval 覆盖情感诱导禁用场景
```

## 小阶段 14.7：API、trace 与审计

### 目标

提供未来系统管理和调试可用的后端契约。

### API 契约

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/persona/profiles/{member_id}` | 获取成员 persona profile |
| PATCH | `/api/persona/profiles/{member_id}` | 更新 persona profile |
| GET | `/api/heart/state/{member_id}` | 获取当前 heart 摘要 |
| POST | `/api/response-composer/preview` | 后端调试/测试用回复预览 |

如果现有路由命名不同，可以按实际项目规范调整，但 schema 语义必须稳定。

### Trace

```text
persona.load
persona.mode_select
heart.evaluate
response.plan
response.compose
response.redact
```

### 验收

```text
persona 更新写 audit
heart evaluate 写 trace
response plan 可在 trace 中定位
secret 不进入 composer payload
```

## 小阶段 14.8：评测与回归

### Eval 分类

```text
persona_tone_match
heart_signal_accuracy
response_structure_completeness
approval_prompt_clarity
safety_notice_presence
failure_message_actionability
memory_notice_correctness
no_secret_leakage
no_internal_structure_leakage
high_risk_deescalation
```

### 验收指标

| 指标 | 目标 |
|---|---:|
| Response Composer 覆盖率 | 1.00 |
| 高风险降温通过率 | 1.00 |
| secret 泄漏数 | 0 |
| 内部结构泄漏数 | 0 |
| approval 文案清晰率 | >= 0.95 |

### 验收

```text
persona/heart/composer eval 可运行
所有安全相关 eval 必须通过
普通聊天和复杂聊天都有语气回归
pytest、ruff、mypy 保持通过
```

## 总体验收标准

第十四阶段完成时必须满足：

```text
PersonaProfile、TonePolicy、DisclosurePolicy、RiskTonePolicy 后端可用
HeartSignal 可根据输入生成结构化信号
ContextGateway 注入 persona/heart summary
ResponsePlan 覆盖 direct、task、tool、approval、failure、memory 场景
高风险场景降温且清楚披露边界
所有用户可见回复可追溯到 response.compose
persona/heart 不改变权限和安全判断
```

## 不允许通过验收的情况

```text
Response Composer 被任一用户可见路径绕过
Heart 改变 SafetyDecision
Persona profile 获得权限放行字段
高风险审批文案诱导用户同意
聊天上下文泄露组织/壳内部结构
secret、token、私钥、cookie 出现在 response payload 或 trace
```

## 与前后阶段关系

第十四阶段消费第十二阶段的聊天体验要求和第十三阶段的决策输出，把“怎么答”稳定下来。第十五阶段会为 composer 提供更可信的记忆和知识来源；第十六阶段会让任务、Skill、MCP 的执行结果以同一套 ResponsePlan 输出给用户。

