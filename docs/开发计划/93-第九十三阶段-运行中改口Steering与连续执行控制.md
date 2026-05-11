# 第九十三阶段 - 运行中改口Steering与连续执行控制

## 阶段定位

前面的聊天主链路主要解决“每一轮 turn 怎么做好”。  
但高质量 agent 还需要处理另一类真实场景：**用户在执行中途改口、补充、打断、插入新约束**。

第九十三阶段专门补这一层，让系统从“回合式聊天代理”升级为“可被实时纠偏的连续执行代理”。

## 直接依赖

```text
docs/开发计划/77-第七十七阶段-聊天运行时收口与主链路统一.md
docs/开发计划/78-第七十八阶段-会话与渠道语义统一.md
docs/开发计划/80-第八十阶段-聊天内工具调用闭环.md
docs/开发计划/88-第八十八阶段-渠道可靠性与NoTurn治理闭环.md
docs/开发计划/89-第八十九阶段-聊天质量误拦截治理与规则减法.md
```

## 阶段目标

```text
支持 active run 期间的新指令接入
建立 steering / interrupt / followup / queue merge 的统一语义
让“继续刚才”“停，改成”“先别做那个”不只在聊天文本层生效，也在执行控制层生效
避免 active turn 与新 turn 之间出现状态竞争和重复执行
```

## 核心问题

当前系统对 continuation 与 latest override 已经比之前好很多，  
但更多仍属于“下一轮回复理解”而不是“运行中控制能力”：

```text
用户中途改口时，系统未必能稳定影响当前执行
queue / runtime / tool loop / channel followup 的控制语义还不统一
interrupt、defer、merge、reject 的策略缺少统一 contract
```

## 本阶段范围

### 必须完成

```text
定义 steering event / control intent / run interruption 语义
让 ChatRuntime 能识别 active run 上的 followup control
建立 turn merge / supersede / pause / cancel / resume 的主链契约
补真实渠道场景下的运行中改口验收
```

### 明确不做

```text
不引入全新外部渠道
不在本阶段做多用户共享编辑协作
不跳过 approval / safety / evidence gate
```

## 主要模块

```text
apps/local-api/app/services/chat_runtime.py
apps/local-api/app/services/chat_turn_execution.py
apps/local-api/app/services/context_gateway.py
apps/local-api/app/services/brain_decision.py
apps/local-api/app/services/channel_ingress_runtime.py
apps/local-api/app/services/channel_stream_bridge.py
apps/local-api/app/services/wechat_gateway.py
apps/local-api/app/services/feishu_gateway.py
```

## 实施拆解

### 93.1 steering control contract

目标：

```text
把“改口、打断、补充、撤回、暂停”定义成统一控制语义，而不是散落文案判断
```

交付：

```text
steering intent schema
active run control event
run supersede / merge / pause / resume policy
```

### 93.2 ChatRuntime 连续执行控制

目标：

```text
让 ChatRuntime 在 active turn 存在时，能处理新消息对当前执行的影响
```

交付：

```text
run steering coordinator
queue merge / interrupt gate
active turn conflict resolution
```

### 93.3 tool / task / workflow 控制传播

目标：

```text
让 steering 不停留在聊天层，而能影响工具链、任务链、workflow 汇总态
```

交付：

```text
tool loop steering hooks
task handoff interruption semantics
workflow step followup control
```

### 93.4 渠道侧连续执行体验

目标：

```text
让微信 / 飞书中的连续补充、撤回方向改口也可被稳定解释
```

交付：

```text
channel followup control diagnostics
run steering acceptance scenarios
wrong steering reuse / duplicate control suppression
```

## 测试与验收

### 最小测试集

```text
apps/local-api/tests/test_phase78_session_channel_semantics.py
apps/local-api/tests/test_phase80_chat_tool_loop.py
apps/local-api/tests/test_phase84_chat_mainline_acceptance_matrix.py
apps/local-api/tests/test_phase88_channel_reliability.py
```

### 本阶段新增测试重点

```text
active run 期间用户改口可影响当前执行路径
同一轮补充与新任务请求不会互相误吞
pause / cancel / supersede / resume 状态可见且一致
渠道场景下 steering 不会制造重复 turn 或错误复用
```

## 完成定义

```text
系统具备运行中改口与连续执行控制能力
steering 成为 runtime-native contract
聊天体验从回合式升级到连续协作式
active run 与 followup 不再互相打架
```
