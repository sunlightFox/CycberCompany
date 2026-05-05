# 第六十二阶段 - 执行证据链强化与 delegate_task 并行委派通用化

## 阶段背景

当前项目已经有 browser、terminal、checkpoint、task replay、artifact store、supervisor 等能力，但真实场景里仍然会出现“看起来执行了，实际上证据不够硬”的问题。质量回归里已经暴露了 `browser.click`、`terminal.read_log`、回放证据和回复质量分数不足等缺口，说明执行层还需要再收紧。

与此同时，`supervisor` 现在更多还是任务编排中的一部分，还没有完全变成一个通用的 `delegate_task` 式并行委派底座。这个阶段要做的是把“执行证据链”与“并行委派”一起补强，让系统既能真执行，也能真分工。

本阶段继续只做后端、schema、migration、repository、service、API、tests、evals 和文档；不新增前端页面、组件、样式、Tauri 窗口或桌面端交互代码。

## 参考结论

### OpenClaw 采用点

OpenClaw 更强调可恢复的工作流和执行回路：

```text
动作有状态
状态可追踪
执行过程能回放
失败可以回到前一状态
```

本项目采用：

```text
browser / terminal / checkpoint / artifact / trace 必须构成统一证据链
执行结果不能只靠口头描述
```

### Hermes Agent 采用点

Hermes 的 delegate / subagent 思路强调并行和接力：

```text
一个主代理可以拆任务给子代理
子代理有独立上下文
结果再汇总回主线
```

本项目采用：

```text
把 delegate_task 做成通用 API，而不是只绑在任务编排中
并行子代理必须保留边界、trace 和汇总证据
```

## 核心目标

本阶段完成后，后端应支持：

```text
browser / terminal / checkpoint 的完整证据链
click / fill / type / submit / read_log / replay 的可审计结果
任务执行结果可回放、可验证、可追责
delegate_task 风格的并行子代理通用接口
```

## 阶段原则

1. 执行动作必须有证据，不接受“应该已经做了”。
2. 可写动作必须绑定回滚或说明不可回滚。
3. 任务证据要覆盖输入、计划、执行、结果和恢复点。
4. 并行委派必须默认最小上下文，不共享不必要的私有信息。
5. delegate_task 是通用能力，不是 supervisor 的内部小技巧。

## 阶段范围

### 本阶段必须完成

```text
browser / terminal / checkpoint 证据模型统一
browser 交互和 terminal 输出的审计回放闭环
任务 replay、artifact、trace 与 decision 证据统一
delegate_task / subagent / handoff 的通用 API
```

### 本阶段不做

```text
不把没有证据的执行说成完成
不让子代理读取超出其任务范围的上下文
不把并行委派退化成单线程轮询
不新增 UI
```

## 主要待补

```text
浏览器交互证据
终端输出证据
checkpoint 与回放一致性
delegate_task 通用接口
并行子代理汇总机制
```

## 验收标准

```text
browser.click / fill / type / submit 都能产生可回放证据
terminal.read_log 一定能关联到对应执行输出
checkpoint 与 replay 的结果一致且可审计
delegate_task 可以被多个业务域复用，不只服务任务引擎
```
