# 第七十三阶段 - Skill、MCP 与渠道桥接运行时重构

## 阶段背景

当前 `skill_plugin.py`、`mcp_runtime.py`、`channel_connectors.py` 三块都已经有基础，但运行时边界还不够清楚：

```text
Skill 安装态与运行态耦合
MCP server 生命周期、bundle 配置、聊天桥接未彻底拆开
channel provider、session context、approval UI、stream bridge 都堆在 connector 层
```

结果是聊天质量容易出现：

```text
Skill 被模型误当作“已执行”
MCP 能力像工具清单，不像真正聊天态能力
微信/飞书等渠道会话上下文与审批交互不够自然
多渠道并发时存在会话串线与状态分散风险
```

本阶段目标是参考 OpenClaw 的 plugin runtime / bundle MCP，以及 Hermes 的 session context / event bridge / MCP messaging bridge，把 Skill、MCP、渠道桥接收成稳定运行时。

## 核心目标

完成后应满足：

```text
Skill 安装、注册、匹配、运行、评测分层
MCP 的 bundle 发现、server lifecycle、conversation bridge 分层
渠道 connector 只做 provider IO，不再承载过多会话语义
微信/飞书等渠道有统一 session context、stream bridge、approval UI 语义
Skill、MCP、channel 都能稳定进入聊天主链路而不污染 chat.py
```

## 破坏性调整

### Skill 运行时拆分

将 `skill_plugin.py` 拆为：

```text
apps/local-api/app/services/skill_installer.py
apps/local-api/app/services/skill_registry.py
apps/local-api/app/services/skill_runtime.py
apps/local-api/app/services/skill_matcher.py
apps/local-api/app/services/skill_eval_runtime.py
```

### MCP 运行时拆分

将 `mcp_runtime.py` 扩展为：

```text
apps/local-api/app/services/mcp_runtime.py
apps/local-api/app/services/mcp_bundle_loader.py
apps/local-api/app/services/mcp_event_bridge.py
apps/local-api/app/services/mcp_conversation_bridge.py
```

### Channel 运行时拆分

将 `channel_connectors.py` 拆为：

```text
apps/local-api/app/services/channel_session_context.py
apps/local-api/app/services/channel_stream_bridge.py
apps/local-api/app/services/channel_approval_bridge.py
apps/local-api/app/services/channels/providers/wechat.py
apps/local-api/app/services/channels/providers/feishu.py
```

## 实现要求

### 1. Skill 安装态与运行态解耦

要求：

```text
install_bundle 只负责来源解析、bundle 校验、文件入库、权限预览、治理分析
skill runtime 只负责匹配、调用、结果结构化、trace
skill eval 单独负责评测
聊天上下文只拿 skills index，不直接读安装态大对象
```

### 2. MCP 加入“会话桥接面”

除 lifecycle / sanitization 外，新增能力：

```text
conversation list/read
events poll/wait
approval list/respond
channel target send
```

要求：

```text
MCP 不只是 server 启停
MCP 需要能作为聊天态桥接能力进入系统
桥接输出仍受 trace / safety / visible filter 控制
```

### 3. 渠道会话上下文独立

参考 Hermes 的 session_context 思路，要求：

```text
每个 inbound/outbound turn 都有独立 channel session context
不要用进程级可变共享态承载当前会话
微信、飞书、群聊、线程、私聊来源区分清晰
sender label、recipient、thread ref 可进入 prompt metadata
```

### 4. 建立 Stream / Approval Bridge

要求：

```text
connector 只做 send / poll / media / bind 等 provider IO
stream bridge 负责把流式回复、tool progress、final visible reply 投递到渠道
approval bridge 负责把审批状态转成渠道按钮或自然语言交互契约
不同渠道允许 UI 表现不同，但语义统一
```

## 建议文件调整

### 新增文件

```text
apps/local-api/app/services/skill_installer.py
apps/local-api/app/services/skill_registry.py
apps/local-api/app/services/skill_runtime.py
apps/local-api/app/services/skill_matcher.py
apps/local-api/app/services/skill_eval_runtime.py
apps/local-api/app/services/mcp_bundle_loader.py
apps/local-api/app/services/mcp_event_bridge.py
apps/local-api/app/services/mcp_conversation_bridge.py
apps/local-api/app/services/channel_session_context.py
apps/local-api/app/services/channel_stream_bridge.py
apps/local-api/app/services/channel_approval_bridge.py
apps/local-api/app/services/channels/providers/wechat.py
apps/local-api/app/services/channels/providers/feishu.py
```

### 缩减文件

```text
apps/local-api/app/services/skill_plugin.py
apps/local-api/app/services/channel_connectors.py
```

## 验收标准

```text
Skill 安装态与运行态解耦
MCP 具备 conversation / events / approval 的桥接能力
channel connector 不再承担全部会话与审批语义
微信/飞书多轮聊天上下文不串线
审批可自然映射到渠道交互
```

## 测试计划

```text
apps/local-api/tests/test_phase38_skill_marketplace_governance.py
apps/local-api/tests/test_phase42_external_platform_actions.py
apps/local-api/tests/test_phase53_channel_wechat_bindings.py
apps/local-api/tests/test_phase54_message_channel_expansion.py
apps/local-api/tests/test_phase60_wechat_ingress_worker.py
```

新增断言：

```text
channel session context 在并发 turn 下不串线
Skill 匹配与 Skill 安装态存储分离
MCP bridge 返回 conversation/events/approval 结构
connector provider 文件中不再含大量会话级业务逻辑
```
