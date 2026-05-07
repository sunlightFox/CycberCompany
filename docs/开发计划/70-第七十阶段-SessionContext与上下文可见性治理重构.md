# 第七十阶段 - SessionContext 与上下文可见性治理重构

## 阶段背景

当前 `RuntimeContextGateway` 已经能拼出 summary、recent messages、memory、persona、heart、asset handles，但仍有几个关键缺口：

```text
会话来源上下文没有形成统一 SessionContext
多渠道、多会话并发时缺少任务级上下文隔离语义
引用消息、转发消息、外部原文、工作台文件缺少可见性策略
untrusted_context 目前没有真正成体系
上下文预算与裁剪还偏粗
```

这直接影响多轮聊天、深度聊天、记忆调用、群聊/私聊区分，以及后续 MCP / channel / browser 的接入质量。

本阶段目标是参考 Hermes 的 session context 注入和 OpenClaw 的 context visibility 控制，把“模型到底看见什么、为什么看见、哪些不能看见”收敛成稳定契约。

## 核心目标

完成后，上下文系统应满足：

```text
每轮都有统一 SessionContext
并发会话上下文不串线
recent/history/quote/forwarded/workbench/file/tool output 分别受可见性策略控制
trusted 与 untrusted context 明确分层
上下文预算裁剪规则独立可测
渠道来源、聊天对象、sender label、thread 信息可稳定进入 prompt metadata
```

## 破坏性调整

### 新上下文子模块

将 `context_gateway.py` 拆为：

```text
apps/local-api/app/services/context_gateway.py
apps/local-api/app/services/context_session.py
apps/local-api/app/services/context_visibility.py
apps/local-api/app/services/context_budget.py
apps/local-api/app/services/context_untrusted.py
apps/local-api/app/services/context_message_selection.py
```

### 新上下文来源 taxonomy

统一上下文来源：

```text
session.current_channel
session.current_sender
session.current_thread
history.recent_messages
history.quoted_messages
history.forwarded_messages
memory.snapshot
tool.result_summary
browser.page_summary
multimodal.observation
workbench.context_files
external.channel_payload
```

### 可见性决策结构

新增统一决策语义：

```json
{
  "include": true,
  "reason": "mode_all|sender_allowed|quote_override|blocked",
  "source_kind": "history|quote|forwarded|tool_result|workbench",
  "trusted": false
}
```

## 实现要求

### 1. 建立 SessionContext

新增主接口建议为：

```python
class SessionContext:
    session_id: str | None
    conversation_id: str
    channel_profile: str | None
    delivery_mode: str | None
    sender_label: str | None
    thread_ref: str | None
    route_scope: str
```

要求：

```text
从 message envelope、conversation、channel metadata 中统一生成
供 ContextGateway、PromptAssembler、Channel Runtime 共用
不使用进程级全局变量承载会话上下文
若后续需要并发隔离，优先使用 contextvars 语义
```

### 2. 重构可见性治理

影响范围：

```text
apps/local-api/app/services/context_gateway.py
apps/local-api/app/services/chat_ingress.py
apps/local-api/app/services/channel_connectors.py
```

要求：

```text
recent/history/quote/forwarded 分别判断
同 sender、同 session、同 thread 的上下文优先
跨来源原文默认按 untrusted 处理
工作台 context files 默认不是 trusted user instruction
网页正文、文件摘录、OCR、ASR、外部 webhook payload 都进入 untrusted
```

### 3. 独立 Token Budget

从 `RuntimeContextGateway.build()` 中抽出预算逻辑，至少区分：

```text
stable prompt budget
session summary budget
recent history budget
memory budget
untrusted context budget
tool/browser result budget
reserved budget
```

要求：

```text
当前消息永远不裁掉
session summary 优先于低相关长历史
高置信 semantic memory 优先于低相关 episodic
untrusted context 先摘要再截断
```

### 4. 建立上下文诊断 payload

每轮上下文元数据至少输出：

```text
selected_recent_message_ids
omitted_recent_message_count
selected_memory_ids
selected_untrusted_items
trusted_context_token_estimate
untrusted_context_token_estimate
session_context_hash
visibility_decisions
```

## 建议文件调整

### 新增文件

```text
apps/local-api/app/services/context_session.py
apps/local-api/app/services/context_visibility.py
apps/local-api/app/services/context_budget.py
apps/local-api/app/services/context_untrusted.py
apps/local-api/app/services/context_message_selection.py
```

### 缩减文件

```text
apps/local-api/app/services/context_gateway.py
```

## 验收标准

```text
每轮都有稳定 SessionContext metadata
上下文可见性规则可单测，不再散落在 build() 内
trusted / untrusted context 真正分层
跨渠道、跨 sender、跨 thread 的上下文不会默认混入
上下文预算裁剪结果可追踪
```

## 测试计划

```text
apps/local-api/tests/test_phase22_persona_heart_experience.py
apps/local-api/tests/test_phase56_long_term_memory_experience_loop.py
apps/local-api/tests/test_phase61_agent_workbench_loop.py
apps/local-api/tests/test_xiaowu_wechat_multimodal.py
```

新增断言：

```text
quoted / forwarded / workbench / multimodal 内容进入 untrusted
同 session 与跨 session recent message 选择结果不同
当前消息永远保留
prompt metadata 带 session_context_hash
```
