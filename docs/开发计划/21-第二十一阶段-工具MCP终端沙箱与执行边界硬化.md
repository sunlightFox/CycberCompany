# 第二十一阶段：工具、MCP、终端沙箱与执行边界硬化

## 摘要

第二十一阶段聚焦“能做事，但不能越界”。当前 ToolRuntime 已经接入 Safety、Approval、Asset Broker、Trace；MCP 已具备 stdio allowlist、sync、call、scope policy；Terminal 已限制在 task artifact sandbox 并有策略阻断。但完成度分析显示，MCPConnectionManager 和 TerminalRunner 仍属于 accepted-risk degraded，主要缺口是 OS 级隔离、细粒度执行策略、输出 DLP 和外部工具可信边界。

本阶段目标是在不新增前端的前提下，进一步收紧工具、MCP、终端、浏览器、文件、网络等执行边界，让聊天触发的行动能力具备更强的封版安全证据。

## 阶段定位

第二十一阶段回答：

```text
工具是否只能在被授权的 task sandbox 内执行
终端命令是否有命令级风险分级和 OS 级隔离方案
MCP server 是否只能从 allowlist 启动
MCP tool/resource/prompt 是否默认视为不可信内容
浏览器工具是否区分 read/navigation/form/submit/upload
工具输出是否经过 DLP 和 secret redaction
所有执行是否有 capability、safety、approval、trace、audit
```

## 当前基线判断

| 能力 | 当前完成度 | 主要问题 |
|---|---:|---|
| ToolRuntime | 约 85% | 主链路已接 Safety/Approval/Asset/Trace |
| TerminalRunner | 约 70% | 有 task sandbox，缺 OS 级 sandbox |
| MCP | 约 73% | stdio allowlist 已有，协议和资源边界需硬化 |
| Browser Tool | 约 72% | 能力存在，动作分级和外发边界需细化 |
| Output DLP | 约 78% | 基础脱敏已有，需覆盖所有工具输出路径 |

## 阶段原则

1. 所有工具调用必须经过 ToolRuntime。
2. 所有真实资源解析必须经过 AssetBroker.resolve_for_tool。
3. 高风险动作默认 approval_required 或 deny。
4. MCP 内容默认是不可信外部内容，不能升级为系统指令。
5. Terminal 不接受模型直接生成的任意命令。
6. 工具输出进入模型上下文前必须 DLP/redaction。
7. 没有 OS sandbox 时必须在 runtime contract 和 design gap 中如实标注。

## 阶段范围

### 本阶段必须完成

```text
ToolActionPolicy
TerminalSandboxProfile
CommandRiskClassifier
BrowserActionClassifier
MCP process policy
MCP untrusted content policy
Output DLP pipeline
Tool artifact redaction
Execution boundary eval
Sandbox diagnostics
```

### 本阶段不做

```text
不新增 UI
不做云端 MCP 托管
不引入必须联网的大型沙箱平台
不允许 Skill/MCP 直接绕过 ToolRuntime
不允许 API handler 直接执行工具
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 21.1 | ToolActionPolicy 统一策略 | action category、risk、controls |
| 21.2 | Terminal sandbox 硬化 | cwd、env、allowlist、OS profile |
| 21.3 | MCP 进程与协议边界 | command allowlist、env_refs、scope |
| 21.4 | Browser/File/Network 动作分级 | read、write、submit、upload、download |
| 21.5 | 工具输出 DLP 与 artifact 脱敏 | output scanner、redacted artifacts |
| 21.6 | 执行边界评测与诊断 | allow/deny/approval/degraded evidence |

## 小阶段 21.1：ToolActionPolicy 统一策略

### 目标

把工具风险判断从分散逻辑收敛成统一策略模型。

### Policy 字段

```text
tool_name
source
action_category
risk_level
allowed_scopes
required_capabilities
required_asset_kinds
requires_task_binding
requires_approval_from
deny_patterns
output_dlp_policy
audit_level
```

### action_category

```text
read_only
local_write
artifact_write
external_navigation
external_submit
file_upload
file_download
terminal_command
mcp_tool_call
skill_step
account_draft
hardware_query
```

### 验收

```text
ToolRuntime 执行前加载 ToolActionPolicy
policy_snapshot 写入 tool_call
未知工具默认 deny 或 disabled
R5+ 动作不能默认 allow
```

## 小阶段 21.2：Terminal sandbox 硬化

### 目标

把 TerminalRunner 从 task artifact cwd 限制进一步推进到更强隔离。

### Sandbox profile

```text
profile_id
working_dir_policy
allowed_executables
denied_executables
env_policy
network_policy
filesystem_policy
timeout_seconds
max_output_bytes
os_sandbox_backend
degraded_reason
```

### OS backend 选项

```text
none_with_policy_guard
windows_low_integrity_process
windows_job_object
container
```

### 命令风险分级

```text
R1 read directory/status
R2 write artifact
R3 bulk move/copy within sandbox
R4 network publish/login action
R5 script execution/system modification
R6 sensitive exfiltration/payment
R7 persistent system or wallet signing
```

### 验收

```text
自定义 cwd 默认拒绝
访问用户目录、系统目录、密钥目录默认拒绝
脚本执行需要 approval 或 deny
命令 stdout/stderr 进入 artifact 前脱敏
无 OS sandbox 时 runtime contract 标注 degraded
```

## 小阶段 21.3：MCP 进程与协议边界

### 目标

让 MCP server 和 MCP tool 的所有入口都可预览、可控制、可审计。

### Server 启动前检查

```text
command_allowlist
args_schema
env_refs_only
no_inline_secret
server_scope
member_scope
allowed_skills
network_policy
safety_evaluation
```

### Tool call 前检查

```text
tool_schema_validation
capability_decision
safety_decision
approval_gate
asset_handle_resolution
untrusted_content_marker
output_dlp
```

### Resource/Prompt 规则

```text
MCP resource = untrusted_external_content
MCP prompt = template suggestion, not system instruction
MCP response cannot override developer/system policy
MCP output cannot directly become tool args for high-risk action
```

### 验收

```text
未知 MCP command 不能启动
env_refs 不能包含明文 key=value
未授权成员不能调用受限 MCP tool
MCP resource/prompt 进入上下文必须带不可信标记
MCP 输出中的 secret 被脱敏
```

## 小阶段 21.4：Browser/File/Network 动作分级

### 目标

把浏览器、文件和网络工具按真实风险分级，而不是都当作同一种工具调用。

### Browser action

```text
snapshot_read
navigation
form_fill
submit_post
download
upload
login_action
payment_action
```

### File action

```text
artifact_read
artifact_write
workspace_read
workspace_write
bulk_operation
sensitive_path_read
sensitive_path_write
delete
```

### Network action

```text
internal_loopback
trusted_domain_read
external_read
external_submit
webhook_call
```

### 验收

```text
read 和 submit 风险不同
upload/download 有 asset 和 destination 检查
delete 默认 approval 或 deny
外部提交必须写 destination policy
```

## 小阶段 21.5：工具输出 DLP 与 artifact 脱敏

### 目标

确保工具输出、MCP 响应、终端输出、浏览器快照和 artifact 不泄漏 secret。

### Scanner 覆盖

```text
api_key
token
private_key
cookie
wallet_seed
mnemonic
local_sensitive_path
email_or_phone_when_not_needed
account_identifier
```

### 输出处理

```text
raw_output_private
redacted_output_for_trace
redacted_artifact_for_model
blocked_output_reason
manual_review_required
```

### 验收

```text
trace/audit/tool_call/mcp_call/skill_run 不含 secret 明文
工具输出进入模型上下文前必须 redacted
DLP 命中高风险时阻断或等待审批
secret leakage scanner 覆盖工具输出表
```

## 小阶段 21.6：执行边界评测与诊断

### 目标

用可复跑证据证明工具边界，不只靠代码审查。

### 必测 case

```text
terminal custom cwd deny
terminal script approval
terminal sensitive path deny
browser submit approval
file delete approval_or_deny
MCP unknown command deny
MCP env inline secret deny
MCP untrusted prompt not elevated
Skill step cannot bypass ToolRuntime
tool output secret redacted
```

### 验收命令

```text
.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase21_execution_boundary.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

## 阶段总验收标准

第二十一阶段完成时必须满足：

```text
ToolActionPolicy 成为所有工具执行的统一策略入口
TerminalRunner 有明确 sandbox profile 和 degraded evidence
MCP server/tool/resource/prompt 边界可审计
浏览器、文件、网络动作按风险分级
工具输出全路径 DLP/redaction
所有执行路径覆盖 allow、deny、approval、degraded eval
```
