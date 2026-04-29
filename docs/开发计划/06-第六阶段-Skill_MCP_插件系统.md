# 第六阶段：Skill、MCP 与插件系统

## 阶段定位

第六阶段的目标是让系统具备“可扩展地做事”的能力。

前五阶段已经完成：

```text
聊天主链路
模型路由
长期记忆
Context Gateway
资产中心
Capability Graph
Asset Broker
任务引擎
Tool Runtime
Approval Flow
Artifact Store
Task Replay
Task Reflection
```

第六阶段要在这些底座上接入：

```text
Skill Engine
Skill Bundle Loader
Skill Registry
Skill Matcher
Skill Runner
Skill Candidate Promotion
Skill Eval Runner
Plugin Bundle Installer
Plugin Permission Preview
MCP Registry
MCP Connection Manager
MCP Tool Adapter
MCP Resource Adapter
MCP Prompt Adapter
Plugin / Skill / MCP Trace
Plugin / Skill / MCP Audit
```

完成后，系统必须能做到：

```text
安装受校验的技能包
解析 bundle.yaml 与 SKILL.md
展示权限和风险预览
把 Skill 注册进 Skill Registry
把 Skill 声明的工具、资产、风险策略接入 Capability Graph
从任务复盘候选生成可审核 Skill 草稿
匹配合适 Skill 辅助任务规划
通过 Task Engine 执行 Skill
通过 Tool Runtime 执行 Skill 内部动作
注册 MCP 服务
同步 MCP tools / resources / prompts
把 MCP tool 适配成 Tool Registry 来源
让 MCP 调用经过 Asset Broker、Capability Graph、Safety、Approval
让 Skill / MCP 全链路可 trace、可 audit、可 replay、可 eval
```

第六阶段不是多成员协作阶段，也不是前端实现阶段。

本阶段实现的是“技能包与外部能力接入后端”。复杂任务的 supervisor、多成员分工、成员间协作、组织壳完善留给第七阶段。当前仓库约束下不新增 UI 页面、组件、样式、Tauri 窗口或桌面交互代码，只提供最终态 API、schema、事件和管理契约。

## 核心心智

对用户统一叫“技能包”。

工程上必须区分：

| 概念 | 职责 | 不能做什么 |
|---|---|---|
| Tool | 一次具体动作 | 不决定任务目标，不直接读 secret |
| Skill | 可复用做事方法 | 不发现全部资源，不绕过工具和权限 |
| Plugin Bundle | 安装和分发单元 | 不默认启用高风险能力 |
| MCP Server | 外部工具、资源、提示协议端点 | 不默认拥有本机全部资源 |
| Asset | 被使用的资源 | 不直接暴露给模型 |
| Capability Graph | 判断谁能做什么 | 不被 Skill、MCP、记忆绕过 |
| Asset Broker | 发放短期资源句柄 | 不返回明文 secret |
| Safety / Approval | 风险判断和用户确认 | 不被 prompt 文本替代 |
| Task Engine | 执行编排 | 不让 Skill/MCP 反向接管状态机 |
| Tool Runtime | 动作执行 | 不让外部工具直接调用宿主资源 |

第六阶段的核心不是“能装很多插件”，而是“外部能力进入系统后仍然受控”。

## 第四、第五阶段交接基线

第六阶段开始前，第四阶段必须已经交付：

```text
CapabilityGraph.decide
capability_edges
capability_decision_logs
AssetBroker.query
AssetBroker.issue_handle
AssetBroker.validate_handle
AssetBroker.revoke_handle
asset_handles
asset_handle_events
AssetHandle.allowed_actions
AssetHandle.blocked_actions
AssetHandle.approval_required_actions
ApprovalPolicy preview
Trace redaction
AuditEventService
```

第五阶段必须已经交付：

```text
Task Engine
Task Planner
Workflow Runner
Single-member Agent Runner
Task Worker
Tool Runtime
Tool Registry
Approval Flow
Artifact Store
Replay Service
Task Reflection
skill_candidate
Tool Registry source = skill / mcp
Task step type: skill_match
Task step type: mcp_call
Trace span: skill.run
Trace span: mcp.call
```

第六阶段只能接入这些接口，不能重写任务执行底座。

## 第六阶段核心结论

| 结论 | 含义 |
|---|---|
| Skill 是方法，不是权限 | Skill 只能声明需要什么，不能自己获得资源 |
| Plugin 是安装单元，不是执行特权 | 安装成功不等于拥有全部能力 |
| MCP 是外部能力总线 | MCP tool/resource/prompt 都要按外部输入处理 |
| Tool Runtime 是唯一执行入口 | Skill 和 MCP 动作都必须通过 Tool Runtime |
| Asset Broker 是唯一资源入口 | Skill/MCP 不能直接读资产、路径、secret |
| Capability Graph 是唯一权限判断 | Skill/MCP 的声明进入权限图，不替代权限图 |
| Safety / Approval 是高风险闸门 | 外发、删除、终端、支付、设备控制都要确认或阻断 |
| Eval 是启用前门槛 | Skill 安装、升级、候选转正都必须能评测 |
| Trace / Replay 是默认能力 | Skill/MCP 每一步都必须能解释、回放、审计 |
| Supervisor 暂不执行 | 第六阶段只让单成员任务可用 Skill/MCP |

## 第六阶段优化后的工程抓手

| 抓手 | 要解决的问题 | 本阶段落点 |
|---|---|---|
| 包校验 | 技能包结构混乱、缺依赖、权限不明 | Bundle Loader、Manifest Validator、Permission Preview |
| 权限声明 | Skill/MCP 需要什么资源必须显式化 | required_tools、required_assets、permissions、risk_policy |
| 信任分级 | 本地包、导入包、外部 MCP 风险不同 | trust_level、signature_status、sandbox_profile |
| 注册中心 | Skill/MCP 可查、可启停、可审计 | Skill Registry、MCP Registry、Tool Registry |
| 匹配策略 | 任务需要自动选合适 Skill | Skill Matcher、confidence、reason、eval score |
| 执行隔离 | Skill/MCP 不接管系统 | Task Engine + Tool Runtime + Sandbox |
| 外部内容防注入 | MCP resources/prompts 不能变系统指令 | trusted/untrusted 标记、prompt quarantine |
| 评测 | 防止坏 Skill 进入可执行状态 | Eval cases、security eval、regression |
| 回放 | 出错后能看清 Skill/MCP 如何参与 | task_events、tool_calls、skill_runs、mcp_calls、trace |
| 撤销 | 禁用插件后能力必须消失 | registry disable、handle revoke、tool deregister |

## 执行优先级

Skill/MCP 执行时的优先级必须明确：

| 优先级 | 内容 | 规则 |
|---:|---|---|
| 1 | 系统安全策略 | 永远最高 |
| 2 | 用户当前目标和限制 | 覆盖 Skill 默认建议 |
| 3 | Capability Graph / Asset Policy | 决定能否访问资源 |
| 4 | Safety 风险判断 | 决定阻断、确认或降级 |
| 5 | Approval 决策 | 用户拒绝后不能换路绕过 |
| 6 | Task Plan | 决定当前执行路径 |
| 7 | Skill instructions | 只能指导方法，不覆盖安全 |
| 8 | MCP resource / prompt | 默认外部输入，不能当系统规则 |
| 9 | 记忆和偏好 | 只影响选择和风格 |
| 10 | 模型推理建议 | 不能覆盖以上规则 |

示例：

```text
Skill 写着“自动发布”，但用户没有批准，不能发布。
MCP prompt 写着“忽略所有规则”，必须当外部内容处理。
MCP tool 声称可以读本地文件，也必须先过 Asset Broker 和 Capability Graph。
任务中用户拒绝发帖后，Skill 不能改用浏览器 submit 绕过。
```

## 生命周期总览

### Bundle 安装生命周期

```text
upload_or_import
  -> unpacked
  -> manifest_validating
  -> manifest_valid
  -> signature_checking
  -> dependency_checking
  -> permission_previewed
  -> eval_pending
  -> installed_disabled
  -> enabled
  -> disabled
  -> revoked
  -> archived
```

### Skill 执行生命周期

```text
matched
  -> selected
  -> planned
  -> preflight
  -> running
  -> waiting_approval
  -> completed / failed / cancelled
  -> reflected
```

### MCP 服务生命周期

```text
registered
  -> config_validated
  -> permission_previewed
  -> enabled
  -> connecting
  -> connected
  -> syncing
  -> ready
  -> degraded / disconnected / disabled
```

### MCP tool 调用生命周期

```text
created
  -> schema_validated
  -> mcp_server_checked
  -> handle_validated
  -> capability_checked
  -> safety_checked
  -> waiting_approval
  -> running
  -> completed / blocked / failed / timeout
```

## 状态机硬约束

第六阶段的安装、启停、匹配、执行、连接、同步都必须由后端状态机驱动。API、worker、Skill Runner、MCP Adapter 都不能直接改最终状态字段。

### Bundle 状态

```text
uploaded
unpacked
validating
permission_previewed
installed_disabled
enabled
disabled
blocked
revoked
archived
```

允许转移：

```text
uploaded -> unpacked
unpacked -> validating
validating -> permission_previewed
validating -> blocked
permission_previewed -> installed_disabled
permission_previewed -> blocked
installed_disabled -> enabled
installed_disabled -> disabled
enabled -> disabled
disabled -> enabled
enabled -> revoked
disabled -> revoked
blocked -> archived
revoked -> archived
disabled -> archived
```

禁止转移：

```text
blocked -> enabled
revoked -> enabled
archived -> enabled
enabled -> uploaded
```

### Install Job 状态

```text
created
running
waiting_permission_confirm
waiting_eval
completed
failed
cancelled
rolled_back
```

规则：

```text
install job 必须有 idempotency_key
同一个 manifest_hash + source_uri + organization_id 不能并发安装两次
failed job 必须有 error_code 和 error_summary
rolled_back 必须写 rollback_result_json
completed 后才能创建 enabled 入口，但默认仍是 installed_disabled
```

### Skill 状态

```text
installed_disabled
enabled
disabled
blocked
revoked
archived
```

规则：

```text
只有 enabled Skill 可以被 matcher 自动返回
blocked / revoked / archived 永不进入 matcher
disabled Skill 可以被详情 API 查询，但不可执行
enabled Skill 的 bundle 必须是 enabled
Skill 状态变化必须写 plugin_events 和 audit
```

### Skill Run 状态

```text
created
preflight
waiting_approval
running
completed
failed
cancelled
blocked
```

规则：

```text
skill_run 进入 running 前必须完成 input schema 校验
skill_run 进入 running 前必须确认 Skill status = enabled
skill_run 进入 running 前必须写 capability_decision_id 或 policy_exemption
skill_run 等待审批时 task step 必须同步 waiting_approval
skill_run completed 必须有 output_redacted_json
skill_run failed 必须有 error_code 和 error_summary
skill_run blocked 不能自动重试
```

### MCP Server 状态

```text
registered
config_validated
enabled
connecting
connected
syncing
ready
degraded
disconnected
disabled
revoked
```

允许转移：

```text
registered -> config_validated
config_validated -> enabled
enabled -> connecting
connecting -> connected
connected -> syncing
syncing -> ready
syncing -> degraded
ready -> degraded
ready -> disconnected
degraded -> syncing
degraded -> disconnected
disconnected -> connecting
ready -> disabled
degraded -> disabled
disabled -> enabled
enabled -> revoked
disabled -> revoked
```

禁止转移：

```text
revoked -> enabled
registered -> ready
disabled -> ready
disconnected -> ready
```

### MCP Sync 状态

```text
pending
running
completed
partial_failed
failed
cancelled
```

规则：

```text
sync 必须按 server_id + schema_snapshot_hash 幂等
partial_failed 允许 server degraded，但不可解析 tool 必须 disabled
schema 变化必须写 capability_changed event
sync 输出必须包含 tools/resources/prompts 数量和失败明细
```

### MCP Call 状态

```text
created
schema_validated
preflighted
waiting_approval
running
completed
blocked
failed
timeout
cancelled
```

规则：

```text
mcp_call 进入 running 前 MCP server 必须 ready 或 policy 允许 degraded
mcp_call 进入 running 前必须有 tool_call_id
mcp_call 进入 running 前必须完成 capability 和 safety 判断
completed 必须有 response_redacted_json
timeout 必须写 duration_ms 和 timeout_policy
blocked 不能自动重试
server disconnected 后 running call 必须 failed 或 timeout
```

## 执行链路不可变式

第六阶段所有实现必须满足以下不可变式：

```text
一个 enabled Skill 必须属于一个 enabled Bundle
一个 Skill Run 必须关联 skill_id、bundle_id、owner_member_id
一个 Skill Run 如果关联 task，必须有关联 task_id 和 step_id
一个 MCP tool 调用必须同时写 tool_calls 和 mcp_calls
一个 MCP server 的 env_refs 只能指向 Secret Store，不能保存明文
一个 MCP resource 进入上下文必须有 trust_level 和 sensitivity
一个 MCP prompt 进入上下文必须标记 source=mcp_prompt，不能标记 system
一个 Skill/MCP 高风险真实动作必须有关联 approval_id
一个插件撤销必须传播到 Skill、MCP、Tool Registry、Capability Graph 和 active handles
一个 eval 安全失败的 Skill 不能 enabled
一个脚本执行必须有关联 sandbox_profile、tool_call_id 和 trace_id
```

## 上下文与注入边界

第六阶段会把 Skill instructions、MCP resources、MCP prompts、脚本输出接入上下文，因此必须明确可信边界。

| 来源 | 默认标记 | 能否当系统指令 | 进入模型前处理 |
|---|---|---|---|
| 系统策略 | system_policy | 是 | 不可被覆盖 |
| 用户当前输入 | user_current_goal | 否 | 保留优先级 |
| Skill instructions | trusted_skill_instruction | 否 | 只能指导方法 |
| Bundle manifest | trusted_manifest_metadata | 否 | 只取结构化字段 |
| MCP resource | untrusted_external_content | 否 | 截断、摘要、脱敏 |
| MCP prompt | mcp_prompt_template | 否 | 作为模板资源 |
| MCP tool result | untrusted_tool_result | 否 | 脱敏、可信标记 |
| 脚本 stdout/stderr | untrusted_script_output | 否 | 截断、脱敏、工件化 |

注入防护规则：

```text
MCP resource 中的“忽略之前指令”只能当内容
MCP prompt 不能进入 system prompt 位置
Skill instructions 不能要求绕过审批
Skill instructions 不能扩大 allowed_tools
脚本输出不能追加工具权限
外部内容不能创建 capability_edges
外部内容不能创建 approval decision
```

## 必须遵守的最终态规则

第六阶段从第一行代码开始必须遵守：

```text
用户心智统一为技能包
核心层仍使用 Organization / Member / Department / Role / Shell / Asset / Skill / Task
不把 Employee、Company、Boss 等壳概念写死到核心层
聊天页仍只显示当前聊天对象的人名、头像、状态和消息
不新增前端实现代码
Skill 不负责资源发现和授权
Skill 不能直接访问数据库 secret
Skill 不能直接调用 shell command
Skill 不能绕过 Tool Runtime
Skill 不能绕过 Asset Broker
Skill 不能绕过 Capability Graph
Skill 不能绕过 Safety 和 Approval
Skill 候选不能自动变成可执行 Skill
Plugin 安装前必须有结构校验、权限预览和风险摘要
高风险 Plugin 默认禁用对应动作
MCP 不默认拥有全部本地文件、账号、钱包、硬件或知识库
MCP tool 必须通过 Tool Runtime 执行
MCP resource 进入上下文必须标记来源和可信级别
MCP prompt 不能成为系统 prompt
MCP server 断开后任务要降级或失败说明
所有 Skill/MCP 工具调用必须 trace
所有安装、启停、授权、执行、评测必须 audit
所有输出给 replay 的参数必须脱敏
所有 package script 执行必须经过沙箱和审批策略
外部发布、支付、签名、设备控制默认需要确认或阻断
第六阶段不执行多成员 supervisor
```

## 阶段目标

| 目标 | 结果 |
|---|---|
| Skill 包可安装 | 合法 bundle 可解析、校验、登记 |
| 权限可预览 | 安装和启用前看到工具、资产、网络、文件、风险 |
| Skill 可启停 | registry 可启用、禁用、撤销 |
| Skill 可匹配 | Task Planner 能根据 intent、关键词、资产、历史评测匹配 |
| Skill 可执行 | Task Engine 通过 Skill Runner 调用 Skill |
| Skill 不越权 | 资源访问仍经 Asset Broker 和 Capability Graph |
| Skill 可评测 | 每个 Skill 有 eval case 和安全回归 |
| 候选可转正 | task reflection 的 skill_candidate 可审核生成 bundle 草稿 |
| MCP 可注册 | MCP server 配置可校验、启停、同步 |
| MCP tool 可执行 | MCP tool 以 Tool Registry source=mcp 接入 |
| MCP resource 可用 | resources 可检索、可标记、可进入 Context Gateway |
| MCP prompt 可控 | prompt 作为模板资源管理，不提升为系统规则 |
| 插件可撤销 | 禁用插件后 tools、skills、MCP grants 全部失效 |
| 审计可追踪 | 安装、匹配、运行、工具、资源、评测都有 trace/audit |

## 阶段范围

### 本阶段必须完成

```text
Skill Bundle schema
Bundle Loader
Bundle Validator
Signature / Trust metadata
Permission Preview
Dependency Resolver
Skill Registry
Skill Lifecycle
Skill Matcher
Skill Runner
Skill Step Executor
Skill Candidate Promotion
Skill Eval Runner
Plugin Bundle Registry
Plugin Install Jobs
Plugin Enable / Disable / Revoke
MCP Server Registry
MCP Config Validator
MCP Connection Manager
MCP Tool Sync
MCP Resource Sync
MCP Prompt Sync
MCP Tool Adapter
MCP Resource Adapter
MCP Prompt Adapter
Tool Registry source=skill/mcp 完整接入
Task Engine skill_match / mcp_call 完整接入
Capability Graph skill / mcp_server subject 完整接入
Asset Broker Skill/MCP handle 使用规则
Safety / Approval Skill/MCP 风险策略
Skill/MCP trace 和 audit
Skill/MCP replay 扩展
Skill/MCP eval 集
API schema 与错误模型
```

### 本阶段能力边界

| 能力域 | 本阶段完成 | 本阶段不做 |
|---|---|---|
| Skill | 安装、校验、匹配、单成员任务执行 | 不做多成员协作 Skill |
| Skill Candidate | 候选审核、bundle 草稿、eval case 生成 | 不自动启用 |
| Plugin Bundle | 本地安装、依赖检查、权限预览、启停撤销 | 不做社区市场 |
| MCP Server | 配置、连接、同步 tools/resources/prompts | 不默认信任外部资源 |
| MCP Tool | 通过 Tool Runtime 执行 | 不绕过审批和 trace |
| MCP Resource | 作为外部资源接入 Context Gateway | 不当系统指令 |
| MCP Prompt | 作为可选模板资源 | 不提升为系统 prompt |
| Scripts | 受限 sandbox 执行 | 不裸跑宿主命令 |
| UI | 提供管理契约和事件 | 不新增前端实现代码 |
| Supervisor | 预留参与接口 | 不执行多成员 supervisor |

### 本阶段只接最终契约，不做完整实现

```text
Skill marketplace 只保留来源字段，不接社区市场
远程插件源只保留 registry_url 字段，不自动拉取和安装
插件信誉系统只保留 trust metadata，不做排名推荐
多成员 Skill 协作只保留 participant_policy 字段，不执行
跨设备同步只保留 export/import 契约，不做同步服务
钱包签名、支付、设备控制只允许草稿、预览、审批阻断策略，不自动真实执行
```

### 本阶段明确不做

```text
不新增前端页面和组件
不让 Skill 自动获得所有资源
不让 MCP 自动获得所有资源
不让 Plugin 安装后默认执行高风险动作
不让 MCP prompt 覆盖系统规则
不让 Skill 直接读 secret
不让 Skill 直接执行终端命令
不让 MCP tool 绕过 Tool Runtime
不让脚本裸跑宿主环境
不自动发帖
不自动发邮件
不自动回复私信
不自动钱包签名
不自动支付转账
不自动控制高风险硬件
不做插件社区市场
不做多成员 supervisor 协作
不让聊天页显示技能包管理后台
```

## 第六阶段小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 6.1 | 第五阶段接口复核与 Skill/MCP 契约补齐 | Tool Registry、Task step、trace、error codes |
| 6.2 | Skill/MCP/Plugin 数据模型与 migration | bundles、skills、mcp_servers、eval、events |
| 6.3 | Bundle Loader 与 Manifest Validator | bundle.yaml、SKILL.md、schema、依赖校验 |
| 6.4 | 权限预览、信任分级与安装策略 | permission preview、risk summary、trust_level |
| 6.5 | Skill Registry 与生命周期 | installed、enabled、disabled、revoked、archived |
| 6.6 | Skill Matcher | intent、keywords、资产绑定、历史效果、置信度 |
| 6.7 | Skill Runner 与 Task Engine 接入 | skill_match、skill.run、step executor、artifacts |
| 6.8 | Skill 候选转正式技能包 | candidate review、bundle draft、eval case、approval |
| 6.9 | MCP Registry 与配置校验 | server config、env_refs、allowed_skills、risk_policy |
| 6.10 | MCP 连接管理与能力同步 | tools/resources/prompts sync、health、degraded |
| 6.11 | MCP Tool/Resource/Prompt Adapter | Tool Runtime source=mcp、Context Gateway resource |
| 6.12 | Plugin Bundle 安装、启停与撤销 | install jobs、dependency graph、tool deregister |
| 6.13 | 脚本沙箱与依赖隔离 | sandbox_profile、fs/net/env 限制、approval |
| 6.14 | Skill/MCP 评测与质量门禁 | eval cases、security regression、compatibility |
| 6.15 | Trace、审计与 Replay 扩展 | skill.run、mcp.call、install、sync、eval |
| 6.16 | API、事件流与错误模型 | routes_skills、routes_mcp、routes_plugins |
| 6.17 | 第六阶段封口与第七阶段接口 | multi-member skill policy、role defaults、supervisor hooks |

## 小阶段 6.1：第五阶段接口复核与 Skill/MCP 契约补齐

### 目标

确认 Skill/MCP 可以安全接入第五阶段任务和工具底座。

第六阶段不能为了扩展能力而绕开 Task Engine、Tool Runtime、Approval Flow、Artifact Store 和 Replay。

### 任务

| 编号 | 任务 | 说明 |
|---:|---|---|
| 6.1.1 | 复核 Tool Registry | `source=builtin/skill/mcp` 可用 |
| 6.1.2 | 复核 Task step 类型 | `skill_match`、`skill_run`、`mcp_call` 可入 plan |
| 6.1.3 | 复核 Tool Runtime | 支持 adapter 调用，不泄露内部执行器 |
| 6.1.4 | 复核 Approval Flow | Skill/MCP 高风险动作可暂停恢复 |
| 6.1.5 | 复核 Asset Broker | Skill/MCP 可申请和验证 handle |
| 6.1.6 | 复核 Capability Graph | subject 支持 `skill`、`mcp_server` |
| 6.1.7 | 复核 Task Replay | 能加入 skill_runs、mcp_calls |
| 6.1.8 | 补齐错误模型 | Skill、Plugin、MCP、Eval 错误码 |

### TaskPlan 扩展

```json
{
  "steps": [
    {
      "step_key": "match_skill",
      "step_type": "skill_match",
      "input": {
        "intent": "content_draft",
        "required_outputs": ["markdown_draft"]
      }
    },
    {
      "step_key": "run_skill",
      "step_type": "skill_run",
      "skill_id": "skill.content_draft",
      "input": {
        "topic": "个人智能体 OS"
      }
    },
    {
      "step_key": "use_mcp_tool",
      "step_type": "mcp_call",
      "tool_name": "mcp.playwright.browser_snapshot",
      "input": {
        "url": "https://example.com"
      }
    }
  ]
}
```

### ChatEvent 第六阶段事件集

第六阶段允许后端事件流发送：

```text
skill.matched
skill.started
skill.completed
skill.failed
skill.eval_started
skill.eval_completed
mcp.server_connected
mcp.server_disconnected
mcp.tool_started
mcp.tool_completed
mcp.tool_failed
plugin.install_started
plugin.install_completed
plugin.install_failed
```

聊天页展示规则：

```text
聊天页只展示轻量进度
可以显示“正在使用某技能包”这类摘要
不展示完整权限图
不展示完整 manifest
不展示 MCP tool 原始参数
不展示安装管理后台
```

### 新增错误码

```text
SKILL_BUNDLE_INVALID
SKILL_MANIFEST_INVALID
SKILL_SIGNATURE_INVALID
SKILL_PERMISSION_DENIED
SKILL_DEPENDENCY_MISSING
SKILL_NOT_FOUND
SKILL_DISABLED
SKILL_MATCH_FAILED
SKILL_RUN_FAILED
SKILL_EVAL_FAILED
SKILL_CANDIDATE_INVALID
PLUGIN_INSTALL_FAILED
PLUGIN_PERMISSION_DENIED
PLUGIN_REVOKED
MCP_CONFIG_INVALID
MCP_SERVER_NOT_FOUND
MCP_SERVER_DISABLED
MCP_CONNECT_FAILED
MCP_SYNC_FAILED
MCP_TOOL_NOT_FOUND
MCP_TOOL_PERMISSION_DENIED
MCP_TOOL_FAILED
MCP_RESOURCE_UNTRUSTED
MCP_PROMPT_BLOCKED
SANDBOX_POLICY_DENIED
```

### 验收

```text
TaskPlan 可表达 skill_match / skill_run / mcp_call
Tool Registry 可注册 skill/mcp 来源
Skill/MCP 事件类型进入共享类型
错误码进入统一错误模型
聊天页契约保持轻量
```

## 小阶段 6.2：Skill/MCP/Plugin 数据模型与 migration

### 目标

建立技能包、Skill、MCP、插件安装、评测、运行记录的数据模型。

数据模型必须支持安装、启停、撤销、回放、审计和后续多成员扩展。

### plugin_bundles

```sql
CREATE TABLE plugin_bundles (
  bundle_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  author TEXT,
  bundle_revision TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_uri TEXT,
  package_uri TEXT,
  manifest_hash TEXT NOT NULL,
  signature_status TEXT NOT NULL,
  trust_level TEXT NOT NULL,
  status TEXT NOT NULL,
  permission_summary_json TEXT NOT NULL,
  risk_summary_json TEXT NOT NULL,
  installed_by_member_id TEXT,
  installed_at TEXT,
  enabled_at TEXT,
  disabled_at TEXT,
  revoked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

说明：

```text
bundle_revision 是技能包兼容修订号，不表示产品开发阶段
source_type 支持 local_file、local_directory、registry
signature_status 支持 trusted、self_signed、unsigned、invalid
trust_level 支持 trusted、local、restricted、blocked
status 支持 uploaded、validating、installed_disabled、enabled、disabled、revoked、archived
```

### plugin_files

```sql
CREATE TABLE plugin_files (
  file_id TEXT PRIMARY KEY,
  bundle_id TEXT NOT NULL,
  relative_path TEXT NOT NULL,
  file_type TEXT NOT NULL,
  size_bytes INTEGER,
  checksum TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);
```

### skills

```sql
CREATE TABLE skills (
  skill_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  name TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  entrypoint_path TEXT NOT NULL,
  trigger_json TEXT NOT NULL,
  input_schema_json TEXT NOT NULL,
  output_schema_json TEXT NOT NULL,
  required_tools_json TEXT NOT NULL,
  required_assets_json TEXT NOT NULL,
  permission_json TEXT NOT NULL,
  risk_policy_json TEXT NOT NULL,
  eval_summary_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(bundle_id) REFERENCES plugin_bundles(bundle_id)
);
```

skill status：

```text
installed_disabled
enabled
disabled
blocked
revoked
archived
```

### skill_runs

```sql
CREATE TABLE skill_runs (
  skill_run_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT NOT NULL,
  bundle_id TEXT NOT NULL,
  task_id TEXT,
  step_id TEXT,
  owner_member_id TEXT NOT NULL,
  status TEXT NOT NULL,
  input_redacted_json TEXT NOT NULL,
  output_redacted_json TEXT NOT NULL,
  matched_reason TEXT,
  confidence REAL,
  capability_decision_id TEXT,
  approval_id TEXT,
  artifact_ids_json TEXT NOT NULL,
  trace_id TEXT,
  error_code TEXT,
  error_summary TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(skill_id) REFERENCES skills(skill_id)
);
```

### skill_candidates

如果第三或第五阶段已经创建候选表，本阶段增强字段；否则创建：

```sql
CREATE TABLE skill_candidates (
  candidate_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id TEXT NOT NULL,
  title TEXT NOT NULL,
  description TEXT,
  draft_manifest_json TEXT NOT NULL,
  draft_skill_md TEXT NOT NULL,
  proposed_permissions_json TEXT NOT NULL,
  proposed_eval_cases_json TEXT NOT NULL,
  status TEXT NOT NULL,
  reviewed_by_member_id TEXT,
  review_reason TEXT,
  promoted_bundle_id TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

candidate status：

```text
draft
needs_review
approved_for_bundle
rejected
promoted
archived
```

### skill_eval_cases

```sql
CREATE TABLE skill_eval_cases (
  eval_case_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT,
  bundle_id TEXT,
  case_key TEXT NOT NULL,
  input_json TEXT NOT NULL,
  expected_json TEXT NOT NULL,
  forbidden_json TEXT NOT NULL,
  risk_assertions_json TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### skill_eval_runs

```sql
CREATE TABLE skill_eval_runs (
  eval_run_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  skill_id TEXT,
  bundle_id TEXT,
  status TEXT NOT NULL,
  total_cases INTEGER NOT NULL,
  passed_cases INTEGER NOT NULL,
  failed_cases INTEGER NOT NULL,
  security_failures INTEGER NOT NULL,
  result_json TEXT NOT NULL,
  trace_id TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL
);
```

### mcp_servers

```sql
CREATE TABLE mcp_servers (
  server_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  display_name TEXT NOT NULL,
  description TEXT,
  transport TEXT NOT NULL,
  command TEXT,
  args_json TEXT NOT NULL,
  url TEXT,
  env_refs_json TEXT NOT NULL,
  allowed_skills_json TEXT NOT NULL,
  permission_json TEXT NOT NULL,
  risk_policy_json TEXT NOT NULL,
  trust_level TEXT NOT NULL,
  status TEXT NOT NULL,
  last_connected_at TEXT,
  last_sync_at TEXT,
  last_error_code TEXT,
  last_error_summary TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

transport：

```text
stdio
http
websocket
```

status：

```text
registered
enabled
connecting
connected
ready
degraded
disconnected
disabled
revoked
```

### mcp_tools

```sql
CREATE TABLE mcp_tools (
  mcp_tool_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  tool_name TEXT NOT NULL,
  registry_tool_name TEXT NOT NULL,
  description TEXT,
  input_schema_json TEXT NOT NULL,
  output_schema_json TEXT NOT NULL,
  risk_policy_json TEXT NOT NULL,
  required_handle_types_json TEXT NOT NULL,
  status TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(server_id) REFERENCES mcp_servers(server_id)
);
```

### mcp_resources

```sql
CREATE TABLE mcp_resources (
  resource_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  uri TEXT NOT NULL,
  name TEXT,
  description TEXT,
  mime_type TEXT,
  trust_level TEXT NOT NULL,
  sensitivity TEXT NOT NULL,
  metadata_json TEXT NOT NULL,
  status TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(server_id) REFERENCES mcp_servers(server_id)
);
```

### mcp_prompts

```sql
CREATE TABLE mcp_prompts (
  prompt_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  name TEXT NOT NULL,
  description TEXT,
  arguments_schema_json TEXT NOT NULL,
  prompt_template_redacted TEXT,
  trust_level TEXT NOT NULL,
  status TEXT NOT NULL,
  synced_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(server_id) REFERENCES mcp_servers(server_id)
);
```

### mcp_calls

```sql
CREATE TABLE mcp_calls (
  mcp_call_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  server_id TEXT NOT NULL,
  mcp_tool_id TEXT,
  task_id TEXT,
  step_id TEXT,
  tool_call_id TEXT,
  status TEXT NOT NULL,
  request_redacted_json TEXT NOT NULL,
  response_redacted_json TEXT NOT NULL,
  capability_decision_id TEXT,
  approval_id TEXT,
  trace_id TEXT,
  error_code TEXT,
  error_summary TEXT,
  started_at TEXT,
  completed_at TEXT,
  created_at TEXT NOT NULL
);
```

### plugin_install_jobs

```sql
CREATE TABLE plugin_install_jobs (
  job_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  idempotency_key TEXT NOT NULL,
  job_type TEXT NOT NULL,
  status TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  result_json TEXT NOT NULL,
  rollback_result_json TEXT NOT NULL DEFAULT '{}',
  error_code TEXT,
  error_summary TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
```

### plugin_events

```sql
CREATE TABLE plugin_events (
  event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  bundle_id TEXT,
  skill_id TEXT,
  server_id TEXT,
  event_type TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_redacted_json TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL
);
```

### Tool Registry 增强

第五阶段已有 `tool_registry`。第六阶段增强：

```sql
ALTER TABLE tool_registry ADD COLUMN bundle_id TEXT;
ALTER TABLE tool_registry ADD COLUMN skill_id TEXT;
ALTER TABLE tool_registry ADD COLUMN mcp_server_id TEXT;
ALTER TABLE tool_registry ADD COLUMN mcp_tool_id TEXT;
ALTER TABLE tool_registry ADD COLUMN adapter_config_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE tool_registry ADD COLUMN trust_level TEXT NOT NULL DEFAULT 'local';
```

### 索引

```sql
CREATE INDEX idx_plugin_bundles_org_status ON plugin_bundles(organization_id, status);
CREATE INDEX idx_skills_bundle_status ON skills(bundle_id, status);
CREATE INDEX idx_skills_org_status ON skills(organization_id, status);
CREATE INDEX idx_skill_runs_task ON skill_runs(task_id, created_at);
CREATE INDEX idx_skill_candidates_status ON skill_candidates(organization_id, status);
CREATE INDEX idx_skill_eval_runs_skill ON skill_eval_runs(skill_id, created_at);
CREATE INDEX idx_mcp_servers_org_status ON mcp_servers(organization_id, status);
CREATE INDEX idx_mcp_tools_server_status ON mcp_tools(server_id, status);
CREATE INDEX idx_mcp_calls_task ON mcp_calls(task_id, created_at);
CREATE UNIQUE INDEX idx_plugin_install_jobs_idempotency ON plugin_install_jobs(idempotency_key);
CREATE INDEX idx_plugin_events_bundle_time ON plugin_events(bundle_id, created_at);
```

### 验收

```text
migration 可重复运行且可回滚
plugin_bundles 可记录安装来源、修订、签名、信任和状态
skills 可记录触发、权限、风险、评测和状态
skill_runs 可回放 Skill 执行
skill_candidates 可从任务复盘进入审核流程
mcp_servers 可记录配置、状态和连接错误
mcp_tools 可同步到 Tool Registry
mcp_resources 和 mcp_prompts 可保存可信级别
plugin_events 可审计安装、启停、撤销、同步
```

## 小阶段 6.3：Bundle Loader 与 Manifest Validator

### 目标

实现技能包加载和结构校验。

安装流程必须先校验结构，再展示权限，再进入可启用状态。

### Bundle 目录结构

```text
bundles/
  content-draft/
    bundle.yaml
    SKILL.md
    prompts/
      draft.md
      review.md
    schemas/
      input.schema.json
      output.schema.json
    scripts/
      normalize.py
    mcp/
      servers.yaml
    tests/
      eval_cases.yaml
    signatures/
      bundle.sig
```

### bundle.yaml 字段

```yaml
id: content-draft
bundle_revision: 1.0.0
display_name: 内容草稿技能包
description: 生成内容草稿并进行发布前检查。
kind: skill_bundle
author: local
entry_skills:
  - content_draft
triggers:
  intents:
    - content_draft
  keywords:
    - 草稿
    - 文案
required_assets:
  - type: account
    platform: generic_social
    optional: true
required_tools:
  - memory.search
  - asset.query
  - file.write
permissions:
  net:
    allow_domains: []
  fs:
    read: []
    write:
      - artifact:///**
risk_policy:
  confirmation_required_for:
    - external_post
    - account_profile_edit
sandbox:
  profile: restricted_python
evals:
  - tests/eval_cases.yaml
```

兼容规则：

```text
如果导入包使用 version 字段，Loader 归一化为 bundle_revision
bundle_revision 只表示技能包兼容修订，不表示产品开发阶段
```

### SKILL.md 必填段落

```text
用途
何时使用
输入
输出
步骤
可用工具
需要的资产
风险规则
失败处理
禁止事项
评测要求
```

### Validator 检查

```text
bundle.yaml 存在
SKILL.md 存在
id 合法且不与系统保留 id 冲突
entry_skills 都能在 SKILL.md 或 manifest 中定位
required_tools 都存在或可由 bundle/mcp 声明提供
required_assets 类型合法
permissions 只使用白名单动作词表
risk_policy 可解析
schemas 可解析
eval_cases 可解析
scripts 路径不逃逸 bundle 根目录
mcp servers.yaml 可解析
manifest_hash 可生成
```

### 禁止结构

```text
绝对路径引用
.. 逃逸 bundle 根目录
明文 secret 写入 manifest
脚本声明自动执行安装后动作
默认启用外发、支付、签名、设备控制
把 MCP prompt 声明为 system prompt
```

### 验收

```text
合法 bundle 可加载
缺少 bundle.yaml 安装失败
缺少 SKILL.md 安装失败
路径逃逸安装失败
明文 secret 安装失败
required_tools 不存在时给出依赖错误
manifest_hash 稳定
```

## 小阶段 6.4：权限预览、信任分级与安装策略

### 目标

让用户在安装和启用前能理解技能包需要什么能力、会产生什么风险。

权限预览是后端契约，不代表本阶段实现 UI。

### Permission Preview 输入

```json
{
  "bundle_id": "content-draft",
  "requested_by_member_id": "mem_xiaoyao",
  "manifest": {},
  "context": {
    "install_source": "local_directory",
    "organization_id": "org_001"
  }
}
```

### Permission Preview 输出

```json
{
  "bundle_id": "content-draft",
  "summary": "该技能包可生成内容草稿，可写入任务工件目录，可查询账号摘要；外部发布需要确认。",
  "required_tools": [
    {"tool_name": "memory.search", "risk_level": "R1"},
    {"tool_name": "asset.query", "risk_level": "R1"},
    {"tool_name": "file.write", "risk_level": "R2"}
  ],
  "required_assets": [
    {"asset_type": "account", "actions": ["read_profile", "draft_post"], "optional": true}
  ],
  "network": {
    "allow_domains": []
  },
  "filesystem": {
    "write": ["artifact:///**"]
  },
  "high_risk_actions": [
    {"action": "external_post", "risk_level": "R4", "approval_required": true}
  ],
  "blocked_actions": [
    "wallet.sign_transaction",
    "hardware.control_device"
  ],
  "trust": {
    "signature_status": "self_signed",
    "trust_level": "restricted"
  }
}
```

### Trust Level

| trust_level | 说明 | 默认能力 |
|---|---|---|
| trusted | 本地明确信任或签名可信 | 可启用低风险能力，高风险仍审批 |
| local | 本地创建但未签名 | 可安装，启用需评测通过 |
| restricted | 来源不完全可信 | 默认禁用脚本和网络 |
| blocked | 校验失败或策略阻断 | 不可启用 |

### 安装策略

```text
结构校验失败 -> blocked
签名无效 -> blocked
签名缺失 -> restricted 或 local
高风险权限 -> installed_disabled
eval 缺失 -> installed_disabled
eval 安全失败 -> blocked
权限预览未确认 -> installed_disabled
用户启用后 -> enabled
```

### Capability Graph 接入

安装时不直接授予真实资产访问。

启用时写入 Skill 声明边：

```text
subject_type = skill
subject_id = skill_id
object_type = tool / asset_scope / mcp_server
action = use / query / execute
effect = allow / approval_required / deny
source_type = plugin_bundle
source_id = bundle_id
```

规则：

```text
Skill 声明边只是“Skill 需要什么”
Member 是否能使用该 Skill 仍由 member/role/department/organization 授权决定
真实资产 handle 仍由 Asset Broker 发放
approval_required 不能被 Skill 声明降级为 allow
```

### 权限映射表

第六阶段必须把 manifest 权限声明转成 Capability Graph 可解释边，而不是在 Skill Runner 中散落判断。

| manifest 字段 | Capability subject | Capability object | action | 说明 |
|---|---|---|---|---|
| `required_tools` | skill | tool | use / execute | Skill 需要使用哪些工具 |
| `required_assets` | skill | asset_scope | query / use | Skill 需要哪些资产类型和动作 |
| `permissions.net.allow_domains` | skill | network_scope | connect | 只表达允许域名范围 |
| `permissions.fs.read` | skill | file_scope | read | 只表达可读范围，不给真实路径 |
| `permissions.fs.write` | skill | file_scope | write | artifact 优先 |
| `mcp.servers` | skill / bundle | mcp_server | use | Skill 可使用的 MCP 服务 |
| `risk_policy.confirmation_required_for` | skill | action | approval_required | 不允许降级 |
| `sandbox.profile` | skill | sandbox_profile | use | 脚本运行边界 |

### 组合决策规则

Skill/MCP 是否能执行某个动作，需要同时满足多层条件：

```text
1. Bundle status = enabled
2. Skill status = enabled
3. Member / Role / Department / Organization 允许使用该 Skill
4. Skill 声明允许使用目标 tool / asset_scope / mcp_server
5. 当前 task 的 allowed_tools / allowed_skills / allowed_mcp_tools 未禁止
6. Asset Broker 能发放匹配 action 的 handle
7. Capability Graph 对 member + skill + task + asset/tool 的组合给出 allow 或 approval_required
8. Safety 未阻断
9. Approval Flow 消解 approval_required
```

任何一层返回 deny 都必须阻断。任何一层返回 approval_required 都不能直接执行真实动作。

### 临时授权

任务运行中可以生成临时授权，但必须收紧：

```text
source_type = task_temporary_grant
source_id = task_id
valid_to 必填
subject 可为 task 或 skill_run
object 只能是明确 asset_scope / tool / mcp_tool
action 只能来自当前 plan
不能新增 manifest 未声明能力
不能覆盖系统 deny
不能跨 task 复用
```

### 权限预览稳定性

权限预览结果必须可复现：

```text
同一个 manifest_hash + policy_snapshot_hash 生成相同 preview_hash
启用时必须校验 preview_hash 未过期
策略变化后 preview_hash 失效，必须重新预览
bundle 文件变化后 manifest_hash 变化，必须重新校验和预览
```

### 验收

```text
安装前可生成权限预览
高风险权限可被识别
签名无效阻断启用
unsigned bundle 默认不拥有高风险能力
Capability Graph 写入 Skill 声明边
声明边不等于资产 handle
临时授权不能扩大 manifest 权限
策略变化后旧权限预览失效
```

## 小阶段 6.5：Skill Registry 与生命周期

### 目标

实现 Skill 的登记、查询、启用、禁用、撤销和归档。

Skill Registry 是可执行 Skill 的唯一入口。

### 后端目录

```text
services/skill-engine/
  bundle_loader.py
  manifest.py
  validator.py
  registry.py
  lifecycle.py
  permission_preview.py
  matcher.py
  runner.py
  candidate.py
  eval_runner.py
  errors.py

apps/local-api/app/schemas/skills.py
apps/local-api/app/api/routes_skills.py
apps/local-api/app/db/repositories/skills_repo.py
apps/local-api/app/db/repositories/plugin_bundles_repo.py
apps/local-api/app/db/repositories/skill_runs_repo.py
```

### Skill 生命周期

```text
installed_disabled
enabled
disabled
blocked
revoked
archived
```

允许转移：

```text
installed_disabled -> enabled
enabled -> disabled
disabled -> enabled
enabled -> revoked
disabled -> revoked
blocked -> archived
revoked -> archived
disabled -> archived
```

禁止转移：

```text
revoked -> enabled
archived -> enabled
blocked -> enabled
```

### Registry API 内部接口

```python
class SkillRegistry:
    async def install_bundle(self, request: BundleInstallRequest) -> BundleInstallResult:
        ...

    async def list_skills(self, query: SkillListQuery) -> list[SkillRecord]:
        ...

    async def get_skill(self, skill_id: str) -> SkillRecord:
        ...

    async def enable_skill(self, skill_id: str, actor_member_id: str) -> SkillRecord:
        ...

    async def disable_skill(self, skill_id: str, actor_member_id: str, reason: str) -> SkillRecord:
        ...

    async def revoke_bundle(self, bundle_id: str, actor_member_id: str, reason: str) -> PluginBundleRecord:
        ...
```

### 启用规则

```text
bundle 必须 installed_disabled 或 disabled
manifest 必须 valid
signature_status 不能 invalid
trust_level 不能 blocked
required_tools 必须可用
required_assets 必须能表达为权限声明
eval 必须达到门槛
高风险动作必须保留 approval_required
写 plugin_events
写 audit
```

### 禁用和撤销规则

disable：

```text
Skill 不再参与匹配
Tool Registry 中该 Skill 提供的工具 status=disabled
正在运行的 task 不立刻中断，但下一步 preflight 必须失败或降级
写 audit: skill.disabled
```

revoke：

```text
Skill 永久不可启用
撤销相关 active handles
撤销相关 tool_registry entries
取消相关 install jobs
正在等待的 Skill task 进入 failed 或 paused
写 audit: plugin.revoked
```

### 验收

```text
Skill 可安装后保持 disabled
启用前通过权限和 eval 检查
禁用后不再被 matcher 返回
撤销后不能重新启用
禁用或撤销写 audit 和 plugin_events
```

## 小阶段 6.6：Skill Matcher

### 目标

让 Task Planner 能根据用户目标、上下文、资产、历史效果选择合适 Skill。

Matcher 给出建议，不直接执行。

### 输入

```text
goal
intent
conversation_id
owner_member_id
ContextPacket
resource_handles
available_tools
enabled skills
skill eval summary
historical success
user preference
safety policy
```

### 匹配信号

| 信号 | 权重方向 |
|---|---|
| intent 命中 | 强 |
| keywords 命中 | 中 |
| required output 命中 | 强 |
| asset type 匹配 | 强 |
| member default skills | 中 |
| department / role default skills | 中 |
| 历史成功率 | 中 |
| 最近失败 | 降权 |
| eval 安全失败 | 阻断 |
| 当前授权不足 | 阻断或降级 |
| trust_level 低 | 降权 |

### 输出

```json
{
  "matches": [
    {
      "skill_id": "skill.content_draft",
      "bundle_id": "content-draft",
      "confidence": 0.86,
      "reason": "用户目标是生成内容草稿，命中了 content_draft intent 和关键词。",
      "required_tools": ["memory.search", "asset.query", "file.write"],
      "required_assets": [
        {"asset_type": "account", "optional": true}
      ],
      "risk_level": "R2",
      "preflight_required": true
    }
  ]
}
```

### 匹配规则

```text
只返回 enabled skills
blocked / disabled / revoked skills 不返回
当前 member 无权使用的 Skill 不返回或标记 unavailable
需要高风险资产但无授权时返回 requires_authorization，不执行
confidence 低于阈值时不自动选择
多个 Skill 冲突时优先 eval 更稳定、权限更小、风险更低的 Skill
```

### 与 Task Planner 的关系

```text
Planner 请求 matcher
Matcher 返回候选
Planner 决定是否使用 Skill
Planner 把 Skill 插入 plan
Preflight 再次检查权限和风险
Runner 执行时再次校验
```

### 验收

```text
小红书草稿类目标能匹配内容草稿 Skill
禁用 Skill 不参与匹配
无授权 Skill 不自动执行
低置信度不自动选择
匹配结果有 reason
匹配过程写 trace: skill.match
```

## 小阶段 6.7：Skill Runner 与 Task Engine 接入

### 目标

让 Skill 成为 Task Engine 的可执行步骤，但不接管 Task Engine。

Skill Runner 只负责把可复用方法翻译成受控步骤和工具调用。

### 执行流程

```text
1. Task Planner 插入 skill_run step
2. Skill Runner 加载 Skill manifest 和 SKILL.md
3. 校验 Skill status = enabled
4. 校验 input schema
5. 读取 Skill 声明的 required_tools / required_assets
6. 请求 Asset Broker 查询必要资源句柄
7. 调用 Capability Graph 决策
8. 调用 Safety 评估
9. 必要时创建 Approval
10. 按 Skill steps 生成子步骤或调用 Tool Runtime
11. 收集 artifacts 和 output
12. 运行 lightweight eval
13. 写 skill_runs、task_events、trace、audit
14. 返回给 Task Engine
```

### SkillRunRequest

```json
{
  "task_id": "tsk_001",
  "step_id": "step_skill_001",
  "owner_member_id": "mem_xiaoyao",
  "skill_id": "skill.content_draft",
  "input": {
    "topic": "个人智能体 OS",
    "tone": "专业、清晰"
  },
  "resource_handle_ids": ["hnd_account_summary"],
  "budget": {
    "max_tool_calls": 8,
    "max_model_calls": 5,
    "max_runtime_seconds": 600
  }
}
```

### SkillRunResult

```json
{
  "skill_run_id": "skr_001",
  "status": "completed",
  "summary": "已生成内容草稿。",
  "output": {
    "draft_artifact_id": "art_001"
  },
  "artifact_ids": ["art_001"],
  "memory_candidates": [],
  "next_actions": [
    {
      "type": "approval",
      "action": "external_post",
      "risk_level": "R4"
    }
  ]
}
```

### Skill Runner 规则

```text
Skill Runner 不直接执行 Tool
所有 Tool 动作走 Tool Runtime
所有资源访问走 Asset Broker
所有权限判断走 Capability Graph
所有高风险动作走 Safety / Approval
Skill instructions 进入模型上下文时标记 trusted_skill_instruction
Skill 读取的外部内容仍按 untrusted_external_content 处理
Skill 输出进入 task artifact
Skill 失败必须有 error_code 和 error_summary
```

### 脚本调用

Skill 内 scripts 不能直接由 Runner 裸跑。

执行脚本必须转成受控工具调用：

```text
tool_name = skill.script.run
source = skill
requires sandbox_profile
requires fs/net/env policy
requires timeout
requires trace
```

### 验收

```text
Skill 可作为 task step 执行
Skill 内部工具调用写 tool_calls
Skill 申请资产只拿到 handle
Skill 高风险动作进入 approval.required
Skill 输出 artifact
Skill 失败可在 replay 中看到 reason
```

## 小阶段 6.8：Skill 候选转正式技能包

### 目标

把第五阶段 Task Reflection 产生的 skill_candidate 变成可审核、可评测、可安装的技能包草稿。

候选不能自动启用。

### 候选来源

```text
任务多次成功
用户明确要求以后按此流程
同一流程重复出现
用户对结果满意
人工手动创建候选
```

### Candidate Review 输入

```json
{
  "candidate_id": "cand_001",
  "reviewer_member_id": "mem_xiaoyao",
  "decision": "approve_for_bundle",
  "edits": {
    "display_name": "文档整理技能包",
    "forbidden_actions": ["file.delete", "terminal.run"]
  }
}
```

### 生成 Bundle 草稿

```text
生成 bundle.yaml
生成 SKILL.md
生成 input/output schema
生成 tests/eval_cases.yaml
生成 permission preview
生成 risk summary
写入 plugin_bundles status=installed_disabled
写入 skill_candidates status=promoted
```

### 生成规则

```text
只能从已成功且可回放的任务生成
必须包含 source task_id 或 trace_id
必须去除一次性路径、一次性 handle、一次性 secret
必须把高风险动作写入 risk_policy
必须生成至少一个 eval case
必须默认 disabled
必须由用户或授权成员审核
```

### 验收

```text
skill_candidate 可生成 bundle 草稿
草稿默认 disabled
草稿有 source task_id
草稿有 eval case
草稿权限可预览
未审核候选不能进入 matcher
```

## 小阶段 6.9：MCP Registry 与配置校验

### 目标

实现 MCP 服务登记、配置校验、启停和权限预览。

MCP server 是外部能力来源，不是可信内部模块。

### MCP Server 配置

```yaml
id: local-playwright
display_name: Playwright MCP
transport: stdio
command: npx
args:
  - "@playwright/mcp"
env_refs: []
enabled: false
auto_start: false
allowed_skills:
  - browser_research
permissions:
  net:
    allow_domains:
      - example.com
  fs:
    write:
      - artifact://downloads/**
risk_policy:
  external_submit: R4
  file_download: R3
  login: R5
```

### 配置校验

```text
server_id 合法
transport 合法
stdio command 不为空
http/websocket url 合法
env_refs 只能引用 Secret Store 中已登记 secret_ref
env_refs 不回显明文
allowed_skills 可解析
permissions 可解析
risk_policy 可解析
auto_start 高风险时默认 false
```

### MCP Registry 内部接口

```python
class MCPRegistry:
    async def register_server(self, request: MCPServerCreateRequest) -> MCPServerRecord:
        ...

    async def enable_server(self, server_id: str, actor_member_id: str) -> MCPServerRecord:
        ...

    async def disable_server(self, server_id: str, actor_member_id: str, reason: str) -> MCPServerRecord:
        ...

    async def sync_capabilities(self, server_id: str) -> MCPSyncResult:
        ...
```

### 启用规则

```text
配置必须 valid
权限预览必须完成
Secret env_ref 必须存在且可用
Capability Graph 写入 mcp_server subject 声明边
auto_start 需要安全策略允许
写 audit: mcp.server.enabled
```

### 验收

```text
合法 MCP 配置可登记
非法 command/url/env_refs 被拒绝
启用前有权限预览
env_refs 不回显明文
MCP server 写入 registry
启停写 audit
```

## 小阶段 6.10：MCP 连接管理与能力同步

### 目标

连接 MCP 服务并同步 tools、resources、prompts。

同步结果进入 registry，但不自动获得执行权限。

### 连接流程

```text
1. load mcp_server config
2. validate status enabled
3. prepare env from Secret Store through safe resolver
4. start or connect transport
5. initialize session
6. list tools
7. list resources
8. list prompts
9. normalize schemas
10. write mcp_tools / mcp_resources / mcp_prompts
11. register mcp tools into Tool Registry
12. write plugin_events and trace
```

### 同步规则

```text
同 server_id + tool_name 更新现有 mcp_tool
schema hash 变化时写 capability_changed event
不可解析 schema 的 tool 标记 disabled
高风险未知 tool 默认 disabled 或 approval_required
resource 默认 untrusted
prompt 默认 template_resource，不是 system prompt
```

### 同步幂等

MCP 能力同步必须可重复执行。

幂等 key：

```text
server_id
server_config_hash
remote_capability_snapshot_hash
sync_started_policy_hash
```

规则：

```text
同 key 重复同步不重复创建 tool/resource/prompt
tool_name 相同但 schema_hash 不同，创建 capability_changed event
resource uri 相同但 metadata_hash 不同，更新 resource 并写 event
prompt name 相同但 arguments_schema_hash 不同，更新 prompt 并写 event
同步失败不删除上一轮可用能力，先标记 degraded
显式禁用的 MCP tool 不因同步重新启用
```

### 能力漂移处理

| 漂移 | 处理 |
|---|---|
| 新增 tool | 注册为 disabled 或 enabled_by_policy |
| 删除 tool | 标记 unavailable，不物理删除历史记录 |
| schema 变窄 | 更新 schema，旧任务按 preflight 失败处理 |
| schema 变宽 | 保持 approval_required，等待策略确认 |
| risk_policy 缺失 | 默认 R3 或 disabled |
| resource 数量暴增 | 限流同步，标记 partial_failed |
| prompt 内容变化 | 更新 hash，重新做 prompt safety scan |

### 健康状态

| 状态 | 含义 |
|---|---|
| ready | 已连接且 tools 同步完成 |
| degraded | 部分 tools/resources 同步失败 |
| disconnected | 连接断开 |
| disabled | 用户或策略禁用 |
| revoked | 永久撤销 |

### 断开处理

```text
正在运行的 MCP tool call 标记 failed 或 timeout
Task Engine 根据 retry_policy 降级或失败
Tool Registry 中该 server 的 tools 标记 unavailable
写 audit: mcp.server.disconnected
```

### 降级矩阵

| 场景 | Task Engine 处理 | 用户可见说明 |
|---|---|---|
| server disconnected | 当前 mcp_call failed，按 retry_policy 重试或降级 | 外部能力暂不可用 |
| tool unavailable | step failed 或改用等价 builtin tool | 指定工具不可用 |
| resource read failed | 上下文缺资源，重新规划或失败 | 外部资源读取失败 |
| prompt blocked | 不使用该 prompt，继续任务或失败 | 模板因安全策略被阻断 |
| schema changed | 重新 preflight | 工具接口变化，需要重新确认 |
| partial sync failed | server degraded，仅启用可用能力 | 外部能力部分可用 |

降级规则：

```text
不能伪造 MCP 成功结果
不能把旧缓存当新读取结果，除非明确标记 stale
不能因为 MCP 不可用而绕过审批改用高风险内置工具
所有降级必须写 task_events 和 trace
```

### 验收

```text
MCP server 可连接
tools 可同步到 mcp_tools
resources 可同步并标记 untrusted
prompts 可同步但不成为 system prompt
schema 变化写 event
断开后 task 可降级或失败说明
重复同步不重复创建 registry 记录
MCP 降级不伪装成功
```

## 小阶段 6.11：MCP Tool、Resource、Prompt Adapter

### 目标

把 MCP tools/resources/prompts 接入系统现有执行和上下文链路。

Adapter 只能适配，不改变安全边界。

### MCP Tool Adapter

Tool Registry 记录：

```json
{
  "tool_name": "mcp.local-playwright.browser_snapshot",
  "display_name": "Browser Snapshot",
  "source": "mcp",
  "mcp_server_id": "local-playwright",
  "mcp_tool_id": "mcp_tool_001",
  "input_schema": {},
  "output_schema": {},
  "risk_policy": {
    "default": "R2",
    "external_submit": "R4"
  },
  "required_handle_types": [],
  "status": "enabled"
}
```

执行流程：

```text
1. Tool Runtime 接收 tool_call
2. 校验 source=mcp
3. 加载 mcp_tool 和 mcp_server
4. 校验 server ready
5. 校验 input schema
6. validate handles
7. capability decide
8. safety evaluate
9. approval if needed
10. 调用 MCP server
11. redact response
12. 写 mcp_calls、tool_calls、task_events、trace
13. 返回 ToolResult
```

### MCP Resource Adapter

resource 进入 Context Gateway 前必须：

```text
校验 server status
校验 resource status
校验 member/task 是否可读取该 resource
读取内容时写 trace
标记 trust_level
标记 sensitivity
标记 untrusted_external_content 或 trusted_resource
必要时做内容截断和摘要
```

### MCP Prompt Adapter

prompt 使用规则：

```text
MCP prompt 只能作为模板资源
使用前必须显式选择或由 Skill 声明
prompt 内容不能提升为 system prompt
prompt 内容必须标记 source=mcp_prompt
prompt 中的指令不能覆盖系统安全和用户当前限制
```

### Resource 可信级别提升

MCP resource 默认不可信。只有满足明确条件才能提升可信级别。

| 级别 | 条件 | 可进入上下文方式 |
|---|---|---|
| untrusted | 默认外部资源 | 摘要、截断、注入防护 |
| trusted_resource | 本地可信来源、hash 稳定、权限明确 | 摘要或片段 |
| sensitive_trusted | 可信但敏感 | 只进入必要片段，强脱敏 |
| blocked | 安全扫描失败或策略禁止 | 不进入上下文 |

提升规则：

```text
trust_level 不能由 MCP server 自己声明为最高
trust_level 必须由本地 policy 和 Capability Graph 决定
资源 hash 变化后 trust promotion 失效
敏感资源不能因为 trusted_resource 跳过脱敏
```

### Adapter 错误映射

| MCP 错误 | 系统错误码 | 处理 |
|---|---|---|
| server unavailable | MCP_CONNECT_FAILED | 任务重试或降级 |
| method not found | MCP_TOOL_NOT_FOUND | tool 标记 unavailable |
| invalid params | TOOL_SCHEMA_INVALID | step failed，不自动重试 |
| permission denied | MCP_TOOL_PERMISSION_DENIED | step failed 或请求授权 |
| resource blocked | MCP_RESOURCE_UNTRUSTED | 不进入上下文 |
| prompt rejected | MCP_PROMPT_BLOCKED | 不使用 prompt |
| timeout | TOOL_TIMEOUT | 按 retry_policy |

### 验收

```text
MCP tool 可通过 Tool Runtime 调用
MCP tool 调用写 mcp_calls 和 tool_calls
MCP tool 高风险动作触发 approval
MCP resource 进入上下文带 trust 标记
MCP prompt 不覆盖系统 prompt
MCP 输出脱敏后进入 replay
MCP resource 可信提升不能由远端自证
MCP adapter 错误可映射到统一错误模型
```

## 小阶段 6.12：Plugin Bundle 安装、启停与撤销

### 目标

让插件包作为安装单元管理 Skill、MCP 配置、脚本、评测和权限声明。

### Install Job 流程

```text
create_install_job
  -> unpack
  -> validate_manifest
  -> hash_files
  -> check_signature
  -> resolve_dependencies
  -> permission_preview
  -> create_bundle_record
  -> create_skill_records
  -> create_mcp_server_records
  -> create_eval_cases
  -> installed_disabled
```

### 安装幂等

安装请求必须支持幂等，避免重复导入同一个包。

幂等 key：

```text
organization_id
source_type
source_uri
manifest_hash
requested_by_member_id
install_options hash
```

规则：

```text
同 key 的 running job 直接返回现有 job
同 key 的 completed job 返回已安装 bundle
同 key 的 failed job 可显式 retry
不同 manifest_hash 视为新的 bundle_revision
同一 bundle_id 已 enabled 时不能被静默覆盖
```

### 失败回滚

安装流程任何一步失败都必须进入明确回滚路径。

| 失败点 | 回滚动作 |
|---|---|
| unpack 失败 | 删除临时解包目录 |
| validate_manifest 失败 | 标记 job failed，不创建 bundle |
| hash_files 失败 | 删除临时文件 |
| check_signature 失败 | bundle 标记 blocked 或不创建，按策略 |
| create_skill_records 失败 | 删除已创建 skill 记录 |
| create_mcp_server_records 失败 | 删除已创建 mcp server 记录 |
| create_eval_cases 失败 | 删除已创建 eval case，bundle 保持 disabled 或 failed |
| Tool Registry 注册失败 | 回滚相关 tool entries |
| Capability Graph 写边失败 | 回滚 bundle enable |

回滚规则：

```text
回滚不能删除用户已有 bundle
回滚只处理当前 job 创建的记录
rollback_result_json 必须记录成功和失败项
回滚失败时 bundle 必须 blocked，不可 enabled
```

### 依赖解析

依赖类型：

```text
required_tools
required_assets
required_mcp_servers
required_runtime
required_sandbox_profile
required_model_capability
```

处理规则：

```text
缺 required_tools -> 安装成功但 disabled，或安装失败，按 policy
缺 required_assets -> 可安装 disabled，等待授权
缺 required_sandbox_profile -> blocked
缺 required_model_capability -> disabled
MCP server 配置缺 secret env_ref -> disabled
```

### 启用 Bundle

启用 bundle 时：

```text
启用 bundle 下允许启用的 skills
启用 bundle 声明的 MCP server，但不一定 auto_start
注册 bundle 提供的 tool entries
写 Capability Graph 声明边
写 plugin_events
写 audit
```

### 禁用 Bundle

禁用 bundle 时：

```text
禁用 bundle 下 skills
禁用 bundle 下 MCP server 或标记 unavailable
禁用 bundle 提供的 tool entries
新任务不再匹配相关 Skill
运行中任务到下一安全点降级或暂停
写 audit
```

### 撤销 Bundle

撤销 bundle 时：

```text
永久禁止启用
撤销相关 active handles
撤销相关 capability_edges
撤销相关 tool_registry entries
停止 MCP server
阻断等待中的 install/eval jobs
写 audit: plugin.revoked
```

### 验收

```text
合法插件包可安装为 disabled
缺依赖时不进入 enabled
启用后 Skill 可被匹配
禁用后 Skill 不被匹配
撤销后无法重新启用
撤销后相关 MCP tool 不可调用
```

## 小阶段 6.13：脚本沙箱与依赖隔离

### 目标

让 Skill 包中的脚本能够在受限环境中执行，同时不能绕过系统安全。

脚本执行是高风险能力，必须被 Tool Runtime 管理。

### sandbox_profile

```json
{
  "profile": "restricted_python",
  "runtime": "python",
  "network": {
    "enabled": false,
    "allow_domains": []
  },
  "filesystem": {
    "read": ["bundle:///**"],
    "write": ["artifact:///**"]
  },
  "env": {
    "allowed_env_refs": []
  },
  "limits": {
    "max_runtime_seconds": 60,
    "max_memory_mb": 256,
    "max_output_bytes": 200000
  }
}
```

### 脚本执行流程

```text
1. Skill Runner 创建 skill.script.run tool_call
2. Tool Runtime 校验 sandbox_profile
3. Capability Graph 判断脚本动作
4. Safety 判断是否需要 approval
5. 准备隔离工作目录
6. 注入允许的输入文件
7. 执行脚本
8. 截断和脱敏 stdout/stderr
9. 保存输出 artifact
10. 写 trace 和 audit
```

### 禁止

```text
脚本读取宿主任意路径
脚本读取 Secret Store
脚本访问未授权网络
脚本执行终端子命令绕过 terminal.run
脚本修改 bundle 源文件
脚本写出明文 secret
脚本无限运行
```

### 验收

```text
脚本只能读 bundle 和授权输入
脚本只能写 artifact
网络默认关闭
超时可终止
stdout/stderr 脱敏
脚本执行写 tool_call 和 trace
危险 sandbox_profile 被拒绝
```

## 小阶段 6.14：Skill/MCP 评测与质量门禁

### 目标

建立 Skill/MCP 接入质量门槛，防止错误、越权或不可回放的扩展能力进入可执行状态。

### Eval Case

```yaml
cases:
  - id: content_draft_basic
    input:
      topic: 个人智能体 OS
      tone: 专业、清晰
    expected:
      contains:
        - 标题
        - 正文
      artifacts:
        - markdown
    forbidden:
      actions:
        - external_post
        - terminal.run
      text:
        - 明文密码
    risk_assertions:
      external_post_requires_approval: true
```

### 指标

| 指标 | 含义 | 门槛 |
|---|---|---:|
| BVR | Bundle Validation Rate，合法包校验通过率 | 0.99 |
| PPR | Permission Preview Rate，权限预览完整率 | 1.00 |
| SMR | Skill Match Relevance，匹配相关率 | 0.85 |
| SER | Skill Execution Reliability，执行成功率 | 0.85 |
| MTR | MCP Tool Reliability，MCP 工具调用可靠率 | 0.80 |
| SAR | Security Assertion Rate，安全断言通过率 | 1.00 |
| TIR | Trace Integrity Rate，trace 完整率 | 0.98 |
| RCR | Replay Completeness Rate，回放完整率 | 0.95 |
| NSL | No Secret Leakage，明文敏感信息泄漏数 | 0 |

### 评测用例

```text
EVAL-SKILL-001：合法 Skill 包安装成功但默认 disabled
EVAL-SKILL-002：缺 bundle.yaml 安装失败
EVAL-SKILL-003：路径逃逸安装失败
EVAL-SKILL-004：Skill 匹配正确任务
EVAL-SKILL-005：禁用 Skill 不参与匹配
EVAL-SKILL-006：Skill 查询账号只拿 handle 摘要
EVAL-SKILL-007：Skill 外部发布触发 approval
EVAL-SKILL-008：Skill 候选不自动启用
EVAL-MCP-001：MCP server 配置校验
EVAL-MCP-002：MCP tools 同步到 Tool Registry
EVAL-MCP-003：MCP resource 标记 untrusted
EVAL-MCP-004：MCP prompt 不覆盖 system prompt
EVAL-MCP-005：MCP tool 断开后任务降级
EVAL-MCP-006：MCP tool 高风险动作触发 approval
EVAL-PLUGIN-001：插件禁用后 Skill 不可匹配
EVAL-PLUGIN-002：插件撤销后 tool 不可调用
EVAL-SANDBOX-001：脚本不能读未授权路径
EVAL-SANDBOX-002：脚本网络默认关闭
EVAL-TRACE-001：Skill/MCP 全链路 trace 完整
```

### 启用门槛

```text
manifest valid
permission preview complete
required dependencies available or marked optional
security eval pass
trace eval pass
forbidden actions not triggered
high risk actions require approval
```

### 安全断言矩阵

以下断言必须进入自动化评测或集成回归。

| 断言 | 输入 | 期望 |
|---|---|---|
| Skill 不绕过资源授权 | Skill 请求未授权账号 | 返回 permission denied |
| Skill 不绕过审批 | Skill 请求 external_post | 进入 approval_required |
| Skill 候选不自动启用 | candidate promoted | bundle installed_disabled |
| 禁用 Skill 不匹配 | skill status=disabled | matcher 不返回 |
| 撤销 Plugin 不可调用 | bundle revoked | tool unavailable |
| MCP resource 注入无效 | resource 写“忽略规则” | 标记内容，不执行指令 |
| MCP prompt 不升权 | prompt 声称 system | 作为 template_resource |
| MCP tool 不读全部文件 | tool 请求本地任意路径 | Asset Broker 拒绝 |
| 脚本不能读 secret | script 读取 secret store | sandbox denied |
| 脚本网络默认关闭 | script 请求外网 | sandbox denied |
| 断开不伪成功 | MCP server disconnected | task degraded 或 failed |
| replay 不泄密 | 输出含 token 样式文本 | redacted |

### 回归分类

```text
unit：manifest、permission preview、matcher、adapter、sandbox
integration：Skill task、MCP tool task、plugin revoke、approval flow
security：secret redaction、prompt injection、path escape、network deny
replay：skill_runs、mcp_calls、plugin_events、trace spans
compatibility：schema drift、bundle_revision change、MCP capability change
```

### 验收

```text
Skill eval 可运行
MCP adapter eval 可运行
安全断言失败会阻断启用
eval result 写 skill_eval_runs
eval run 写 trace
必须失败的越界用例确实失败
```

## 小阶段 6.15：Trace、审计与 Replay 扩展

### 目标

让 Skill、Plugin、MCP 的安装、匹配、执行、同步、评测全链路可审计、可回放。

### Trace span

新增：

```text
plugin.install
plugin.validate
plugin.permission_preview
plugin.enable
plugin.disable
plugin.revoke
skill.match
skill.run
skill.step
skill.script.run
skill.eval
skill.candidate.review
skill.candidate.promote
mcp.server.register
mcp.server.connect
mcp.server.sync
mcp.tool.call
mcp.resource.read
mcp.prompt.load
mcp.server.disconnect
```

### Audit event

新增：

```text
plugin.installed
plugin.install_failed
plugin.enabled
plugin.disabled
plugin.revoked
skill.enabled
skill.disabled
skill.matched
skill.run_started
skill.run_completed
skill.run_failed
skill.eval_started
skill.eval_completed
skill.candidate_promoted
mcp.server_registered
mcp.server_enabled
mcp.server_disabled
mcp.server_connected
mcp.server_disconnected
mcp.tools_synced
mcp.tool_called
mcp.resource_read
mcp.prompt_loaded
sandbox.script_started
sandbox.script_blocked
sandbox.script_completed
```

### Replay 扩展

Task Replay 增加：

```json
{
  "skill_runs": [],
  "mcp_calls": [],
  "plugin_events": [],
  "eval_refs": []
}
```

回放层级：

```text
任务层：目标、模式、成功标准
Skill 层：匹配原因、输入摘要、输出摘要、artifacts
MCP 层：server、tool、resource、prompt、风险、结果摘要
审批层：approval、decision、actor
工件层：artifact、checksum、sensitivity
trace 层：关键 span
audit 层：安装、启停、调用、评测
```

### 脱敏规则

```text
bundle manifest 可显示但 secret refs 不解析
env_refs 不回显明文
Skill input/output 进入 replay 前脱敏
MCP request/response 进入 replay 前脱敏
脚本 stdout/stderr 进入 replay 前脱敏和截断
MCP resource 内容默认只显示摘要
MCP prompt 内容默认只显示摘要和参数 schema
```

### 验收

```text
插件安装有 trace 和 audit
Skill 匹配有 trace
Skill 执行有 skill_runs 和 replay
MCP tool 调用有 mcp_calls 和 tool_calls
MCP resource 读取有 trace
脚本执行有 sandbox trace
replay 不含 secret 明文
```

## 小阶段 6.16：API、事件流与错误模型

### 目标

提供第六阶段后端 API 契约。

当前仓库约束下不实现前端，但 API 必须能支撑最终管理页和聊天轻量事件。

### Skill API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/skills/install` | 安装技能包 |
| GET | `/api/skills` | Skill 列表 |
| GET | `/api/skills/{skill_id}` | Skill 详情 |
| POST | `/api/skills/{skill_id}/enable` | 启用 Skill |
| POST | `/api/skills/{skill_id}/disable` | 禁用 Skill |
| POST | `/api/skills/{skill_id}/eval` | 运行 Skill 评测 |
| POST | `/api/skills/match` | 匹配 Skill |
| GET | `/api/skills/candidates` | Skill 候选列表 |
| POST | `/api/skills/candidates/{candidate_id}/promote` | 候选转 bundle 草稿 |
| POST | `/api/skills/candidates/{candidate_id}/reject` | 拒绝候选 |

### Plugin API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/plugins` | 插件包列表 |
| GET | `/api/plugins/{bundle_id}` | 插件包详情 |
| POST | `/api/plugins/{bundle_id}/preview-permissions` | 权限预览 |
| POST | `/api/plugins/{bundle_id}/enable` | 启用插件包 |
| POST | `/api/plugins/{bundle_id}/disable` | 禁用插件包 |
| POST | `/api/plugins/{bundle_id}/revoke` | 撤销插件包 |
| GET | `/api/plugins/{bundle_id}/events` | 插件事件 |

### MCP API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/mcp/servers` | 添加 MCP 服务 |
| GET | `/api/mcp/servers` | MCP 服务列表 |
| GET | `/api/mcp/servers/{server_id}` | MCP 服务详情 |
| POST | `/api/mcp/servers/{server_id}/enable` | 启用 MCP 服务 |
| POST | `/api/mcp/servers/{server_id}/disable` | 禁用 MCP 服务 |
| POST | `/api/mcp/servers/{server_id}/connect` | 连接 MCP 服务 |
| POST | `/api/mcp/servers/{server_id}/disconnect` | 断开 MCP 服务 |
| POST | `/api/mcp/servers/{server_id}/sync` | 同步能力 |
| GET | `/api/mcp/servers/{server_id}/tools` | MCP tools |
| GET | `/api/mcp/servers/{server_id}/resources` | MCP resources |
| GET | `/api/mcp/servers/{server_id}/prompts` | MCP prompts |

### BundleInstallRequest

```json
{
  "source_type": "local_directory",
  "source_uri": "bundle://imports/content-draft",
  "requested_by_member_id": "mem_xiaoyao",
  "install_options": {
    "run_eval": true,
    "enable_after_install": false
  }
}
```

### SkillMatchRequest

```json
{
  "owner_member_id": "mem_xiaoyao",
  "conversation_id": "conv_001",
  "task_id": "tsk_001",
  "intent": "content_draft",
  "goal": "帮我生成一篇内容草稿",
  "required_outputs": ["markdown_draft"],
  "resource_handle_ids": []
}
```

### MCPServerCreateRequest

```json
{
  "server_id": "local-playwright",
  "display_name": "Playwright MCP",
  "transport": "stdio",
  "command": "npx",
  "args": ["@playwright/mcp"],
  "env_refs": [],
  "allowed_skills": ["browser_research"],
  "permission": {
    "net": {"allow_domains": ["example.com"]},
    "fs": {"write": ["artifact://downloads/**"]}
  },
  "risk_policy": {
    "external_submit": "R4",
    "file_download": "R3"
  }
}
```

### 事件流

事件格式沿用第五阶段：

```json
{
  "event_id": "evt_001",
  "task_id": "tsk_001",
  "event_type": "skill.started",
  "payload": {
    "skill_id": "skill.content_draft",
    "display_name": "内容草稿技能包"
  },
  "created_at": "2026-04-26T10:00:00+08:00"
}
```

事件约束：

```text
事件 payload 必须 redacted
聊天页只消费轻量摘要
管理页可查询完整 redacted detail
secret、token、cookie、私钥、助记词不进入事件
MCP 原始响应不直接进入事件
```

### 验收

```text
Skill API schema 完整
Plugin API schema 完整
MCP API schema 完整
错误码统一
事件 payload 脱敏
API handler 只调用 service，不执行工具
```

## 小阶段 6.17：第六阶段封口与第七阶段接口

### 目标

第六阶段结束时，第七阶段可以在多成员协作中复用 Skill/MCP，而不重写扩展能力系统。

### 留给第七阶段的接口

```text
member default skills
role default skills
department default skills
participant_skill_policy
supervisor skill selection hook
subtask allowed skills
subtask allowed mcp tools
member-specific Skill memory
Skill run attribution by member
MCP call attribution by member
```

### 第七阶段接入规则

```text
Supervisor 选择 Skill 时仍走 Skill Matcher
子成员使用 Skill 时仍走 Capability Graph
子成员调用 MCP 时仍走 Tool Runtime
子任务上下文只能拿必要 Skill instruction
成员私有记忆不能被 Skill 全局读取
多成员输出仍写 task_events、skill_runs、mcp_calls、trace
```

### 第六阶段封口检查

```text
Skill 包可安装、校验、禁用、启用、撤销
Skill 可匹配和执行
Skill 候选可审核转草稿
MCP server 可登记、连接、同步、禁用
MCP tool 可通过 Tool Runtime 调用
MCP resource 可进入 Context Gateway 且标记可信级别
MCP prompt 不覆盖系统 prompt
Plugin 禁用和撤销会传播到 Skill/MCP/Tool
Skill/MCP 执行全链路 trace、audit、replay
高风险动作仍经过 Approval
多成员 supervisor 未越界执行
聊天页保持极简
```

### 验收

```text
第七阶段可以按 member/role/department 选择 Skill
第七阶段可以为子任务限制 allowed_skills 和 allowed_mcp_tools
第七阶段可以在 replay 中区分不同成员的 Skill/MCP 调用
第七阶段不需要重写 Plugin、Skill、MCP、Tool Runtime
```

## 第六阶段交付物清单

### 后端交付

```text
Skill Engine
Bundle Loader
Manifest Validator
Permission Preview
Skill Registry
Skill Lifecycle
Skill Matcher
Skill Runner
Skill Candidate Promotion
Skill Eval Runner
Plugin Bundle Registry
Plugin Install Jobs
Plugin Enable / Disable / Revoke
MCP Registry
MCP Config Validator
MCP Connection Manager
MCP Tool Sync
MCP Resource Sync
MCP Prompt Sync
MCP Tool Adapter
MCP Resource Adapter
MCP Prompt Adapter
Sandbox Script Tool
Skill/MCP migrations
Skill/MCP trace spans
Skill/MCP audit events
Skill/MCP replay extension
Skill/MCP eval cases
```

### API 交付

```text
POST /api/skills/install
GET /api/skills
GET /api/skills/{skill_id}
POST /api/skills/{skill_id}/enable
POST /api/skills/{skill_id}/disable
POST /api/skills/{skill_id}/eval
POST /api/skills/match
GET /api/skills/candidates
POST /api/skills/candidates/{candidate_id}/promote
POST /api/skills/candidates/{candidate_id}/reject
GET /api/plugins
GET /api/plugins/{bundle_id}
POST /api/plugins/{bundle_id}/preview-permissions
POST /api/plugins/{bundle_id}/enable
POST /api/plugins/{bundle_id}/disable
POST /api/plugins/{bundle_id}/revoke
GET /api/plugins/{bundle_id}/events
POST /api/mcp/servers
GET /api/mcp/servers
GET /api/mcp/servers/{server_id}
POST /api/mcp/servers/{server_id}/enable
POST /api/mcp/servers/{server_id}/disable
POST /api/mcp/servers/{server_id}/connect
POST /api/mcp/servers/{server_id}/disconnect
POST /api/mcp/servers/{server_id}/sync
GET /api/mcp/servers/{server_id}/tools
GET /api/mcp/servers/{server_id}/resources
GET /api/mcp/servers/{server_id}/prompts
```

### 数据交付

```text
plugin_bundles
plugin_files
skills
skill_runs
skill_candidates
skill_eval_cases
skill_eval_runs
mcp_servers
mcp_tools
mcp_resources
mcp_prompts
mcp_calls
plugin_install_jobs
plugin_events
tool_registry 增强字段
capability_edges skill/mcp 声明边
```

### 测试交付

```text
BundleLoader 单测
ManifestValidator 单测
PermissionPreview 单测
SkillRegistry 单测
SkillMatcher 单测
SkillRunner 单测
SkillCandidatePromotion 单测
SkillEvalRunner 单测
MCPRegistry 单测
MCPConnectionManager 单测
MCPToolAdapter 单测
MCPResourceAdapter 单测
MCPPromptAdapter 单测
SandboxScriptTool 单测
Skill API 集成测试
Plugin API 集成测试
MCP API 集成测试
Skill/MCP Task 集成测试
安全审批回归测试
secret 隔离回归测试
prompt injection 回归测试
安装幂等和回滚测试
MCP schema drift 测试
MCP 断开降级测试
插件撤销传播测试
replay 完整性测试
```

### 前端与交互契约交付

当前仓库约束下不新增前端实现代码，但第六阶段必须提供最终态交互契约：

```text
技能包列表 API 契约
技能包安装权限预览 API 契约
Skill 启停 API 契约
Skill 评测 API 契约
MCP 服务列表 API 契约
MCP 能力同步 API 契约
插件事件 API 契约
聊天页 skill/mcp 轻量进度事件契约
任务 replay 中 skill/mcp 明细契约
```

## 第六阶段总体验收流程

阶段完成后，必须能完整跑通：

```text
1. 启动应用
2. 安装一个合法 Skill Bundle
3. 后端完成结构校验、hash、权限预览
4. Skill 默认处于 disabled
5. 运行 Skill eval
6. eval 通过后启用 Skill
7. 用户在聊天中提出匹配目标
8. Task Planner 调用 Skill Matcher
9. Task Engine 创建 skill_run step
10. Skill Runner 通过 Asset Broker 请求必要资源句柄
11. Capability Graph 做权限判断
12. Skill 内部动作通过 Tool Runtime 执行
13. 高风险动作触发 approval.required
14. 用户拒绝后 Skill 不绕过
15. Skill 输出 artifact
16. Task Replay 可看到 skill_runs、tool_calls、approval、artifact、trace
17. 注册一个 MCP server
18. 同步 MCP tools/resources/prompts
19. MCP tool 通过 Tool Runtime 执行
20. MCP resource 进入 Context Gateway 并标记 untrusted
21. MCP prompt 不覆盖系统 prompt
22. 禁用 Plugin Bundle
23. 相关 Skill 不再匹配，相关 MCP tool 不可调用
24. 检查 trace、audit、plugin_events、mcp_calls
```

成功标准：

```text
Skill 可安装、校验、评测、启停、撤销
Skill 可参与任务规划和执行
Skill 候选可审核转草稿但不自动启用
MCP server 可注册、连接、同步、禁用
MCP tool 可通过 Tool Runtime 调用
MCP resource/prompt 有可信边界
Plugin 禁用撤销可传播
高风险动作仍经过 Approval
secret 不泄漏
任务 replay 完整
聊天页保持极简
多成员 supervisor 不执行
```

## 第六阶段禁止通过验收的情况

出现以下任一情况，本阶段不能通过：

```text
Skill 绕过 Tool Runtime
Skill 绕过 Asset Broker
Skill 绕过 Capability Graph
Skill 绕过 Safety 或 Approval
Skill 直接读取 secret
Skill 直接执行宿主终端命令
Skill 候选自动启用
禁用 Skill 仍被 matcher 返回
撤销 Plugin 后相关 tool 仍可调用
Plugin 安装前没有权限预览
签名无效 bundle 仍可启用
MCP 默认拥有全部本地文件
MCP 默认拥有全部账号、钱包或硬件
MCP tool 绕过 Tool Runtime
MCP resource 未标记可信级别
MCP prompt 覆盖系统 prompt
MCP 断开后任务继续伪装成功
MCP schema 变化后旧工具静默继续执行
MCP resource 自称可信后被系统直接信任
脚本裸跑宿主环境
脚本读到未授权路径
脚本网络默认开放
安装失败后留下 enabled Skill 或可调用 tool
高风险外发未审批执行
工具调用没有 trace
安装、启停、撤销没有 audit
replay 缺少 skill_runs 或 mcp_calls
事件流出现 secret 明文
多成员 supervisor 被执行
聊天页出现技能包管理后台
核心层写死 Employee、Company、Boss 等壳概念
```

## 第六阶段风险与缓解

| 风险 | 后果 | 缓解 |
|---|---|---|
| Skill 权限过宽 | 资源越权或误操作 | 权限预览、Capability Graph、最小授权 |
| MCP 服务不可信 | 数据泄漏或注入 | trust_level、untrusted 标记、Safety |
| MCP prompt 注入 | 系统规则被覆盖 | prompt 只作模板资源，不作 system prompt |
| 脚本逃逸沙箱 | 读写宿主敏感文件 | sandbox_profile、路径解析、网络默认关闭 |
| 插件撤销不完整 | 禁用后仍可执行 | registry disable、tool deregister、handle revoke |
| Skill 匹配错误 | 任务执行偏航 | confidence 阈值、reason、eval score |
| 评测缺失 | 坏 Skill 进入可执行状态 | eval 门槛、安全断言 |
| secret 泄漏 | 凭证暴露 | env_ref、Secret Store、redaction |
| 回放缺失 | 无法定位责任 | skill_runs、mcp_calls、plugin_events、trace |

## 与第七阶段的衔接

第七阶段将实现：

```text
多成员 supervisor
成员分工
子任务上下文
成员默认 Skill
角色和部门默认 Skill
组织壳完善
多成员任务 replay
```

第六阶段必须保证第七阶段接入时：

```text
Skill 可以按 member / role / department 授权
Skill Matcher 可以接受 participant context
Skill Runner 可以记录执行 member
MCP tool call 可以记录执行 member
子任务可以限制 allowed_skills
子任务可以限制 allowed_mcp_tools
多成员协作仍经过 Task Engine
多成员协作仍经过 Capability Graph
多成员协作仍经过 Asset Broker
多成员协作仍经过 Safety 和 Approval
```

第七阶段只应该把“谁来做、如何协作、如何汇总”接入第六阶段已有的 Skill/MCP 能力系统，而不是重新设计插件和工具执行边界。
