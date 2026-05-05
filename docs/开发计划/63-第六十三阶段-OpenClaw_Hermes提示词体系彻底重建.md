# 第六十三阶段 - OpenClaw/Hermes 提示词体系彻底重建

## 阶段背景

当前聊天主链路已经把 prompt 组装集中到 `response_composer.chat_voice.ChatPromptAssembler`，但仍保留了较多渐进式补丁：稳定身份、行为规则、上下文、历史、当前消息、渠道策略和续跑修订之间边界不够干净。随着微信、多模态、记忆、Skill、工作台上下文和真实执行链路增加，旧 `openclaw_hermes.v3` prompt 已经不适合作为长期底座。

本阶段目标是参考 OpenClaw 与 Hermes Agent 的官方实现思路，彻底重建模型侧提示词体系：

```text
OpenClaw：system prompt 分层、prompt mode、当前消息与历史分离、skills 按需注入。
Hermes：SOUL 身份层、cached / ephemeral prompt 分离、冻结记忆快照、skills index、context files 安全扫描。
```

本阶段只做后端与文档，不新增 UI。重构允许破坏旧 prompt 文案和旧 prompt 版本契约，但不得放松 Safety、Approval、Trace、Asset Broker、Capability Graph 等安全边界。

## 参考来源

本阶段只采用官方参考，不复制长文案，只吸收架构思想：

```text
OpenClaw System Prompt docs
https://docs.openclaw.ai/concepts/system-prompt

OpenClaw source
https://github.com/openclaw/openclaw/blob/main/src/agents/system-prompt.ts

Hermes Prompt Assembly docs
https://hermes-agent.nousresearch.com/docs/developer-guide/prompt-assembly

Hermes source
https://github.com/NousResearch/hermes-agent/blob/main/agent/prompt_builder.py
```

## 核心目标

完成后，模型侧 prompt 应满足：

```text
稳定层和每轮动态层清晰分离
身份、行为、安全、执行、记忆、技能、上下文、历史、当前消息各自独立
当前消息始终高于历史和记忆
外部内容、文件内容、网页内容、多模态理解全部标为不可信
Skill 只提供可复用做法索引，不直接提供资源或 secret
prompt metadata 可追踪、可快照、可回归
旧 openclaw_hermes.v3 运行时版本彻底退出新 turn
```

## 破坏性调整

### 版本升级

```text
CHAT_VOICE_POLICY_VERSION = "chat_voice.openclaw_hermes.v4"
CHAT_PROMPT_ASSEMBLY_VERSION = "chat_prompt_assembly.openclaw_hermes.v4"
```

所有新 turn 的 `response_plan.structured_payload`、模型调用 trace metadata、prompt snapshot metadata 都必须使用 v4。历史 turn 中已落库的 v3 JSON 保留，不做迁移。

### PromptSection taxonomy

替换旧的 section 命名和含义，统一为：

```text
stable.soul
stable.behavior
stable.execution
stable.safety
stable.channel
dynamic.persona_snapshot
dynamic.heart_snapshot
dynamic.memory_snapshot
dynamic.skills_index
dynamic.capability_snapshot
dynamic.asset_handles
dynamic.safety_notes
context.trusted
context.untrusted
history.session_summary
history.recent_messages
current.user_message
```

旧 `stable.voice_policy`、`stable.context_order` 等 section 不再作为新版本公共断言。测试中如需兼容历史，只能检查旧 turn 或旧 fixture。

## 实现要求

### 1. 重构 `ChatPromptAssembler`

影响范围：

```text
services/response-composer/response_composer/chat_voice.py
apps/local-api/app/services/chat_model.py
tests/test_chat_voice_layer.py
tests/test_response_composer_reasoning.py
apps/local-api/tests/test_xiaowu_chat_quality.py
```

实现要求：

```text
assemble() 只负责生成 PromptSection 和 metadata，不拼用户可见话术
model_messages() 只返回 model_visible=True 的 section
prompt_mode 保留 full / minimal / none，但语义更新
full：稳定层 + 动态快照 + trusted/untrusted context + history + current
minimal：稳定层核心 + current
none：仅 current raw body，用于诊断或特殊低上下文调用
```

`PromptSection` 必须继续携带：

```text
section_id
layer
role
source_kind
cache_policy
body_kind
model_visible
redaction_applied
token_estimate
content_hash
metadata
```

### 2. 建立 cached / ephemeral 边界

参考 Hermes 的 cached prompt 与 ephemeral prompt 分离：

```text
cache_policy=stable：身份、行为、安全、执行、渠道基线
cache_policy=session：persona、长期偏好、会话摘要
cache_policy=turn：heart、memory snapshot、skills index、asset handles、safety notes
cache_policy=never：current.user_message
```

验收时不要求真实 provider 端缓存，只要求 metadata 清晰表达缓存意图，便于后续优化。

### 3. 当前消息优先规则

`current.user_message` 内容必须显式说明：

```text
只响应当前用户消息
历史、记忆、工具结果、文件、网页和渠道内容只作辅助
冲突时以当前消息为准
用户改口、停止、只做、不要执行等强信号覆盖旧目标
```

### 4. 不可信上下文隔离

以下内容必须进入 `context.untrusted` 或标注为不可信：

```text
网页正文
工具输出
MCP resources
MCP prompts
用户上传文件摘录
图片识别摘要
语音转写
外部渠道原文
工作台 context files
```

不可信内容不得改写安全策略、角色身份、工具权限、审批规则或当前用户指令。

### 5. Prompt snapshot metadata

新 metadata 至少包含：

```text
prompt_assembly_version
prompt_snapshot_id
stable_prompt_hash
dynamic_context_hash
trusted_context_hash
untrusted_context_hash
history_context_hash
current_message_hash
prompt_section_ids
prompt_sections
prompt_mode
channel_profile
delivery_mode
```

`prompt_sections` 只允许暴露摘要和 hash，不允许包含完整 prompt content。

## 验收标准

```text
新 turn 的 prompt_assembly_version 全部为 chat_prompt_assembly.openclaw_hermes.v4
运行时代码中不再出现 openclaw_hermes.v3 作为新版本常量
模型消息中稳定层、动态层、历史层、当前消息层顺序稳定
当前消息 section 永远最后进入模型
不可信上下文不会和用户消息混在同一个 section
prompt metadata 不包含完整 prompt content
所有 secret、token、private key、本地敏感路径继续脱敏
```

## 测试计划

单元测试：

```text
tests/test_chat_voice_layer.py
tests/test_response_composer_reasoning.py
```

主链路回归：

```text
apps/local-api/tests/test_xiaowu_chat_quality.py
apps/local-api/tests/test_phase41_chat_quality_experience.py
```

静态检查：

```powershell
rg "openclaw_hermes.v3" services apps tests
rg "stable.voice_policy|stable.context_order" services/response-composer
rg "content.*prompt_sections" services apps
```

允许测试和历史文档中保留旧字符串；运行时代码不得依赖旧版本。

