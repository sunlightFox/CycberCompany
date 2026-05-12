# 第九十六阶段 - AgentLoop主链路加厚与观察重规划闭环

## 阶段定位

第九十六阶段不是继续补更多工具名录，也不是再扩新业务域。

它只回答一个更硬的问题：

**系统的 agent 模式，是否已经从“有 agent 字段、有 facade、有部分重规划能力”，变成了真正默认可跑的主链路？**

本阶段要把现有 agent 相关能力从分散存在，推进为统一、稳定、可回放的执行闭环：

```text
观察
选择下一步
执行
记录结果
复盘
必要时重规划
在预算或边界命中时可靠停止
```

## 直接依赖

```text
docs/开发计划/19-第十九阶段-模型辅助规划与Agent智能执行深化.md
docs/开发计划/25-第二十五阶段-真实模型Planner与自适应Agent执行质量提升.md
docs/开发计划/76-第七十六阶段-聊天主链路完善总计划.md
docs/开发计划/80-第八十阶段-聊天内工具调用闭环.md
docs/开发计划/82-第八十二阶段-记忆写入与运行账本统一.md
docs/开发计划/93-第九十三阶段-运行中改口Steering与连续执行控制.md
docs/开发计划/94-第九十四阶段-失败经验沉淀与自增长治理闭环.md
docs/开发计划/95-第九十五阶段-聊天代理成熟度封版验收与长期运行治理.md
```

## 阶段目标

```text
把 agent loop 从 facade + 局部能力升级为唯一权威执行循环
统一 observe / decide / act / reflect / replan / stop 的 typed contract
让 task agent runtime、chat task handoff、browser observation、tool result 都进入同一循环账本
把预算、审批、改口、失败恢复、人工接管都变成 loop 内一等公民
形成能稳定复现的 agent loop 评测集
```

## 当前核心问题

目前项目里与 agent 执行相关的东西已经不少，但仍偏分散：

```text
TaskAgentRuntime 与 TaskAgentLoop 仍偏薄
真正执行复杂度主要压在 TaskEngine 内部
观察结果、下一步决策、重规划、停止原因还没有形成统一主干证据
agent 模式和 workflow / chat tool loop 的边界虽然存在，但默认执行肌肉还不够强
```

结果就是：

```text
系统具备 agent 能力雏形
但还不具备像成熟执行代理那样稳定、自解释、自纠偏的默认循环
```

## 本阶段范围

### 必须完成

```text
定义统一 AgentLoop typed state
统一 observation、next_action、replan_delta、stop_reason、handoff_reason 契约
把 runtime 真正收口到单一 agent loop 执行入口
让工具结果、浏览器页面状态、知识检索、失败经验、steering 都成为标准观察源
把审批等待、预算命中、人工接管、边界阻断都纳入标准 stop / pause 语义
补齐 replay 与 diagnostics 的 loop 时间线
```

### 明确不做

```text
不扩展新的 MCP / Skill 品类
不新增 UI 工作台
不把 workflow 任务强行全部改写成 agent 模式
不绕过 Asset Broker / Capability Graph / Safety / Trace
```

## 主要模块

```text
apps/local-api/app/services/task_agent_runtime.py
apps/local-api/app/services/task_agent_loop.py
apps/local-api/app/services/tasks.py
apps/local-api/app/services/task_resume_runtime.py
apps/local-api/app/services/chat_turn_execution.py
apps/local-api/app/services/chat_tasks.py
apps/local-api/app/services/browser_page_state.py
apps/local-api/app/services/chat_run_ledger.py
apps/local-api/app/services/release.py
```

## 实施拆解

### 96.1 AgentLoop 状态契约统一

目标：

```text
让 agent loop 不再只是内部约定，而是明确 typed 状态机
```

交付：

```text
agent_loop_state
observation_packet
next_action_decision
replan_delta
loop_stop_reason
loop_pause_reason
handoff_record
```

要求：

```text
任何一轮 loop 都必须能回答：看到了什么、为什么这么选、做了什么、为什么停
```

### 96.2 观察源统一收口

目标：

```text
让 agent 的“观察”不是松散字符串，而是统一证据面
```

交付：

```text
tool result -> observation
browser page state -> observation
knowledge / memory recall -> observation
failure advisory -> observation
steering change -> observation
approval state -> observation
```

要求：

```text
观察必须区分 trusted / untrusted
观察必须保留来源 ref
观察不能直接覆盖用户当前显式指令
```

### 96.3 下一步选择与重规划统一

目标：

```text
把“下一步做什么”和“什么时候改计划”从散点逻辑变成统一策略
```

交付：

```text
pending step selector 收口
observation-aware next_action selector 收口
replan trigger contract
failure recovery planner 接入统一 loop
```

要求：

```text
优先局部修正，不轻易全量重规划
重规划必须留下 delta 证据
每次 next_action 都要有 reason_codes
```

### 96.4 停止、暂停、接管语义统一

目标：

```text
让 agent loop 的停止不是“跑不下去就结束”，而是明确可解释状态
```

交付：

```text
budget_exhausted
approval_waiting
boundary_blocked
human_handoff_required
goal_satisfied
recovery_exhausted
steering_interrupted
```

要求：

```text
停止和暂停都必须可回放
审批等待不能被写成已完成
人工接管要保留接管前最后一轮观察和建议下一步
```

### 96.5 AgentLoop 回放与评测

目标：

```text
让 agent loop 的成熟度不靠体感，而靠标准回放与评测
```

交付：

```text
loop replay timeline
loop diagnostics bundle
observation -> action -> result 闭环检查
agent regression suite
```

## 测试与验收

### 最小测试集

```text
apps/local-api/tests/test_phase19_model_planner_agent.py
apps/local-api/tests/test_phase25_model_planner_quality.py
apps/local-api/tests/test_phase39_task_checkpoints.py
apps/local-api/tests/test_phase60_turn_recovery.py
apps/local-api/tests/test_phase61_agent_workbench_loop.py
apps/local-api/tests/test_phase93_steering_and_continuous_execution.py
```

### 本阶段新增测试重点

```text
观察结果触发 next_action 改变
失败后局部重规划而不是直接崩掉
审批等待、预算命中、人工接管都有明确 stop_reason
同一任务 replay 能还原每轮观察与决策
agent loop 不因 untrusted observation 越权
```

## 完成定义

```text
项目拥有统一权威的 AgentLoop 主链路
observe / decide / act / reflect / replan / stop 形成 typed contract
每轮 loop 都可追溯、可解释、可回放
agent 模式不再只是字段或 facade，而是稳定可执行运行时
后续任何高频做事域都可以复用这条统一 loop 主链
```
