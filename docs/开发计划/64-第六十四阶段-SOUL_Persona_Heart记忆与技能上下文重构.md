# 第六十四阶段 - SOUL/Persona/Heart 记忆与技能上下文重构

## 阶段背景

第六十三阶段重建 prompt 分层后，本阶段继续处理动态身份和上下文质量。当前 persona、heart、memory、skill 进入模型上下文的方式偏“摘要拼接”，缺少明确的稳定身份层、临时心境层、冻结记忆快照和技能索引边界。结果是模型容易把临时情绪当事实、把 Skill 当已执行能力、把记忆当最新指令。

本阶段参考 Hermes Agent 的 SOUL 身份层、记忆快照和 skill index 思路，将“像谁说话”“当前怎样回应”“记得什么”“会哪些方法”拆开，提升聊天自然度和长期一致性。

## 核心目标

完成后，动态上下文应满足：

```text
SOUL 层只定义当前聊天对象的稳定身份和边界
Persona 决定长期表达偏好，不覆盖安全和事实
Heart 只调整节奏、温度、安抚程度，不做事实判断
Memory snapshot 是冻结上下文，只辅助当前消息
Skill index 只说明可复用方法，不直接提供资源或执行权限
所有记忆写入、记忆引用和 skill 引用都有 source / trace
```

## 破坏性调整

### 新动态上下文结构

将原先散落的 persona / heart / memory / skill 文本替换为：

```text
dynamic.persona_snapshot
dynamic.heart_snapshot
dynamic.memory_snapshot
dynamic.skills_index
```

每个 section 必须有独立 metadata：

```json
{
  "snapshot_source": "persona|heart|memory|skill",
  "source_turn_id": "turn_xxx",
  "confidence": 0.0,
  "frozen_for_turn": true,
  "redaction_applied": true
}
```

具体字段可按现有 schema 微调，但语义必须稳定。

## 实现要求

### 1. SOUL 层重构

影响范围：

```text
services/response-composer/response_composer/chat_voice.py
apps/local-api/app/services/soul_manifest.py
apps/local-api/app/services/design_alignment.py
apps/local-api/tests/test_soul_manifest.py
```

SOUL 层必须包含：

```text
当前聊天对象 display_name
不是现实真人，不拥有隐藏账号
能做事必须走系统能力边界
先回应用户当前这句话
能推进就推进，不能推进就说缺什么
高风险动作确认前不声称完成
```

禁止：

```text
不写死 Employee / Company / Boss
不把壳概念写进核心身份
不让 SOUL 绕过 Safety、Approval、Asset Broker
```

### 2. Persona snapshot 重构

Persona 输入模型时只表达稳定风格：

```text
summary
mode
tone_hints
disclosure_hints
style_principles
tone_policy
risk_tone_policy
```

实现约束：

```text
Persona 不允许新增工具权限
Persona 不允许降低风险等级
Persona 不允许声称真人身份
Persona 不允许覆盖用户当前指令
```

### 3. Heart snapshot 重构

Heart 输入模型时只表达当前回应姿态：

```text
mood
user_state
preferred_pace
warmth
humor
directness
deescalation_required
risk_tone_override
confidence
```

实现约束：

```text
用户焦虑时先稳住，再给下一步
用户赶时间时更短更直接
用户发火时降温，不抬杠
高风险场景 humor=none 或 low
Heart 不覆盖 Safety 和 Approval
```

### 4. Memory snapshot 重构

影响范围：

```text
apps/local-api/app/services/context_gateway.py
apps/local-api/app/services/memory.py
services/response-composer/response_composer/chat_voice.py
```

Memory snapshot 必须分层：

```text
semantic：长期偏好和稳定事实
episodic：相关经历摘要
procedural：可复用做法线索
session：当前会话连续性
```

每条记忆进入 prompt 前必须满足：

```text
有 source
有 sensitivity
有 confidence
已脱敏
有相关性理由
不覆盖当前消息
```

记忆写入提示必须继续明确：

```text
记住了什么
后续如何使用
如果用户改口，以新的要求为准
```

### 5. Skill index 重构

Skill index 只提供方法，不提供资源：

```text
可以说“有一个可复用方法适合整理下载文件”
不能说“已经执行下载整理”
不能提供 secret、账号、钱包、硬件明细
真实执行仍走 Task Engine / Skill Engine / Asset Broker / Safety
```

Skill section metadata 必须包含：

```text
skill_id
display_name
source
trust_level
requires_asset_broker
requires_safety
```

## 验收标准

```text
persona / heart / memory / skill 分别进入独立 prompt section
当前消息与记忆冲突时，回复以当前消息为准
用户问“你是真人吗/隐藏账号吗”时自然但诚实
记忆写入回复不再只说“记住了。”
Skill 不再被模型当成已执行工具结果
高风险场景 Heart 降温但不放行
```

## 测试计划

```text
apps/local-api/tests/test_phase22_persona_heart_experience.py
apps/local-api/tests/test_xiaowu_chat_quality.py
apps/local-api/tests/test_phase41_chat_quality_experience.py
apps/local-api/tests/test_phase56_long_term_memory_experience_loop.py
apps/local-api/tests/test_phase61_agent_workbench_loop.py
```

新增断言：

```text
prompt_section_ids 包含 dynamic.persona_snapshot / dynamic.heart_snapshot / dynamic.memory_snapshot / dynamic.skills_index
prompt_sections 不暴露完整 content
response_plan.tone_mode 不会覆盖 safety_boundary
memory 写入回复包含 source 语义和新要求优先语义
```

