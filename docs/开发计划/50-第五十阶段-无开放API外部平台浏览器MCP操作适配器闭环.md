# 第五十阶段 - 无开放 API 外部平台浏览器 MCP 操作适配器闭环

## 阶段背景

第四十二阶段完成了通用外部平台动作编排、账号资产候选、审批和 fake provider E2E。第四十七阶段继续把浏览器执行器、浏览器 session 资产、provider registry 和外部平台 execution mode 做成可替换底座。但当前能力仍停在两个边界：

```text
fake provider 可以模拟发布成功
browser provider 能明确失败为 browser_provider_not_configured
```

真实用户期望更进一步：当目标平台没有开放 API，系统仍能在受控浏览器、工具或 MCP 的帮助下，像用户一样打开网页、复用登录态、填写表单、提交发布，并在每个高风险点停下来确认。这类能力类似 OpenClaw / Hermes Agent 的浏览器自动化路径，但本项目还必须额外接入公司/组织账号资产、Capability Graph、Safety、Approval、Trace 和审计。

本阶段目标是把“无开放 API 外部平台操作”从底座推进到可交付闭环：通用编排层不写死平台，具体平台行为由受控 adapter manifest / Skill / MCP provider 描述；账号和登录态仍从 Asset Broker 获取；提交发布前必须审批；验证码、二次验证、风控和条款限制必须 fail closed 或转人工，不做绕过。

本阶段仍只做后端、schema、migration、repository、service、API、tests、evals 和文档。不新增产品前端页面、组件、样式、Tauri 窗口或桌面端交互代码。测试优先使用本地模拟社交平台和 mock MCP，不默认接入真实生产平台。

## 核心目标

本阶段完成后，后端应支持：

```text
为无开放 API 平台注册 browser/MCP adapter manifest
adapter 将通用 action plan 翻译为受控浏览器步骤
通过 Asset Broker 获取 account/browser_session handle，不暴露 cookie、密码或 token
执行 login_state_check、navigate_publish_page、fill_content、pre_submit_review、submit_publish、verify_result
提交发布、上传、外发消息等外部状态变更必须 Approval
验证码、二次验证、风控、人机校验、页面结构不匹配时停止并给出可恢复状态
执行过程保存 snapshot、screenshot、URL、network summary、DOM step evidence 和 task replay
MCP provider 可作为执行器接入，但必须经过同样的权限、安全、审批和 trace
本地模拟社交平台端到端通过：自然语言 -> 平台解析 -> 账号选择 -> 登录态 -> 填写 -> 审批 -> 发布 -> 证据
```

## 阶段原则

1. 核心编排不写平台 if/else，平台差异放在 adapter manifest / provider 插件 / Skill/MCP 能力声明中。
2. 无开放 API 不等于绕过安全，浏览器模拟操作仍是外部状态变更。
3. 账号、cookie、session、token、密码只通过 Asset Broker 和受控 handle 使用，不进模型上下文。
4. 发布、提交、上传、发送、改资料等动作默认需要审批。
5. 用户确认释放的是唯一绑定的 pending action，不能靠一句“好的”释放多个动作。
6. CAPTCHA、二次验证、风控、封禁提示、登录异常必须停下，不能绕过。
7. 网页内容、DOM、MCP 输出全部视为不可信外部输入。
8. Adapter 只能声明稳定动作和选择器策略，不能存放明文 secret。
9. 页面结构变化时要有 drift detection 和 degraded 状态，不伪装成功。
10. 不新增 UI；后端返回未来聊天、CLI、外部通知可消费的状态、澄清问题和证据摘要。

## 目标用户流程

用户输入：

```text
帮我在某个已配置的社交平台发一篇文章，内容是：今天完成了后端验收。
```

目标后端链路：

```text
1. Chat / external platform resolver 解析 platform_key、action_type、content
2. PlatformTargetRegistry 找到平台 target 和 adapter manifest
3. Asset Broker 查询可用 account/browser_session 候选
4. 多账号时生成澄清问题，单账号进入计划
5. Action Orchestrator 创建 Browser/MCP execution plan
6. Safety 评估内容、动作、平台、账号风险
7. Browser/MCP adapter 检查登录态和发布页可达性
8. 填写标题、正文、标签、媒体等草稿字段
9. 提交发布前生成 pending approval，展示账号、平台、内容摘要和证据
10. 用户确认后点击提交或调用 MCP submit
11. 验证成功页、URL、post id、页面文本或截图
12. 写入 trace、audit、task replay、artifact 和 final response
```

## 当前基线判断

| 能力 | 当前状态 | 第五十阶段目标 |
|---|---|---|
| 外部平台编排 | 支持 resolver、账号候选、approval、fake provider | 接入 browser/MCP adapter 真实步骤 |
| 浏览器工具 | 支持 open/snapshot/fill/click/submit/screenshot/download | 组合成平台级操作流和证据模型 |
| 浏览器 session | 已有资产化、撤销、域名策略方向 | 作为平台登录态 handle 接入 adapter |
| MCP 工具 | 有工具执行入口和治理方向 | 支持 MCP provider adapter，但不绕过权限 |
| 真实平台 | 当前 fail closed | 有平台 adapter 时可执行，无 adapter 明确失败 |
| 测试 | fake provider / 本地测试站点 | 完整本地模拟社交平台 browser/MCP E2E |

## 阶段范围

### 本阶段必须完成

```text
BrowserPlatformAdapter manifest schema
MCPPlatformAdapter manifest schema
AdapterRegistryService
AdapterStep DSL 和 selector strategy
ExternalPlatformActionPlan -> Browser/MCP step compiler
登录态检查、发布页导航、草稿填写、提交前证据、提交后验证
adapter drift detection
captcha/2fa/risk challenge detection
MCP provider 权限、输入输出、trace 和 redaction 契约
本地模拟社交平台 browser adapter
本地模拟社交平台 MCP adapter 或 mock MCP provider
聊天入口到 external platform plan 的端到端测试
release gate / eval / diagnostic 接入
```

### 本阶段不做

```text
不绕过验证码、二次验证、风控、付费墙或网站条款
不默认接入真实生产社交平台
不把某个真实平台选择器写进核心 chat/task service
不保存明文 cookie、password、token 或私钥
不允许模型直接操作浏览器工具绕过 adapter
不新增产品前端页面或桌面交互
不把提交失败、页面漂移或人工验证说成发布成功
```

## 核心契约草案

### BrowserPlatformAdapterManifest

```json
{
  "adapter_id": "bpa_social_fixture",
  "platform_key": "social_fixture",
  "adapter_type": "browser",
  "version": "1.0.0",
  "status": "active",
  "allowed_domains": ["127.0.0.1", "localhost"],
  "supported_actions": ["publish_content"],
  "required_asset_types": ["account", "browser_session"],
  "login_state": {
    "check_url": "/me",
    "success_text": "Logged in",
    "failure_text": "Login required"
  },
  "publish_flow": {
    "url": "/publish",
    "steps": [
      {"name": "fill_title", "tool": "browser.fill", "selector": "[name='title']", "value_from": "content.title"},
      {"name": "fill_body", "tool": "browser.fill", "selector": "[name='body']", "value_from": "content.body"},
      {"name": "pre_submit_snapshot", "tool": "browser.snapshot"},
      {"name": "submit_publish", "tool": "browser.submit", "selector": "form#publish-form", "requires_approval": true}
    ],
    "success": {
      "any_text": ["Published", "post_id"],
      "url_contains": ["/posts/"]
    }
  },
  "challenge_detection": {
    "any_text": ["验证码", "二次验证", "人机验证", "risk check", "captcha"],
    "action": "stop_for_human"
  }
}
```

### MCPPlatformAdapterManifest

```json
{
  "adapter_id": "mpa_social_fixture",
  "platform_key": "social_fixture",
  "adapter_type": "mcp",
  "mcp_server_id": "mcp_social_fixture",
  "tool_map": {
    "check_login": "social.check_login",
    "prepare_publish": "social.prepare_publish",
    "submit_publish": "social.submit_publish",
    "verify_publish": "social.verify_publish"
  },
  "capabilities": ["publish_content"],
  "risk_overrides": {
    "submit_publish": "R4"
  },
  "secret_material_visible": false
}
```

### AdapterExecutionStep

```json
{
  "step_id": "eps_001",
  "plan_id": "epap_001",
  "adapter_id": "bpa_social_fixture",
  "step_name": "submit_publish",
  "executor": "browser",
  "tool_name": "browser.submit",
  "risk_level": "R4",
  "requires_approval": true,
  "status": "awaiting_approval",
  "input_redacted": {
    "url": "https://platform.example/publish",
    "selector": "form#publish-form"
  },
  "evidence": {
    "snapshot_ref": "artifact_001",
    "secret_material_visible": false
  }
}
```

## API 契约建议

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/external-platform/adapters` | 注册 browser/MCP adapter manifest |
| GET | `/api/external-platform/adapters` | 查询 adapter 列表 |
| GET | `/api/external-platform/adapters/{id}` | 查询 adapter 详情 |
| POST | `/api/external-platform/adapters/{id}/validate` | 验证 manifest、域名、工具权限和敏感字段 |
| POST | `/api/external-platform/action-plans/{id}/compile` | 将通用计划编译为 adapter execution steps |
| POST | `/api/external-platform/action-plans/{id}/execute-adapter` | 执行 adapter steps |
| POST | `/api/external-platform/action-plans/{id}/resume-after-human` | 人工处理登录/验证码后继续 |

这些 API 只提供后端契约，未来可由聊天、CLI、通知或 UI 调用。

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 50.1 | Adapter schema、migration 与 repository | manifest、step、drift、challenge、evidence |
| 50.2 | AdapterRegistryService | 注册、校验、版本、状态、目标平台绑定 |
| 50.3 | Browser step compiler | action plan 编译为 browser tool steps |
| 50.4 | MCP step compiler | action plan 编译为 MCP tool steps |
| 50.5 | 登录态与人工接管边界 | session handle、登录状态、captcha/2FA/risk challenge |
| 50.6 | 发布前审批与草稿证据 | pre-submit snapshot、内容摘要、唯一 pending action |
| 50.7 | 提交执行与结果验证 | submit、success detection、failure recovery、drift detection |
| 50.8 | 本地模拟社交平台 E2E | browser adapter 与 mock MCP provider 全链路 |
| 50.9 | 聊天主链路接入与质量回归 | 自然语言发文、反问、审批、证据回复 |
| 50.10 | release gate 与封版证据 | eval、diagnostic、trace redaction、accepted risk |

## 小阶段 50.1：Adapter schema、migration 与 repository

### 目标

建立 adapter manifest 和执行步骤的持久化模型，使平台自动化不再散落在测试或工具参数里。

### 实现要求

```text
新增 external_platform_adapters
新增 external_platform_adapter_versions
新增 external_platform_adapter_steps
新增 external_platform_adapter_executions
新增 external_platform_adapter_drift_events
manifest_json 必须脱敏校验，不允许 secret/token/cookie/password/private_key
adapter 绑定 platform_key、adapter_type、supported_actions、status 和 version
repository 返回明确 schema，不返回长期裸 dict
```

### 验收

```text
可注册 browser adapter manifest
可注册 MCP adapter manifest
重复注册同 platform_key/action/version 时行为明确
manifest 中出现疑似 secret 时拒绝
migration 可重复初始化测试库
```

## 小阶段 50.2：AdapterRegistryService

### 目标

为外部平台执行器提供统一 adapter 查询和校验入口。

### 实现要求

```text
按 platform_key + action_type + adapter_type 查询 adapter
校验 allowed_domains、tool permissions、required_asset_types、risk_defaults
adapter status 支持 active、disabled、degraded、test_only
禁用或降级 adapter 时，计划不得继续执行提交步骤
fake/test adapter 与 production adapter 明确区分
```

### 验收

```text
没有 adapter 时返回 adapter_not_configured
adapter disabled 时 fail closed
test_only adapter 不进入 production execution mode
registry diagnostic 不泄漏 manifest 中的敏感字段
```

## 小阶段 50.3：Browser step compiler

### 目标

把通用 `publish_content` 计划编译成可审计的浏览器步骤，而不是让模型临时决定点哪里。

### 实现要求

```text
编译 login_state_check、navigate_publish_page、fill_title、fill_body、fill_tags、pre_submit_snapshot、submit_publish、verify_result
每个步骤声明 tool_name、selector、url、value_source、risk_level、requires_approval
value_source 只允许从 action plan redacted content 或 artifact 引用取值
选择器缺失或 manifest 不完整时不执行
浏览器 URL 必须通过 BrowserSessionService URL policy
```

### 验收

```text
本地模拟平台 manifest 可编译出完整发布步骤
submit_publish 步骤 risk >= R4 且 requires_approval=true
未审批时不会调用 browser.submit
步骤输入不包含明文 secret 或 cookie
```

## 小阶段 50.4：MCP step compiler

### 目标

允许平台操作由 MCP server 提供，但 MCP 只能作为受控执行器，不能绕过 Asset Broker、Safety 和 Approval。

### 实现要求

```text
MCP adapter manifest 声明 server_id、tool_map、capabilities、risk_overrides
执行前校验 MCP server 是否注册、启用、拥有对应 capability
MCP 输入只包含 asset handle id、redacted content、plan id、step id
MCP 输出必须经过 schema validation 和 redaction
MCP submit 类工具必须绑定 approval_id
```

### 验收

```text
mock MCP provider 可完成 prepare_publish 和 submit_publish
未授权 MCP server 不能执行
MCP 输出含 secret 时被 redaction 或阻断
MCP submit 未审批时不调用
```

## 小阶段 50.5：登录态与人工接管边界

### 目标

让系统能判断“可以继续自动化”还是“需要用户登录/二次验证/人工处理”。

### 实现要求

```text
登录态检查使用 browser_session handle 或 account handle
未登录时返回 awaiting_human_login 或 awaiting_account_reauth
验证码、二次验证、风控、人机验证、封禁提示进入 challenge_detected
challenge 不进入模型求解，不自动绕过
人工完成后通过 resume-after-human 重新检查状态
```

### 验收

```text
未登录不会假装已登录
检测到 captcha/2FA/risk 文本时停止
人工恢复后可继续执行后续低风险步骤
登录态证据不包含 cookie 明文
```

## 小阶段 50.6：发布前审批与草稿证据

### 目标

在真正点击提交前，让用户看到明确、可理解、可审计的发布摘要。

### 实现要求

```text
pre_submit_review 记录平台、账号显示名、内容摘要、目标 URL、草稿 snapshot
approval payload 绑定 plan_id、adapter_id、step_id、asset_id、content_hash
多个 pending submit 时必须追问
内容变化、账号变化、目标 URL 变化、adapter version 变化会使旧审批失效
```

### 验收

```text
提交前返回 awaiting_approval
用户拒绝后 plan cancelled，browser.submit/MCP submit 不执行
用户确认只释放唯一 submit step
审批 payload 不包含明文 secret/cookie/token
```

## 小阶段 50.7：提交执行与结果验证

### 目标

完成提交后必须验证结果，不只因为点击了按钮就说发布成功。

### 实现要求

```text
submit 后检查 success_text、url_contains、post_id selector、toast、HTTP status、snapshot
成功时记录 published URL、post id 或证据 hash
失败时记录 failed_step、failure_reason、recoverable、next_action
页面结构不匹配进入 adapter_drift_detected
重复提交风险需要 idempotency key 或 duplicate detection
```

### 验收

```text
模拟平台发布成功时状态 completed
成功证据包含 URL、post_id 或页面文本 hash
按钮点击但无成功证据时不得 completed
页面 selector 漂移时状态 failed/degraded 且原因明确
```

## 小阶段 50.8：本地模拟社交平台 E2E

### 目标

用本地模拟社交平台证明无开放 API 场景完整可用，而不是只测单个浏览器工具。

### 场景要求

```text
登录态已存在：直接进入发布页，填草稿，审批后发布
未登录：返回 awaiting_human_login，不发布
多账号：反问选择账号
账号无权限：permission denied
发布前拒绝：不提交
发布后成功：记录 post_id、URL、snapshot、screenshot
页面漂移：adapter_drift_detected
验证码/二次验证：challenge_detected
MCP mock：prepare 成功、submit 审批后成功
敏感内容：阻断或要求清理后重试
```

### 必跑测试

```powershell
.\.venv\Scripts\python.exe -m pytest apps/local-api/tests/test_phase50_browser_mcp_platform_adapters.py -q
.\.venv\Scripts\python.exe -m pytest apps/local-api/tests/test_phase42_external_platform_actions.py apps/local-api/tests/test_phase47_browser_provider_execution.py -q
```

## 小阶段 50.9：聊天主链路接入与质量回归

### 目标

让用户从自然语言聊天里触发这个能力，并得到自然、诚实、可继续的回复。

### 实现要求

```text
用户没说平台时反问平台
用户说平台但无 adapter 时说明尚未配置具体平台适配器
用户说平台但无账号时说明需要添加账号资产
多个账号时反问选择哪个账号
等待审批时说明还没有发布
发布成功时给出证据摘要
失败时说明失败步骤和下一步，不甩内部错误码
```

### 推荐回复形态

```text
我找到了这个平台和可用账号，但发布需要先确认。

准备发布：
- 平台：已配置社交平台
- 账号：品牌账号
- 内容摘要：今天完成了后端验收

我已经填好草稿，还没有点击发布。你确认后我再提交。
```

### 验收

```text
聊天回复不暴露 tool_call_id、approval_id、selector、trace_id 等内部细节
但 task replay 和 diagnostic 可查看完整证据
用户确认后能继续同一个 pending action
用户取消后不会继续旧动作
```

## 小阶段 50.10：release gate 与封版证据

### 目标

把本阶段纳入 release gate，防止后续退化成“能点但不能证明”或“失败还说成功”。

### 实现要求

```text
新增 suite_phase50_browser_mcp_platform_adapters
release report 增加 phase50 摘要
diagnostic bundle 包含 adapter registry、执行状态、drift/challenge 统计
evidence 保存脱敏 snapshot/screenshot/artifact 引用
敏感信息扫描覆盖 manifest、trace、audit、artifact metadata、MCP output
```

### 验收

```text
phase50 eval passed
release gate 可发现 adapter_not_configured、challenge_detected、drift_detected
secret/token/cookie/password/private_key 不出现在报告和诊断包
```

## 文件影响范围

| 模块 | 文件范围 |
|---|---|
| Schema | `apps/local-api/app/schemas/external_platform_adapters.py`、`packages/core-types/core_types/external_platform_adapter.py` |
| Migration | `apps/local-api/app/db/migrations/032_external_platform_adapters.sql` |
| Repository | `apps/local-api/app/db/repositories/external_platform_adapter_repo.py` |
| Services | `external_platform_actions.py`、`external_platform_providers.py`、新增 `external_platform_adapters.py`、`browser_executor.py`、`tools.py` |
| API | `routes_external_platform.py` 或新增 `routes_external_platform_adapters.py` |
| MCP | `mcp.py`、`skill_plugin.py`、provider registry |
| Safety | `approvals.py`、`design_alignment.py`、`chat_safety.py`、trace redaction |
| Tests | `apps/local-api/tests/test_phase50_browser_mcp_platform_adapters.py` |
| Evals | `suite_phase50_browser_mcp_platform_adapters` |

## 验收标准

```text
无开放 API 平台可通过 browser adapter 完成本地模拟发文 E2E
无开放 API 平台可通过 mock MCP adapter 完成本地模拟发文 E2E
真实平台没有 adapter 时明确 adapter_not_configured，不伪装成功
用户没说平台时必须澄清
多个账号时必须澄清
未登录、验证码、二次验证、风控时必须停下等待人工处理
提交发布前必须 approval
发布成功必须有 URL/post_id/snapshot/screenshot 等证据之一
页面漂移不能发布成功，必须记录 drift
所有账号和登录态均通过 Asset Broker handle 使用
trace、audit、replay、diagnostic 不泄漏 cookie、password、token、private key
不新增任何产品前端 UI 或桌面端交互代码
```

## 与其他阶段关系

```text
第四十二阶段提供外部平台动作编排和账号资产链路，本阶段补齐无 API 平台的真实执行适配器
第四十七阶段提供浏览器执行器和 provider registry，本阶段把它们组合成平台级 adapter
第四十八阶段的 Skill/MCP 权限治理用于约束 MCP adapter
第四十九阶段的真实模型质量回归需要加入本阶段聊天 E2E
后续接入真实生产平台时，应新增 adapter manifest/provider 插件和合规说明，不修改核心编排
```

## 最终验收定义

第五十阶段完成时，应能回答：

```text
没有开放 API 的平台，是否能通过受控浏览器或 MCP adapter 完成发文
系统是否知道用哪个账号，且账号来自 Asset Broker
系统是否能判断未登录、验证码、二次验证、风控和页面漂移
用户确认前是否绝不提交
提交后是否有真实证据，而不是只说点击成功
没有 adapter 的真实平台是否明确失败，而不是假装支持
平台差异是否在 adapter/Skill/MCP 层，不污染核心 chat/task service
```
