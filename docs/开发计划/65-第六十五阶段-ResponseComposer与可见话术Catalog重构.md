# 第六十五阶段 - ResponseComposer 与可见话术 Catalog 重构

## 现状对齐说明

本阶段已按当前主链路现状对齐落地，不回退到历史时期的旧结构。

- `ResponsePlan` 仍沿用第八十一阶段建立的“可见层 + 内部结构层”双层契约。
- 本阶段负责把可见话术 taxonomy、`response_quality_guard`、渠道可读性约束和 runtime 残留手写文案清理补齐到可验收状态。
- `structured_payload.scenario` 与 `scenario_id` 统一映射到本阶段定义的一级场景集合，不再把 `direct`、`task_completed`、`memory_written` 之类历史别名当作主契约输出。

## 阶段背景

当前用户可见话术分布在 `opening_copy.py`、`contracts.py`、`natural_chat.py`、`chat_quality.py`、`chat_safety.py` 和少量 `chat.py` 补丁中。虽然已有 catalog 雏形，但仍存在旧模板味、机械开头、边界回复偏系统化、场景分类不统一的问题。

本阶段目标是删除旧运行时话术，实现一个新的中文可见话术 catalog 和统一 `ResponseComposer` 质量策略。模型生成负责内容，ResponseComposer 负责可见表达的结构、边界、脱敏、语气和交互契约。

## 统一场景 Taxonomy

新 catalog 只允许以下一级场景：

```text
casual_chat
knowledge_answer
clarification
task_status
approval_required
action_status
tool_boundary
memory
skill
asset
privacy
professional_advice
multimodal
channel_silent
progress_draft
failure_recovery
notification
```

每个场景必须有：

```text
scenario_id
opening variants
tone strategy
quality markers
forbidden visible terms
fallback text
voice metadata
```

## 破坏性调整

### 删除旧话术依赖

运行时代码中删除或替换：

```text
机械开头型模板
“好的，我来...”
“我来继续...”
“记住了。”
“处理结果如下”
“当前状态报告”
“作为 AI...”
```

历史文档、测试证据、旧报告可保留这些词。

### ResponsePlan v4 可见契约

新 turn 的 `ResponsePlan` 至少保留：

```text
summary
plain_text
sections
follow_up_options
action_buttons
tone_metadata
quality_markers
structured_payload.scenario
structured_payload.voice_policy_version
structured_payload.scenario_id
structured_payload.response_quality_guard
```

旧字段如仍有调用方依赖，可短期兼容，但不作为 v4 新契约的主路径。

## 实现要求

### 1. 重构 `opening_copy.py`

影响范围：

```text
services/response-composer/response_composer/opening_copy.py
services/response-composer/response_composer/contracts.py
tests/test_response_composer_reasoning.py
```

要求：

```text
_COPY_VARIANTS 按场景拆分，不再混放所有文案
opening_copy(key, seed, **values) 保持确定性随机
strip_mechanical_openers 升级为 visible_opening_normalizer
conversation_voice_strategy 明确输出 scene / warmth / humor / directness / deescalated
apply_conversation_voice 不强行添加装饰性开头
严格 JSON、表格、代码块不做语气改写
```

### 2. 重构 `ResponseComposer.compose`

要求：

```text
先 strip_reasoning_tags
再 redact_visible_text
再选择 scenario voice strategy
再生成 ResponsePlan
再运行 response_quality_guard
最后输出 plain_text
```

`response_quality_guard` 至少覆盖：

```text
no_internal_terms
no_false_done
boundary_honesty
privacy_redacted
current_message_priority
evidence_required_before_done
strict_format_preserved
```

### 3. 微信可读性重构

微信渠道只做轻量可读性优化：

```text
去掉圆脸 emoji
去掉机械开头
中长结构可少量加入阅读型符号
短回复不强行加符号
JSON / 表格 / 代码块绝不污染
```

多模态回复必须基于具体识别内容：

```text
图片：说看到的具体点，不能只说收到图片
语音：说听到的重点，不能只说收到语音
文件：说读到的摘要，不能只说收到文件
识别不足：直接说不够清楚，不装懂
```

### 4. 可见脱敏统一

所有用户可见回复必须经过：

```text
redact_visible_text
visible_text_guard
ChatVisibleOutputFilter
```

不得出现：

```text
trace_id
approval_id
tool_call_id
task_id
model_safe_text
prompt_snapshot_id
api_key
token
private key
本地敏感路径
```

## 验收标准

```text
运行时代码静态扫描不再命中旧机械话术
ResponsePlan v4 含 response_quality_guard
微信短回复自然，不污染严格格式
复杂回复先结论再结构，不像系统回执
边界回复保留事实和替代路径
隐私阻断不空白
```

## 测试计划

```text
tests/test_response_composer_reasoning.py
apps/local-api/tests/test_phase41_chat_quality_experience.py
apps/local-api/tests/test_phase34_natural_chat_interaction_loop.py
apps/local-api/tests/test_xiaowu_chat_quality.py
apps/local-api/tests/test_xiaowu_wechat_multimodal.py
```

新增静态门禁：

```powershell
rg "好的，我来|我来继续|记住了。|作为 AI|处理结果如下" services apps
```

运行时代码不得命中；测试输入和历史文档可豁免。
