# 第七十一阶段 - ToolRuntime、终端队列与执行语义拆分重构

## 阶段背景

当前 `apps/local-api/app/services/tools.py` 已超过 3400 行，承载：

```text
工具注册
审批
执行边界
资产解析
文件工具
知识库工具
记忆工具
浏览器工具
终端工具
Skill 工具
MCP 工具
Office 工具
部署工具
artifact 落盘
trace span
```

这会让“聊天质量”层面出现两个问题：

```text
不同工具域的失败语义和可见话术很难统一
终端与浏览器这类长执行工具缺少独立 runtime 和队列模型
```

本阶段目标是参照 OpenClaw 的 command queue / lane 思路，把工具总运行时拆成多域 runtime，并给终端执行补上稳定队列和恢复语义。

## 核心目标

完成后，工具系统应满足：

```text
ToolRuntime 只做总调度与统一契约
浏览器、终端、知识、记忆、Skill、MCP 各自独立执行器
终端工具具备队列、并发 lane、超时、重置、恢复能力
只读终端与高风险终端共享同一安全与执行骨架
聊天层不再关心具体工具域实现
```

## 破坏性调整

### 新工具域拆分

将 `tools.py` 拆为：

```text
apps/local-api/app/services/tool_runtime.py
apps/local-api/app/services/tool_terminal_runtime.py
apps/local-api/app/services/tool_browser_runtime.py
apps/local-api/app/services/tool_skill_runtime.py
apps/local-api/app/services/tool_mcp_runtime.py
apps/local-api/app/services/tool_memory_runtime.py
apps/local-api/app/services/tool_knowledge_runtime.py
apps/local-api/app/services/tool_artifact_runtime.py
```

### 新终端队列子系统

新增：

```text
apps/local-api/app/services/terminal_queue.py
apps/local-api/app/services/terminal_lane.py
apps/local-api/app/services/terminal_session_runtime.py
```

统一 lane 语义：

```text
main
readonly
browser_assist
background
recovery
```

## 实现要求

### 1. 重构 ToolRuntime 总入口

要求：

```text
execute() 保留统一入口
参数校验、trace、审批、边界决策仍在总入口
具体执行下发到 domain runtime
domain runtime 只返回结构化结果，不直接拼用户可见回复
```

### 2. 建立 Terminal Queue

参考 OpenClaw 的 command lane 思路，实现：

```text
按 lane 串行或有限并发
队列可观测
任务超时后 lane 可释放
重启/恢复后可 reset lane
读取 active task 数和 queued 数
```

主接口建议：

```python
class TerminalQueueService:
    async def enqueue(self, lane: str, task: Callable[..., Awaitable[Any]], ...) -> Any:
        ...

    def snapshot(self) -> list[dict[str, Any]]:
        ...

    def reset_lane(self, lane: str) -> int:
        ...
```

### 3. 终端运行时语义统一

影响范围：

```text
apps/local-api/app/services/terminal_sandbox.py
apps/local-api/app/services/chat_intent_router.py
apps/local-api/app/services/chat.py
```

要求：

```text
chat_intent_router 只识别“可进入终端 route”
真正 readonly / network_read / network_write / destructive 分类下沉到 terminal runtime
terminal.run 的 sandbox、approval、dlp、queue、cleanup 全部经统一 runtime
```

### 4. Tool 结果语义标准化

所有 domain runtime 返回统一字段族：

```text
status
execution_semantics
evidence_refs
approval_state
sandbox_profile
backend_status
degraded_reason
resource_usage
cleanup
retryable
```

禁止不同工具域再自由长出互不兼容的结果键名。

## 建议文件调整

### 新增文件

```text
apps/local-api/app/services/tool_runtime.py
apps/local-api/app/services/tool_terminal_runtime.py
apps/local-api/app/services/tool_browser_runtime.py
apps/local-api/app/services/tool_skill_runtime.py
apps/local-api/app/services/tool_mcp_runtime.py
apps/local-api/app/services/tool_memory_runtime.py
apps/local-api/app/services/tool_knowledge_runtime.py
apps/local-api/app/services/tool_artifact_runtime.py
apps/local-api/app/services/terminal_queue.py
apps/local-api/app/services/terminal_lane.py
apps/local-api/app/services/terminal_session_runtime.py
```

### 缩减文件

```text
apps/local-api/app/services/tools.py
apps/local-api/app/services/terminal_sandbox.py
```

## 验收标准

```text
tools.py 不再承载各工具域的大段执行逻辑
terminal 工具有独立 queue 与 lane 模型
只读终端命令仍可从聊天入口触发，但聊天层只负责 route，不直接关心执行细节
工具结果字段统一，便于 ResponseComposer 和 trace 消费
高风险终端动作仍必须走 approval
```

## 测试计划

```text
apps/local-api/tests/test_phase35_chat_safety_state_semantics.py
apps/local-api/tests/test_phase51_quality_regression_hardening.py
apps/local-api/tests/test_phase52_chat_deploy_install.py
apps/local-api/tests/test_phase62_wechat_chat_main_chain_benchmark.py
```

新增断言：

```text
terminal lane 超时后可释放
reset lane 不导致队列永久卡死
readonly terminal 与 high risk terminal 共享统一结果结构
tools.py 中不再保留 _execute_browser_tool / _execute_terminal_tool 等整段实现
```
