# 第八十八阶段 - 渠道可靠性与NoTurn治理闭环

## 阶段定位

聊天质量再好，如果外部消息进不来、丢 turn、串 conversation，用户也会直接判定系统“不成熟”。  
本阶段专门处理渠道可靠性，目标是把微信、飞书等外部入口的核心故障从“偶发体验问题”升级为“门禁级治理对象”。

## 直接依赖

```text
docs/开发计划/78-第七十八阶段-会话与渠道语义统一.md
docs/开发计划/84-第八十四阶段-聊天主链路测试与验收矩阵.md
docs/开发计划/85-第八十五阶段-聊天主链路实施任务拆解.md
docs/开发计划/86-第八十六阶段-ChatRuntime兼容壳瘦身与主链路唯一化.md
```

## 阶段目标

```text
治理 no_turn
治理 duplicate inbound
治理错误会话复用与串 conversation
治理 inbound accepted 但 downstream 没形成可见 turn
把渠道可靠性接入 release gate
```

## 关键问题

从现有测试证据看，渠道链路还存在 P0 级问题：

```text
消息到达但没有形成 chat turn
入站、配对、worker、delivery 之间缺少统一失败归因
真实链路回归能发现 no_turn，但还没有强制门禁
```

## 本阶段范围

### 必须完成

```text
建立 no_turn 统一诊断分类
建立 inbound -> turn -> delivery 的全链路关联键
建立渠道失败归因 taxonomy
把微信 / 飞书去重与 session 复用回归纳入固定门禁
```

### 明确不做

```text
不扩展新渠道种类
不重做聊天主链路核心架构
不做新的渠道 UI
```

## 主要模块

```text
apps/local-api/app/services/channel_ingress_runtime.py
apps/local-api/app/services/channel_session_router.py
apps/local-api/app/services/channel_stream_bridge.py
apps/local-api/app/services/wechat_gateway.py
apps/local-api/app/services/feishu_gateway.py
apps/local-api/app/services/channel_session_context.py
apps/local-api/app/services/channel_session_semantics.py
apps/local-api/app/services/chat_run_ledger.py
apps/local-api/app/services/release.py
```

## 核心治理对象

### 1. no_turn

定义：

```text
渠道消息已被接收或轮询到
但未成功形成可追踪的 chat turn
```

### 2. orphan_turn

定义：

```text
turn 已创建
但没有进入 queue / runtime / delivery 的后续阶段
```

### 3. duplicate_turn

定义：

```text
同一 inbound event 因 dedupe 失败生成多个 turn
```

### 4. wrong_conversation_reuse

定义：

```text
不同 peer、thread 或 binding 被错误复用到同一 conversation
```

## 实施拆解

### 88.1 渠道事件全链路关联键统一

目标：

```text
每个 inbound event 都能关联到 turn、conversation、delivery
```

交付：

```text
inbound_event_id
dedupe_key
channel_account_id
peer key
conversation_id
turn_id
delivery record
```

### 88.2 no_turn 归因与告警

目标：

```text
任何 no_turn 都能知道卡在配对、去重、worker、runtime 还是 delivery
```

交付：

```text
no_turn diagnostic taxonomy
failure reason codes
release summary 输出 no_turn breakdown
```

### 88.3 去重与 conversation 复用治理

目标：

```text
同一 peer 正确复用
不同 peer 不串线
重复事件只保留一条 turn
```

交付：

```text
session reuse contract
duplicate inbound gate
conversation binding consistency checks
```

### 88.4 渠道可靠性门禁

目标：

```text
把渠道可靠性问题从“测试报告备注”升级成 release gate 阻断项
```

交付：

```text
no_turn rate
duplicate turn rate
delivery binding completeness
channel continuity acceptance
```

## schema / migration

如缺失则补：

```text
channel inbound correlation fields
dedupe and peer binding fields
delivery lifecycle timestamps
channel diagnostics summary fields
```

## 测试与验收

### 最小测试集

```text
apps/local-api/tests/test_phase78_session_channel_semantics.py
apps/local-api/tests/test_phase54_wechat_gateway_full_link.py
apps/local-api/tests/test_phase66_feishu_channel.py
apps/local-api/tests/test_phase84_chat_mainline_acceptance_matrix.py
```

### 本阶段新增测试重点

```text
wechat no_turn detection
feishu no_turn detection
duplicate inbound 不生成第二个 turn
同一 peer 续接同一 session / conversation
不同 peer 不复用 conversation
delivery 成功与 turn completed 之间可回链
```

## 完成定义

```text
no_turn 有统一诊断与归因
duplicate inbound 与串 conversation 有固定回归
微信 / 飞书真实链路可靠性进入 release gate
渠道入口故障不再只停留在 smoke 报告描述层
```
