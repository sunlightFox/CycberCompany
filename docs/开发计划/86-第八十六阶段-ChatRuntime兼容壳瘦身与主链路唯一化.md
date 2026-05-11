# 第八十六阶段 - ChatRuntime兼容壳瘦身与主链路唯一化

## 阶段定位

第八十五阶段解决的是“按批次怎么做”。第八十六阶段开始真正进入收口执行，第一刀先切运行时权威：

```text
谁拥有聊天主链路入口
谁推进 turn 状态机
谁有权决定 direct / route / model / task 分流
哪些 compat shell 只能转发、不能继续长业务逻辑
```

本阶段不追求一次删光旧代码，目标是把“真正主链路”和“兼容层”分开，并禁止兼容层继续长肉。

## 直接依赖

```text
docs/开发计划/76-第七十六阶段-聊天主链路完善总计划.md
docs/开发计划/77-第七十七阶段-聊天运行时收口与主链路统一.md
docs/开发计划/84-第八十四阶段-聊天主链路测试与验收矩阵.md
docs/开发计划/85-第八十五阶段-聊天主链路实施任务拆解.md
```

## 阶段目标

```text
ChatRuntime 成为唯一主执行入口
ChatTurnExecutionOrchestrator 成为唯一 turn 推进器
chat.py 退化为 compat facade
session_runtime 只保留 session 语义，不再散落聊天业务
兼容壳允许存在，但必须只做委派和适配
```

## 核心问题

当前主链路的问题不是没有模块，而是运行时权威不唯一：

```text
chat.py 仍持有大量直答与兼容逻辑
chat_turn_execution.py 已经是主编排器，但没有彻底拿到唯一权威
部分 direct / deterministic / fallback reply 仍在 compat 壳内提前结束
系统层知道自己有 compat shell，但运行时还没真正把其边界锁死
```

## 范围

### 本阶段必须完成

```text
收口 run_turn 主入口
收口 cancel / retry / recover 的统一入口
把 turn 生命周期事件发射集中到 orchestrator
把 compat shell 标记、诊断和删除窗口写进 runtime 诊断输出
明确 direct_response_chain / route_dispatch_chain / model_execution_chain 的 ownership
```

### 本阶段明确不做

```text
不处理渠道 no_turn 可靠性
不重写 ResponseComposer 文案体系
不改变工具风险分类
不新增新的 route-specific 可见话术
```

## 主要模块

```text
services/chat-runtime/chat_runtime/runtime.py
apps/local-api/app/services/chat.py
apps/local-api/app/services/chat_turn_execution.py
apps/local-api/app/services/session_runtime.py
apps/local-api/app/services/turn_execution.py
apps/local-api/app/services/turn_recovery.py
apps/local-api/app/services/chat_mainline_readiness.py
```

## 改造原则

### 1. compat shell 只准转发

兼容壳允许保留：

```text
旧 API 入口
参数适配
旧诊断接口兼容
旧测试辅助桥接
```

兼容壳禁止继续承载：

```text
新的 direct reply 业务分支
新的 route-specific 长文案
新的 task / tool / approval 旁路
新的状态机推进逻辑
```

### 2. 入口唯一，出口仍可兼容

本阶段先锁入口：

```text
SessionRuntime -> ChatRuntime -> ChatTurnExecutionOrchestrator
```

允许暂时保留部分旧输出适配，但不允许再产生第二套执行入口。

### 3. 状态机写点唯一

以下内容必须只有一个主写点：

```text
turn started
context started / ready
intent detected
mode selected
route selected
response completed / failed
turn completed / failed / recovered
```

## 实施拆解

### 86.1 运行时入口盘点与 compat 壳标记

目标：

```text
明确哪些模块是 runtime_native
明确哪些模块是 compat_shell
明确哪些模块是 helper
```

交付：

```text
runtime topology 诊断输出增强
compat shell 删除窗口字段
host_files / delegates_to / allowed_to_grow 元数据补齐
```

### 86.2 ChatRuntime 主入口唯一化

目标：

```text
create_turn / run_turn / stream / cancel / retry / recover 统一从 ChatRuntime 进入
```

交付：

```text
ChatRuntime 对外 contract 固定
session_runtime 只做 session 级入口，不再推进 turn 业务
chat.py 不再绕过 ChatRuntime 直接走旧路径
```

### 86.3 Turn 状态机写点收口

目标：

```text
turn 生命周期推进只由 ChatTurnExecutionOrchestrator 负责
```

交付：

```text
事件统一在 orchestrator 发射
turn 关键状态写库路径统一
recovery / cancel / retry 的终态一致
```

### 86.4 compat 壳禁长策略

目标：

```text
在代码和测试层明确 compat 壳不可继续扩张
```

交付：

```text
静态扫描规则
运行时诊断规则
测试断言 compat shell allowed_to_grow = false
```

## schema / migration

本阶段原则上不新增业务表，但允许补充以下诊断字段或元数据契约：

```text
runtime topology diagnostic metadata
compat shell cleanup window metadata
turn lifecycle diagnostic tags
```

## 测试与验收

### 最小测试集

```text
apps/local-api/tests/test_phase77_chat_runtime_closure.py
apps/local-api/tests/test_phase70_runtime_topology.py
apps/local-api/tests/test_phase76_chat_mainline_control_plane.py
apps/local-api/tests/test_phase60_turn_recovery.py
```

### 本阶段新增测试重点

```text
ChatRuntime 是唯一 run_turn 入口
chat.py 仅保留 compat facade 所需公开方法
compat shell 不再包含新增业务分支
turn.started -> turn.completed 时间线唯一
cancel / retry / recover 不产生第二套生命周期事件
```

## 完成定义

满足以下条件，视为第八十六阶段完成：

```text
ChatRuntime 成为唯一真实主入口
chat.py 被诊断为 compat_shell，且 allowed_to_grow = false
turn 状态机主写点收口到 orchestrator
session_runtime 不再承载聊天业务逻辑
相关回归测试全绿
```
