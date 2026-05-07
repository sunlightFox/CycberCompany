# 第六十九阶段 - ChatRuntime 主链路拆分与路由收敛

## 阶段背景

当前聊天主链路能力已经很多，但核心编排过度集中在 `apps/local-api/app/services/chat.py`。该文件已超过 5000 行，同时承担：

```text
turn 创建
trace 生命周期
上下文构建
模型调用
direct 回复
自然语言确认
浏览器只读捷径
终端只读捷径
任务恢复
消息落库
回复收尾
```

这类“总控大文件”短期能堆功能，长期会直接伤害聊天质量：闲聊、多轮、深聊、记忆、工具、Skill、MCP、浏览器和系统命令都在同一个大分支树里互相干扰，导致每修一处就容易把另一处带偏。

本阶段目标不是新增用户可见功能，而是先把聊天主链路拆成稳定的后端运行时骨架，为后续质量优化、对标 OpenClaw / Hermes 的 session runtime、tool loop、quality gate 打底。

## 核心目标

完成后，聊天主链路应满足：

```text
chat.py 不再承担全部运行时细节
turn 编排、direct 路由、工具捷径、任务恢复、流式投递各自独立
direct / workflow / agent / supervisor 路由语义统一
浏览器只读与终端只读不再直接长在主编排大文件里
用户可见话术不继续散落到 chat.py
后续 prompt、memory、tools、channels 可单独演进
```

## 破坏性调整

### 新主链路模块边界

将 `chat.py` 当前职责按运行时拆为：

```text
apps/local-api/app/services/chat_runtime_orchestrator.py
apps/local-api/app/services/chat_turn_lifecycle.py
apps/local-api/app/services/chat_direct_routes.py
apps/local-api/app/services/chat_tool_shortcuts.py
apps/local-api/app/services/chat_stream_runtime.py
apps/local-api/app/services/chat_recovery_runtime.py
```

`chat.py` 保留为门面服务：

```text
ChatService 继续作为 API 依赖注入入口
但不再承载大段 route-specific 分支实现
```

### 路由语义统一

所有聊天路由统一收敛到以下 route taxonomy：

```text
default_chat
direct_with_memory
natural_action_resolution
tool_shortcut_browser_read
tool_shortcut_terminal_readonly
tool_shortcut_host_filesystem_list
office_document_task
task_execution
task_recovery
```

旧的“局部 reason_code + 临时特判”允许短期兼容读取，但新 turn 写入统一 route 语义。

## 实现要求

### 1. 拆分 `chat.py`

影响范围：

```text
apps/local-api/app/services/chat.py
apps/local-api/app/services/turn_execution.py
apps/local-api/app/services/turn_recovery.py
apps/local-api/app/services/turn_events.py
```

要求：

```text
create_turn / stream_turn_events / cancel_turn / retry_turn 仍保留在 ChatService
run_turn 和 _execute_turn 的主要编排迁移到 chat_runtime_orchestrator.py
direct route、browser read、terminal readonly、host filesystem list 迁移到 chat_tool_shortcuts.py
turn 持久化、事件落库、结束收尾迁移到 chat_turn_lifecycle.py / chat_stream_runtime.py
任务恢复逻辑迁移到 chat_recovery_runtime.py
```

### 2. 建立 ChatRuntimeOrchestrator

新增主接口建议为：

```python
class ChatRuntimeOrchestrator:
    async def run_turn(self, turn_id: str) -> None:
        ...
```

职责限定：

```text
读取 turn
构建上下文
选择 route
调度 direct / tool shortcut / task
统一结束 turn
统一异常恢复
```

禁止：

```text
不直接写用户可见长文案
不直接实现具体工具执行
不直接实现记忆写入规则
不直接实现 prompt 拼接
```

### 3. 建立 Tool Shortcut Runtime

从 `chat.py` 迁出：

```text
_handle_browser_read_page
_handle_terminal_readonly_command
_handle_host_filesystem_list
```

新模块要求：

```text
只处理聊天入口层的“快捷只读动作”
真正工具执行仍走 ToolRuntime
快捷 route 自己不拼复杂文案，统一交 ResponseComposer
失败时必须明确“没有执行 / 没有看过 / 没有假装完成”
```

### 4. 清理 `chat.py` 中用户可见补丁

要求：

```text
chat.py 不再新增长中文回复模板
route-specific reply 文本迁移到 ResponseComposer 或专门服务
route-specific structured_payload 允许保留，但要从专门 runtime 返回
```

## 建议文件调整

### 新增文件

```text
apps/local-api/app/services/chat_runtime_orchestrator.py
apps/local-api/app/services/chat_turn_lifecycle.py
apps/local-api/app/services/chat_direct_routes.py
apps/local-api/app/services/chat_tool_shortcuts.py
apps/local-api/app/services/chat_stream_runtime.py
apps/local-api/app/services/chat_recovery_runtime.py
```

### 缩减文件

```text
apps/local-api/app/services/chat.py
```

### 暂不删除但后续可并入的薄协调器

```text
apps/local-api/app/services/chat_context.py
apps/local-api/app/services/chat_model.py
apps/local-api/app/services/chat_memory.py
apps/local-api/app/services/chat_response.py
```

## 验收标准

```text
chat.py 体量显著下降，不再持有 browser/terminal/filesystem 大段 route 逻辑
run_turn 主链路可读，direct / tool shortcut / task 分支清晰
所有 turn 仍保持 trace、event、message 持久化完整
只读工具捷径仍可工作，但实现从 chat.py 迁出
用户可见回复不因拆分而退化
```

## 测试计划

```text
apps/local-api/tests/test_phase34_natural_chat_interaction_loop.py
apps/local-api/tests/test_phase35_chat_safety_state_semantics.py
apps/local-api/tests/test_phase41_chat_quality_experience.py
apps/local-api/tests/test_phase64_chat_continuation.py
apps/local-api/tests/test_xiaowu_chat_quality.py
```

静态检查：

```powershell
rg "_handle_browser_read_page|_handle_terminal_readonly_command|_handle_host_filesystem_list" apps/local-api/app/services/chat.py
rg "response_plan_for_tool_boundary|当前.*不可用；我没有" apps/local-api/app/services/chat.py
```

目标是运行时代码迁移后，`chat.py` 中不再保留这些大段快捷路由实现。
