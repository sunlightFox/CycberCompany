# 第二十八阶段：MCP 运行时隔离与协议健壮性硬化

## 摘要

第二十八阶段聚焦 MCP 的安全运行边界。当前 MCP 已支持 stdio allowlist、env_refs、server sync、tool/resource/prompt 注册、member scope、untrusted content 标记和输出 DLP；但 `MCPConnectionManager` 仍为 degraded，边界主要是本地 stdio allowlist 和策略检查。

本阶段目标是把 MCP 从“可控 stdio 接入”推进到“隔离运行、协议健壮、资源不可信、权限最小化”的最终态。

本阶段只做后端，不新增 UI。

## 阶段定位

第二十八阶段回答：

```text
MCP server 是否在隔离 runtime 中启动
server command/env/args 是否能预检和审计
MCP tool schema 是否严格校验
MCP resource/prompt 是否永远按不可信内容处理
MCP 输出是否不能直接驱动高风险工具
server 崩溃、超时、协议错误是否可恢复
```

## 当前基线判断

| 能力 | 当前状态 | 缺口 |
|---|---|---|
| MCP stdio allowlist | implemented | 本地策略边界，非强隔离 |
| MCP tool sync/call | implemented | 协议健壮性和错误恢复需增强 |
| MCP resource/prompt trust | implemented | 已标记不可信，需扩大 eval |
| MCP DLP | implemented | 已覆盖输出，需覆盖协议异常和 streaming |
| MCPConnectionManager | degraded | 缺隔离 runtime 和更强生命周期管理 |

## 阶段原则

1. MCP server 默认不可信。
2. MCP server 启动前必须经过 command allowlist、env_refs、scope、safety。
3. MCP tool call 必须走 ToolRuntime，不允许直接调用。
4. MCP resource/prompt 不得升级为系统指令。
5. MCP 输出进入模型上下文前必须 DLP 和 untrusted marker。
6. server 崩溃、超时、协议错误必须可恢复、可审计。

## 阶段范围

### 本阶段必须完成

```text
MCPRuntimeProfile
server process lifecycle manager
隔离启动策略
协议 handshake validator
tool/resource/prompt schema strict validation
timeout/retry/circuit breaker
untrusted content sanitizer
MCP replay diagnostics
MCP security eval
```

### 本阶段不做

```text
不做社区插件市场
不默认信任第三方 MCP
不允许 MCP 修改系统策略
不允许 MCP tool 直接绕过 Asset Broker
不新增前端
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 28.1 | MCPRuntimeProfile | command、env、scope、timeout、sandbox |
| 28.2 | 生命周期管理 | start/health/stop/restart/circuit breaker |
| 28.3 | 协议健壮性 | initialize、capability、schema、version |
| 28.4 | Tool/resource/prompt 严格边界 | schema validation、untrusted sanitizer |
| 28.5 | MCP 输出到工具链防注入 | output-to-action guard |
| 28.6 | MCP replay 与安全评测 | 崩溃、超时、注入、secret、越权 |

## 小阶段 28.1：MCPRuntimeProfile

### 目标

为每个 MCP server 建立可审计 runtime profile。

### 字段

```text
profile_id
server_id
transport
command_policy
args_policy
env_policy
member_scope_policy
network_policy
filesystem_policy
sandbox_backend
timeout_policy
resource_trust_policy
prompt_trust_policy
status
```

### 验收

```text
server 启动前必须绑定 profile
未知 command 默认拒绝
inline secret env 默认拒绝
profile 写入 runtime contract / diagnostics
```

## 小阶段 28.2：生命周期管理

### 目标

让 MCP server 可控启动、停止、重启、熔断。

### 状态

```text
created
starting
ready
degraded
failed
stopped
circuit_open
```

### 生命周期事件

```text
server.start_requested
server.started
server.health_checked
server.failed
server.restarted
server.circuit_opened
server.stopped
```

### 验收

```text
启动失败不影响主 API
连续失败进入 circuit_open
stop 清理进程
生命周期事件可 replay
```

## 小阶段 28.3：协议健壮性

### 目标

严格处理 MCP 协议版本、capability、schema 和异常。

### 检查

```text
initialize response schema
protocolVersion compatibility
serverInfo required fields
tools/list schema
resources/list schema
prompts/list schema
tools/call timeout
invalid JSON-RPC response
```

### 验收

```text
协议错误标记 server degraded
非法 tool schema 不注册
重复 tool name 有冲突处理
sync 结果可审计
```

## 小阶段 28.4：Tool/resource/prompt 严格边界

### 目标

让 MCP 暴露的所有内容都经过严格边界处理。

### Tool

```text
input_schema_validation
risk_annotation_mapping
capability_registration_preview
default_disabled_if_unknown_risk
```

### Resource

```text
trust_level=untrusted_external_content
mime_type_validation
size_limit
content_hash
sanitized_preview
```

### Prompt

```text
trust_level=mcp_prompt_template
never_system_instruction
argument_schema_validation
prompt_injection_scan
```

### 验收

```text
MCP prompt 不能覆盖系统/开发者策略
MCP resource 不直接进入高风险工具参数
未知高风险 tool 默认 disabled/approval_required
```

## 小阶段 28.5：MCP 输出到工具链防注入

### 目标

阻断 MCP 输出诱导后续工具执行或 secret 外发。

### Guard

```text
untrusted_output_marker
prompt_injection_detector
output_dlp
tool_arg_taint_tracking
high_risk_action_requires_clean_source
```

### 验收

```text
MCP 输出中的“忽略安全策略”不生效
MCP 输出不能直接变成 terminal command
MCP 输出不能直接外发 secret
tainted data 进入 R4+ 动作必须审批或阻断
```

## 小阶段 28.6：MCP replay 与安全评测

### 必测 case

```text
unknown command deny
inline env secret deny
invalid initialize response
invalid tool schema
tool call timeout
server crash recovery
resource prompt injection
prompt tries to become system instruction
MCP output secret redacted
MCP output-to-terminal injection blocked
member scope deny
```

### 验收命令

```text
.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase28_mcp_runtime_isolation.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

## 阶段总验收标准

第二十八阶段完成时必须满足：

```text
MCPConnectionManager 不再只是 stdio allowlist degraded
MCP server 有 runtime profile 和生命周期管理
协议异常可恢复、可审计
resource/prompt/tool 全部严格不可信边界
MCP 输出不能驱动高风险注入链
安全 eval 覆盖协议、越权、注入、secret、crash
```

