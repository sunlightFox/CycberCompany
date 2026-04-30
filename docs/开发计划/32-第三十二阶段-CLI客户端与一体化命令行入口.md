# 第三十二阶段：CLI 客户端与一体化命令行入口

## 摘要

第三十二阶段在第三十、三十一阶段真实聊天主链路 E2E 收敛之后，补齐一个面向最终产品的本地命令行入口。

本阶段要开发的不是临时测试脚本，也不是前端页面的替代品，而是最终态个人智能体操作系统的 CLI 客户端。它默认连接本机 `local-api`，在服务未启动时可按既有后端启动约定自动拉起服务，然后通过现有 HTTP / SSE API 进入真实聊天主链路。

CLI 的第一屏就是聊天，用户可以直接在终端里和当前聊天对象对话；同时 CLI 也提供诊断、回放、会话切换和健康检查能力，方便开发者、测试者和后续自动化脚本验证聊天主链路、任务链路、工具链路和安全边界。

本阶段继续遵守后端优先约束：

```text
不新增前端页面
不新增 React / Tauri / 桌面窗口
不让 CLI 绕过 API 直接访问数据库
不让 CLI 直接执行 Tool / Skill / MCP
不绕过 Safety、Approval、Trace、Asset Broker、Capability Graph
```

开发只按一个最终封版产品推进。CLI 可以分小阶段实现，但产品目标只有一个：形成可长期使用、可测试、可审计、可扩展的一体化命令行入口。

## 阶段定位

第三十二阶段回答：

```text
没有 UI 时，用户如何真实使用聊天主链路
开发者如何用一个稳定入口快速复现聊天、任务、工具、记忆、Persona/Heart 问题
真实聊天 E2E 脚本之外，是否有可日常使用的交互式终端入口
local-api 未启动时，CLI 是否能自动发现并拉起本地服务
SSE 流式事件、turn detail、brain decision、response quality 是否能被终端稳定消费
诊断和回放是否能只通过公开 API 完成，而不破坏后端分层
CLI 输出是否能保持安全脱敏，不把 secret、token、cookie、私钥、本地敏感路径打到终端
```

CLI 在产品中的定位：

| 层级 | 定位 | 边界 |
|---|---|---|
| 产品入口 | 本地终端聊天入口 | 默认进入聊天，不显示组织/壳信息 |
| 开发入口 | 主链路调试和诊断入口 | 只调用 API，不直接 import service |
| 测试入口 | E2E 场景手工复现入口 | 可输出 JSON 事件，便于脚本消费 |
| 运维入口 | 健康检查和服务拉起入口 | 不替代 ReleaseGate，只做调用和展示 |

参考方向：

```text
参考 OpenClaw CLI 的命令分组和本地代理入口思路
参考 Hermes Agent 的终端交互式 Agent 使用形态
结合本项目已有 local-api、SSE、runtime contracts、diagnostics、release evidence 设计
```

## 当前基线

当前仓库已经具备 CLI 所需的后端基础：

| 能力 | 当前状态 | CLI 复用方式 |
|---|---|---|
| local-api 启动 | `scripts/dev.ps1` 可启动 `127.0.0.1:8765` | CLI 复用同一环境变量和启动命令 |
| 健康检查 | `GET /health` | `cycber status` / autostart 探测 |
| 完整健康 | `GET /api/health/full` | `cycber doctor` 展示摘要 |
| 会话列表 | `GET /api/chat/conversations` | 默认选择和 `/conversations` |
| 创建聊天轮次 | `POST /api/chat/turn` | `cycber chat` |
| SSE 流式事件 | `GET /api/chat/stream/{turn_id}` | 实时打印 assistant delta |
| turn 详情 | `GET /api/chat/turns/{turn_id}` | `/turn`、`turns show` |
| 持久事件 | `GET /api/chat/turns/{turn_id}/events` | `/events`、`turns replay` |
| Brain 决策 | `GET /api/chat/turns/{turn_id}/brain-decision` | `/brain`、`--diagnostics` |
| 语义复核 | `GET /api/chat/turns/{turn_id}/semantic-review` | `/semantic`、`--diagnostics` |
| Tone / Quality | `GET /api/chat/turns/{turn_id}/tone-policy`、`response-quality` | `/quality` |
| Trace | `GET /api/traces/{trace_id}` | `/trace` |
| Runtime contracts | `GET /api/system/runtime-contracts` | `doctor` 能力摘要 |

当前缺口：

```text
没有独立 CLI 包
没有交互式聊天 REPL
没有通用 SSE parser
没有一体化 local-api 启动和健康轮询
没有本地 CLI session/conversation 状态管理
没有终端侧 turn 诊断、事件回放和质量摘要命令
没有 CLI 输出脱敏测试
开发计划 README 尚未登记第 32 阶段
```

## 阶段原则

1. CLI 只作为客户端，不作为新的后端业务层。
2. CLI 只调用公开 API，不直接访问 SQLite、repository、service、ToolRuntime、SkillEngine、MCP runtime。
3. CLI 不新增权限，不拥有比普通 API 调用更高的能力。
4. CLI 所有聊天、任务、工具、MCP、Skill 行为必须仍由后端 Safety / Approval / Trace 决定。
5. 默认一体化体验：服务未启动时自动拉起，服务已启动时直接连接。
6. 自动拉起服务必须可关闭，用户可用 `--no-autostart` 只连接现有 API。
7. 默认输出面向人类阅读，`--json` 输出面向脚本消费。
8. 所有终端输出经过本地 redaction，再打印到控制台。
9. CLI 状态只保存非敏感配置，不保存 secret、token、cookie、私钥、密码或模型 API key。
10. 交互模式只显示聊天对象、消息、状态和必要诊断，不显示组织壳语义。
11. 诊断命令可以展示 backend module、trace、runtime contract，但必须明确属于开发诊断视图。
12. 本阶段不引入大型 TUI 框架，先用标准库 + `httpx` + 清晰文本输出完成最终态命令契约。

## 阶段范围

### 本阶段必须完成

```text
新增 apps/local-cli/cycber_cli 独立 CLI 包
新增 scripts/cli.ps1 本地入口
实现 health/status/doctor
实现 local-api 自动发现、自动启动、健康轮询、日志路径
实现 HTTP API client
实现 SSE event parser
实现单轮聊天命令
实现交互式聊天 REPL
实现会话列表、会话选择、本地 session 状态
实现 turn 详情、事件列表、事件回放
实现 brain decision、semantic review、tone policy、response quality 诊断摘要
实现 CLI 输出 redaction
新增 CLI 单元测试和 API mock 测试
更新开发计划 README
```

### 本阶段不做

```text
不新增前端页面
不新增 Tauri / React / 桌面窗口
不做完整 curses / Textual / Rich TUI
不做云端登录、账号体系或远程 SaaS
不在 CLI 里直接执行浏览器、终端、文件、MCP、Skill
不把 CLI 做成绕过审批的管理后门
不直接读取或写入业务数据库
不直接读取 SecretStore
不在 CLI 状态文件里保存敏感值
不改变聊天主链路 API 的既有行为
不把 CLI 测试混入 docs/测试 的真实聊天报告脚本
```

## 产品形态

### 默认入口

用户执行：

```powershell
.\scripts\cli.ps1
```

等价于：

```powershell
.\scripts\cli.ps1 chat --interactive --autostart
```

默认行为：

```text
1. 查找仓库根目录
2. 设置 CYCBER_ROOT 和 PYTHONPATH
3. 检查 http://127.0.0.1:8765/health
4. 如果健康，直接进入聊天
5. 如果不健康，按 local-api 启动约定拉起 uvicorn
6. 轮询 /health，成功后读取默认会话和成员
7. 进入交互式聊天
```

### 交互体验

```text
小曜 > 你好，今天帮我梳理一下测试计划

你  > 先按三部分说：目标、步骤、风险

小曜 > 好，我先给结论...

/status
/brain last
/events last
/quality last
/exit
```

终端中普通输入全部作为聊天消息；以 `/` 开头的是 CLI 本地命令。

### 输出模式

| 模式 | 用途 | 行为 |
|---|---|---|
| human | 默认交互 | 打印 assistant 文本、关键状态、错误提示 |
| compact | 脚本和窄终端 | 只输出最终文本和 turn_id |
| verbose | 开发调试 | 显示事件、intent、mode、trace、diagnostics |
| json | 自动化脚本 | 每行一个 JSON event，不做额外人类文案 |

## 命令契约

### 顶层命令

```text
cycber
cycber chat
cycber status
cycber doctor
cycber serve
cycber conversations
cycber turns
cycber traces
cycber config
```

### `cycber chat`

```powershell
cycber chat
cycber chat --interactive
cycber chat --message "你好"
cycber chat -m "帮我总结一下今天测试结果"
cycber chat --conversation conv_default_xiaoyao --member mem_xiaoyao
cycber chat --session cli_20260430 --diagnostics
cycber chat --json -m "只输出 JSON"
cycber chat --no-stream -m "测试持久事件读取"
cycber chat --no-autostart
```

参数契约：

| 参数 | 默认 | 说明 |
|---|---|---|
| `--message`, `-m` | 空 | 单轮聊天内容 |
| `--interactive`, `-i` | 未传 message 时为 true | 进入 REPL |
| `--base-url` | `http://127.0.0.1:8765` | local-api 地址 |
| `--conversation` | 本地状态或默认会话 | 会话 ID |
| `--member` | 会话 primary member | 成员 ID |
| `--session` | 本地生成 `cli_...` | session ID |
| `--stream` | true | 使用 SSE |
| `--no-stream` | false | 创建 turn 后读取 persisted events |
| `--diagnostics` | false | 完成后拉取诊断摘要 |
| `--json` | false | 输出 JSON Lines |
| `--autostart` | true | API 不健康时自动拉起 |
| `--no-autostart` | false | 禁止自动拉起服务 |
| `--timeout` | 180 | 单轮等待秒数 |

### `cycber status`

展示：

```text
API health status
API version
default_shell
base_url
current conversation
current member
current session
last turn
```

状态只从 `/health`、`/api/chat/conversations` 和 CLI 本地状态读取。

### `cycber doctor`

展示：

```text
Python 可用性
venv Python 路径
仓库根目录
CYCBER_ROOT
PYTHONPATH 关键项
端口 8765 状态
/health 状态
/api/health/full 摘要
runtime contracts 核心模块状态
最近 CLI server log 路径
```

`doctor` 不改变系统状态，除非显式传入 `--autostart`。

### `cycber serve`

显式拉起 local-api：

```powershell
cycber serve
cycber serve --host 127.0.0.1 --port 8765
cycber serve --foreground
cycber serve --log-dir data/cli/logs
```

默认行为：

```text
后台启动 uvicorn
日志写入 data/cli/logs/local-api-<timestamp>.log
启动后轮询 /health
如果端口已被健康 local-api 占用，则复用
如果端口被非本项目服务占用，则报错
```

### `cycber conversations`

```powershell
cycber conversations list
cycber conversations use conv_default_xiaoyao
cycber conversations show conv_default_xiaoyao
```

约束：

```text
只读取和切换本地 CLI 当前会话
不创建新会话，除非后端未来提供公开 API
不修改组织、成员、壳数据
```

### `cycber turns`

```powershell
cycber turns show <turn_id>
cycber turns events <turn_id>
cycber turns replay <turn_id>
cycber turns brain <turn_id>
cycber turns semantic <turn_id>
cycber turns quality <turn_id>
```

用途：

```text
复现聊天主链路问题
查看事件顺序
确认 intent/mode/context 决策
确认 response_plan、tone_policy、quality markers
确认 turn 是否创建 task 或 approval
```

### 交互模式 slash commands

| 命令 | 行为 |
|---|---|
| `/help` | 展示命令 |
| `/status` | 同 `cycber status` |
| `/doctor` | 同 `cycber doctor`，默认不 autostart |
| `/conversations` | 列出会话 |
| `/use <conversation_id>` | 切换当前会话 |
| `/turn <turn_id>` | 展示 turn detail |
| `/events <turn_id|last>` | 展示持久事件 |
| `/replay <turn_id|last>` | 按事件顺序回放 |
| `/brain <turn_id|last>` | 展示脑决策摘要 |
| `/semantic <turn_id|last>` | 展示语义复核摘要 |
| `/quality <turn_id|last>` | 展示 tone policy 和 response quality |
| `/trace <trace_id|last>` | 展示 trace 摘要 |
| `/json on/off` | 切换 JSON Lines 输出 |
| `/verbose on/off` | 切换详细状态输出 |
| `/clear` | 清空终端显示，不清空后端会话 |
| `/exit` | 退出 |

## 技术架构

### 目录建议

```text
apps/local-cli/
  cycber_cli/
    __init__.py
    __main__.py
    app.py
    config.py
    state.py
    redaction.py
    output.py
    server.py
    http_client.py
    sse.py
    chat.py
    repl.py
    diagnostics.py
    commands/
      __init__.py
      chat.py
      status.py
      doctor.py
      serve.py
      conversations.py
      turns.py
      traces.py
  tests/
    test_sse.py
    test_redaction.py
    test_chat_client.py
    test_server_manager.py

scripts/
  cli.ps1
```

### 依赖方向

允许：

```text
local-cli -> httpx
local-cli -> core_types 可选，用于类型和事件枚举
local-cli -> local-api public HTTP API
scripts/cli.ps1 -> apps/local-cli/cycber_cli
```

禁止：

```text
local-cli -> app.services.*
local-cli -> app.db.*
local-cli -> TaskRepository / ChatRepository
local-cli -> ToolRuntime / MCPService / SkillPluginService
local-cli -> SecretStore
local-api -> local-cli
```

### 数据流

```text
用户输入
  -> CLI redaction preview
  -> POST /api/chat/turn
  -> 获取 stream_url
  -> GET /api/chat/stream/{turn_id}
  -> SSE parser
  -> event reducer
  -> terminal renderer
  -> 可选 diagnostics 拉取
  -> 本地状态保存 last_turn / conversation / session
```

### 本地状态

默认路径：

```text
~/.cycbercompany/cli/config.json
~/.cycbercompany/cli/state.json
```

状态字段：

```json
{
  "base_url": "http://127.0.0.1:8765",
  "conversation_id": "conv_default_xiaoyao",
  "member_id": "mem_xiaoyao",
  "session_id": "cli_20260430",
  "last_turn_id": "turn_xxx",
  "last_trace_id": "trc_xxx",
  "output_mode": "human",
  "autostart": true
}
```

禁止保存：

```text
secret
token
cookie
password
private_key
mnemonic
api_key
browser credential
wallet value
raw model key
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 32.1 | CLI 产品契约与工程骨架 | 命令契约、目录、入口脚本、状态文件 |
| 32.2 | HTTP 与 SSE 客户端 | API client、SSE parser、事件 reducer |
| 32.3 | 一体化 local-api 服务管理 | health check、autostart、日志、端口冲突处理 |
| 32.4 | 交互式聊天 REPL | 默认聊天入口、slash commands、流式输出 |
| 32.5 | 会话、turn、诊断与回放 | conversations、turn detail、events、brain、quality |
| 32.6 | 安全脱敏与边界治理 | 输出 redaction、本地状态安全、错误边界 |
| 32.7 | 自动化输出与脚本集成 | JSON Lines、退出码、CI/测试复用 |
| 32.8 | 测试、文档与验收闭环 | 单测、API mock、README、阶段回归 |

## 小阶段 32.1：CLI 产品契约与工程骨架

### 目标

建立 CLI 客户端的最终工程骨架，明确命令行入口、模块边界和本地状态模型。

### 实现要求

```text
新增 apps/local-cli/cycber_cli 包
新增 __main__.py 支持 python -m cycber_cli
新增 scripts/cli.ps1 作为 Windows 主入口
新增 CLI 参数解析层，优先使用 argparse，不引入大型 CLI 框架
定义全局参数：base-url、autostart、json、verbose、timeout
定义本地状态读写模块 state.py
定义输出模块 output.py，所有 print 统一经过 renderer
定义 redaction.py，所有输出统一经过脱敏函数
```

### 设计细节

```text
CLI 包不注册到 local-api
CLI 包不被 local-api import
CLI 入口脚本负责补齐 PYTHONPATH
默认使用 .venv\Scripts\python.exe，如果不存在则退回 python
仓库根目录通过脚本路径或 CYCBER_ROOT 推断
```

### 验收

```powershell
.\scripts\cli.ps1 --help
.\scripts\cli.ps1 status --no-autostart
.\.venv\Scripts\python.exe -m cycber_cli --help
```

通过标准：

```text
命令能启动
帮助信息包含 chat/status/doctor/serve/conversations/turns
不要求 local-api 已启动即可展示 help
state 文件不存在时不会报错
```

## 小阶段 32.2：HTTP 与 SSE 客户端

### 目标

实现 CLI 到 local-api 的稳定通信层，支持聊天创建、流式事件消费和非流式事件读取。

### 实现要求

```text
封装 LocalApiClient
实现 request timeout、连接失败、HTTP 错误、JSON 解析错误的统一错误模型
实现 create_chat_turn
实现 stream_turn_events
实现 list_conversations
实现 get_turn
实现 get_turn_events
实现 get_brain_decision
实现 get_semantic_review
实现 get_tone_policy
实现 get_response_quality
实现 get_trace
```

### SSE parser 要求

```text
支持 event-stream 文本逐行解析
支持 data: JSON
支持多行 data
支持空行提交事件
支持未知字段忽略
支持心跳空行
支持服务端断流后给出 recoverable 错误
支持 --no-stream 回退到 persisted events
```

### 事件 reducer

CLI 内部将事件归约为：

```text
turn_started
context_ready
intent_detected
mode_selected
task_created
approval_required
response_delta
response_completed
turn_completed
turn_failed
cancelled
unknown
```

只在 renderer 层决定展示方式。

### 验收

```text
SSE parser 单测覆盖单行、多行、未知事件、断流
HTTP client 单测覆盖 200、404、422、500、连接失败
chat --no-stream 可读取 persisted events 并输出最终回复
chat --json 每个事件输出一行合法 JSON
```

## 小阶段 32.3：一体化 local-api 服务管理

### 目标

让 CLI 具备一体化本地使用体验：服务已启动时复用，服务未启动时自动拉起，端口异常时明确报错。

### 实现要求

```text
实现 ServerManager
健康检查 GET /health
默认 base_url = http://127.0.0.1:8765
默认 host = 127.0.0.1
默认 port = 8765
默认 app = app.main:app
默认 app-dir = apps/local-api
启动环境沿用 scripts/dev.ps1 中的 PYTHONPATH 列表
日志写入 data/cli/logs/local-api-<timestamp>.log
启动后轮询 /health，默认超时 30 秒
```

### 端口处理

```text
端口未占用：启动 uvicorn
端口已占用且 /health 返回本项目 HealthResponse：复用
端口已占用但 /health 不可访问：报错，提示用户检查端口
端口已占用且返回非本项目响应：报错，不抢占
```

### 失败语义

| 场景 | CLI 行为 |
|---|---|
| Python 不存在 | 提示安装或激活 venv |
| uvicorn 启动失败 | 显示日志路径 |
| health 超时 | 显示最后一次错误和日志路径 |
| 端口冲突 | 显示占用端口和 `--base-url` 建议 |
| migrations 失败 | 展示 API 返回错误摘要，不吞掉错误 |

### 验收

```text
ServerManager 单测不真实拉起长期进程，只验证命令构造和 health polling
doctor 能展示当前服务是否由 CLI 拉起
serve --foreground 可前台运行
chat --no-autostart 在服务未启动时明确失败
```

## 小阶段 32.4：交互式聊天 REPL

### 目标

实现可日常使用的终端聊天入口，普通输入走聊天，slash command 走本地 CLI 命令。

### 实现要求

```text
启动时读取默认 conversation 和 member
如果本地 state 有 conversation，则优先使用
如果 state conversation 不存在，则回退到 API 返回的第一条 active conversation
session_id 默认生成并持久保存
每次发送消息调用 POST /api/chat/turn
默认使用 SSE 实时显示 response.delta
turn 完成后保存 last_turn_id 和 last_trace_id
```

### 显示要求

```text
response.delta 连续打印，不重复换行
task_created 显示任务 ID、标题、状态
approval_required 显示 approval ID 和等待确认提示
turn_failed 显示错误码、错误摘要、trace_id
response_completed 显示 message_id、finish_reason
verbose 模式显示 intent、mode、privacy_level、trace_id
json 模式不输出额外人类文案
```

### slash command 要求

```text
/help 不访问 API
/status 访问 /health 和本地 state
/doctor 默认不 autostart
/conversations 访问会话列表
/use 只切换本地 conversation_id
/events last 使用 state.last_turn_id
/brain last 使用 state.last_turn_id
/quality last 使用 state.last_turn_id
/trace last 使用 state.last_trace_id
/exit 正常退出，退出码 0
```

### 验收

```text
交互模式可以完成至少一轮聊天
输入 /exit 能退出
输入空行不创建 turn
输入 slash command 不创建聊天消息
服务断开时提示可恢复错误
```

## 小阶段 32.5：会话、turn、诊断与回放

### 目标

让 CLI 成为聊天主链路排障入口，能从公开 API 拉取关键诊断证据。

### 实现要求

```text
conversations list 展示 conversation_id、title、primary_member_id、status、updated_at
conversations use 写入本地 state
turns show 展示 turn status、intent、mode、privacy、route、usage、trace
turns events 展示 sequence、event_type、payload 摘要
turns replay 按事件顺序重放 response.delta 和关键状态
turns brain 展示 primary_intent、mode、confidence、reason_codes
turns semantic 展示 semantic review status、fallback、suggestion 摘要
turns quality 展示 tone_mode、quality_markers、leakage_count、boundary violations
traces show 展示 trace status 和 span 摘要
```

### 输出策略

```text
默认 human 模式只展示摘要
verbose 模式展示 payload 的脱敏 JSON
json 模式完整输出脱敏 JSON
不显示 raw secret、不显示本地敏感路径、不显示内部 prompt
```

### 验收

```text
chat --diagnostics 完成后能展示 turn、brain、quality 摘要
turns replay 输出顺序与 persisted events sequence 一致
诊断接口 404 时显示“该证据不存在”，不当作 CLI 崩溃
```

## 小阶段 32.6：安全脱敏与边界治理

### 目标

确保 CLI 不成为新的敏感信息泄漏面，也不成为绕过后端安全边界的入口。

### 实现要求

```text
实现 CLI redaction policy
覆盖 api_key、token、password、cookie、private_key、mnemonic、local_sensitive_path
终端输出统一走 redact_for_terminal
日志输出统一走 redact_for_log
异常消息统一脱敏
state 写入前扫描敏感字段
检测到敏感字段时拒绝写入 state
```

### 边界要求

```text
CLI 不提供 tool execute 命令
CLI 不提供 mcp call 命令
CLI 不提供 skill run 命令
CLI 不提供 approval approve/deny 命令，除非后续阶段专门规划审批 CLI
CLI 不读取 SecretStore
CLI 不读取 data/secrets
CLI 不读取浏览器 profile
```

### 验收

```text
secret/token/password/cookie/private_key/mnemonic 出现在 API mock 响应时，CLI 输出必须脱敏
本地敏感路径出现在错误消息时，CLI 输出必须脱敏
state 文件不能写入敏感字段
异常 traceback 默认不显示，verbose 模式也要脱敏
```

## 小阶段 32.7：自动化输出与脚本集成

### 目标

让 CLI 既适合人工聊天，也适合测试脚本和 CI 调用。

### 实现要求

```text
所有命令定义稳定退出码
--json 使用 JSON Lines
--output <path> 可将脱敏输出写入文件
--quiet 只输出最终文本或错误
--verbose 展示诊断摘要
chat -m 支持非交互式单轮调用
chat --diagnostics --json 可用于 E2E 记录
```

### 退出码

| 退出码 | 含义 |
|---:|---|
| 0 | 成功 |
| 1 | 通用失败 |
| 2 | 参数错误 |
| 3 | API 不可用 |
| 4 | API 返回业务错误 |
| 5 | 流式连接中断 |
| 6 | 本地状态读写失败 |
| 7 | 安全脱敏检查失败 |

### 验收

```text
脚本可通过退出码判断失败类型
--json 输出可被逐行 json.loads 解析
--quiet 不输出诊断噪音
--output 文件不含 secret 明文
```

## 小阶段 32.8：测试、文档与验收闭环

### 目标

补齐测试和文档，使 CLI 能进入后续封版验证链路。

### 必须新增测试

```text
SSE parser 单测
HTTP client 错误映射单测
redaction 单测
state 读写单测
server manager 命令构造和 health polling 单测
chat 单轮 mock API 测试
interactive slash command 单测
diagnostics 聚合单测
JSON Lines 输出单测
scripts/cli.ps1 smoke 检查
```

### 推荐测试文件

```text
tests/test_phase32_cli_sse.py
tests/test_phase32_cli_redaction.py
tests/test_phase32_cli_client.py
tests/test_phase32_cli_server_manager.py
tests/test_phase32_cli_commands.py
```

### 文档要求

```text
README.md 增加 CLI 快速开始
docs/开发计划/README.md 增加第 32 阶段
CLI --help 覆盖所有公开命令
阶段文档记录不做范围和安全边界
```

### 验收命令

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_phase32_cli_sse.py
.\.venv\Scripts\python.exe -m pytest tests/test_phase32_cli_redaction.py
.\.venv\Scripts\python.exe -m pytest tests/test_phase32_cli_client.py
.\.venv\Scripts\python.exe -m pytest tests/test_phase32_cli_server_manager.py
.\.venv\Scripts\python.exe -m pytest tests/test_phase32_cli_commands.py
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy .
```

## 公共接口影响

### 新增命令行接口

```text
scripts/cli.ps1
python -m cycber_cli
cycber chat
cycber status
cycber doctor
cycber serve
cycber conversations
cycber turns
cycber traces
cycber config
```

### 不新增后端 API

本阶段默认不新增后端 API。CLI 只消费已有 API：

```text
GET /health
GET /api/health/full
GET /api/chat/conversations
GET /api/chat/conversations/{conversation_id}
POST /api/chat/turn
GET /api/chat/stream/{turn_id}
GET /api/chat/turns/{turn_id}
GET /api/chat/turns/{turn_id}/events
GET /api/chat/turns/{turn_id}/brain-decision
GET /api/chat/turns/{turn_id}/semantic-review
GET /api/chat/turns/{turn_id}/tone-policy
GET /api/chat/turns/{turn_id}/response-quality
GET /api/traces/{trace_id}
GET /api/system/runtime-contracts
```

如果实现过程中发现现有 API 无法满足诊断展示，只允许新增只读 API，并必须符合：

```text
schema -> service -> API -> tests
不在 API handler 中拼业务逻辑
不泄露 secret
不暴露内部 prompt
不改变现有聊天 turn 行为
```

## 文件影响范围

| 模块 | 文件范围 | 说明 |
|---|---|---|
| CLI Client | `apps/local-cli/cycber_cli/` | 新增 CLI 主体 |
| CLI Tests | `tests/test_phase32_cli_*.py` | 新增 CLI 单测和 mock API 测试 |
| Scripts | `scripts/cli.ps1` | 新增命令行入口 |
| Docs | `docs/开发计划/32-第三十二阶段-CLI客户端与一体化命令行入口.md` | 本阶段文档 |
| Docs Index | `docs/开发计划/README.md` | 阶段索引 |
| Root README | `README.md` | CLI 快速开始 |
| pyproject | `pyproject.toml` | 如需 pytest pythonpath 或可选 script entry，最小修改 |

不应修改：

```text
apps/local-api/app/services/tools.py
apps/local-api/app/services/mcp.py
apps/local-api/app/services/skill_plugin.py
apps/local-api/app/services/asset_broker.py
apps/local-api/app/services/capability.py
```

除非发现后端公开 API 缺陷需要只读诊断补齐。

## 与第三十一阶段的关系

第三十一阶段关注真实聊天主链路全量场景缺口收敛，重点是后端能力、E2E 脚本、浏览器/任务/知识/记忆/安全问题闭环。

第三十二阶段在其基础上提供一个可日常使用的命令行入口：

```text
第三十一阶段：后端主链路全量场景可用、可测、可封版
第三十二阶段：通过 CLI 把主链路变成可直接使用、可诊断、可回放的本地入口
```

第三十二阶段不替代第三十一阶段的测试报告，也不绕过第八、二十九阶段的封版门禁。它为后续真实使用和人工回归提供更顺手的入口。

## 阶段完成定义

```text
scripts/cli.ps1 可启动
cycber chat 默认进入交互聊天
local-api 未启动时可自动拉起并健康检查
chat -m 可完成单轮聊天并显示 SSE 回复
--no-stream 可通过 persisted events 输出回复
--json 输出合法 JSON Lines
conversations list/use 可用
turns show/events/replay 可用
brain/semantic/quality 诊断可用
doctor 可展示健康、运行契约和本地环境摘要
CLI 输出无 secret/token/password/cookie/private_key 明文
CLI state 不保存敏感值
新增 CLI 测试通过
ruff 和 mypy 通过
未新增前端、Tauri、React 或桌面交互代码
未绕过后端 Safety/Approval/Trace/ToolRuntime 边界
```

## 建议开发票据

| 票据 | 优先级 | 标题 | 关闭条件 |
|---|---|---|---|
| DEV-CLI-001 | P0 | CLI 工程骨架与入口脚本 | `scripts/cli.ps1 --help` 和 `python -m cycber_cli --help` 可用 |
| DEV-CLI-002 | P0 | HTTP client 与 SSE parser | chat mock 测试和 SSE parser 测试通过 |
| DEV-CLI-003 | P0 | 一体化 local-api autostart | 服务未启动时可自动拉起并通过 health |
| DEV-CLI-004 | P0 | 交互式聊天 REPL | 普通输入创建 turn，`/exit` 正常退出 |
| DEV-CLI-005 | P1 | 会话切换与本地状态 | `conversations list/use` 和 state 持久化通过 |
| DEV-CLI-006 | P1 | turn 诊断与事件回放 | `turns show/events/replay/brain/quality` 可用 |
| DEV-CLI-007 | P0 | CLI 输出脱敏 | secret/token/password/cookie/private_key 不明文出现 |
| DEV-CLI-008 | P1 | JSON Lines 和脚本化输出 | `chat -m --json --diagnostics` 可被脚本消费 |
| DEV-CLI-009 | P2 | README 与开发文档补齐 | README 和开发计划索引更新 |

## 风险与控制

| 风险 | 控制 |
|---|---|
| CLI 变成新的业务层 | 明确只调用公开 API，禁止 import service/repository |
| 自动启动误抢端口 | 先 health 验证，非本项目响应直接报错 |
| 终端泄露 secret | 输出、日志、异常、state 四层脱敏 |
| CLI 命令过度扩张 | 本阶段只做聊天、诊断、回放、状态，不做审批和工具执行 |
| 流式事件丢失 | 支持 persisted events 回退和 turns replay |
| Windows 路径兼容问题 | 入口脚本优先 PowerShell，路径全部用 `Path` 处理 |
| 真实 API 未启动导致测试不稳 | 单测使用 mock transport，不依赖真实服务 |

## 后续阶段预留

第三十二阶段完成后，后续可以单独规划：

```text
审批 CLI：approval list/show/approve/deny
任务 CLI：tasks list/show/replay
记忆 CLI：memory search/source/candidates
Skill/MCP CLI：只读注册、状态、诊断
Release CLI：release gate run/report/diagnostic
更强 TUI：仅在明确解除“不新增 UI”约束后考虑
```

这些能力不在本阶段实现，避免把 CLI 第一阶段做成过大的管理终端。
