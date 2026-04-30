# 第三十八阶段 - Skill 插件安全治理与能力市场后端

## 阶段背景

当前仓库已经具备 Skill、Plugin、MCP registry、bundle parser、安装任务、启停、eval 和工具同步的基础能力。但随着系统对齐 OpenClaw / Hermes Agent 的使用场景，用户会期待：

```text
安装别人写好的技能包
把常用流程沉淀为 Skill
让 Skill 调浏览器、文件、MCP、账号、知识库
后台任务自动使用合适 Skill
```

这带来的核心风险是：Skill 既是能力入口，也是供应链入口。若没有安装前审查、权限声明、签名/来源、沙箱策略、版本兼容、回滚和 eval，Skill 可能成为绕过 Asset Broker、Capability Graph、Safety 和 DLP 的捷径。

第三十八阶段目标是把 Skill/Plugin 从“可安装可执行”升级为“可治理、可审计、可撤销、可评测”的能力市场后端。当前阶段仍只做后端，不新增 UI、Tauri、React、样式或桌面端交互。

## 核心目标

本阶段完成后，后端应支持：

```text
Skill bundle manifest 权限声明和风险预览
安装前静态检查和来源记录
本地 curated registry / marketplace metadata
bundle 签名或 checksum 校验
Skill 权限 grant 与 Capability Graph 集成
Skill 执行前 capability/safety/asset/tool 预检
Skill 版本升级、禁用、回滚和撤销
Skill eval 必须绑定版本和能力范围
Skill/MCP 输出 DLP 和 untrusted 标记
release gate 能发现高风险 Skill 未评测、未授权或越权调用
```

## 阶段原则

1. Skill 负责“怎么做”，不负责绕过系统资源查询。
2. Skill 不得直接访问 secret、cookie、token、私钥或任意本地路径。
3. Skill 所需资源必须声明为 capability requirement。
4. Skill 安装不等于授权执行；安装、启用、授权、运行是四个不同阶段。
5. Skill 运行必须通过 Tool Runtime、Asset Broker、Capability Graph、Safety 和 Trace。
6. MCP server 不是可信边界；MCP 工具仍要逐个注册和定级。
7. 未评测或来源不明的 Skill 默认 restricted，不进入后台无人值守任务。
8. 升级 Skill 必须保留旧版本、manifest hash、eval 结果和回滚路径。
9. Skill 输出默认不可信，不能覆盖系统指令。
10. 不做公开云市场，只做本地市场后端和可扩展 metadata 契约。

## 对标结论

### OpenClaw 采用点

```text
Skills 是能力生态核心，用户需要安装、启用、组合和复用
工具、MCP、记忆、浏览器、文件能力会被 Skill 编排
能力市场必须表达权限和依赖
```

对我们的启发：

```text
Skill manifest 必须写清需要哪些工具、资产、MCP、网络和文件范围
安装前给出风险预览
运行时仍按最小权限发放 handle
```

### Hermes Agent 采用点

```text
危险命令和工具调用不能因为来自 Skill 就降低风险
执行环境、审批、超时、拒绝、hard block 必须同样生效
```

对我们的启发：

```text
Skill 调用需要复用统一 ActionRiskDecision
Skill 的自动化步骤不能绕过 pending approval / binding / replay
```

## 当前基线判断

| 能力 | 当前状态 | 第三十八阶段目标 |
|---|---|---|
| Skill install | 已有插件安装任务和 manifest parser | 增加来源、签名/checksum、权限预览、风险定级 |
| Skill run | 可运行 Skill 并写记录 | 执行前强制 capability/safety/asset/tool 预检 |
| MCP registry | 可注册/sync/call stdio MCP | Skill 依赖 MCP 时要验证 server/tool scope |
| Eval | 有 suite 和 release gate | Skill eval 绑定版本、权限和风险 |
| Safety/DLP | 工具层已有 | Skill 输入/输出/步骤级 DLP 和 untrusted 标记 |

## 阶段范围

### 本阶段必须完成

```text
SkillGovernance schema 与 migration
bundle manifest v2 权限声明
安装前 static analyzer
bundle source、checksum、signature_status、trust_level
Skill permission preview API
Skill grant / deny / revoke API
Skill version upgrade / rollback
Skill execution sandbox policy
Skill eval binding
Skill capability graph integration
Skill output DLP 和 taint record
release report phase38
```

### 本阶段不做

```text
不做公开在线市场或支付系统
不做前端技能商店页面
不自动从未知 URL 安装并启用 Skill
不允许 Skill 声明“需要全部权限”
不允许 Skill 直接读取本地任意路径
不允许 Skill 持久保存 secret 明文
不把未通过 eval 的 Skill 纳入默认后台任务
```

## Manifest v2 草案

```yaml
bundle_id: browser-research
bundle_revision: 2.0.0
display_name: 浏览器调研
description: 使用公开网页搜索、快照和知识总结生成调研报告。
author: local
source:
  type: local_directory
  uri: bundles/browser-research
permissions:
  tools:
    - name: browser.search
      actions: [read_public_web]
      risk: R2
    - name: browser.snapshot
      actions: [read_public_web]
      risk: R2
    - name: file.write
      actions: [write_task_artifact]
      risk: R2
  assets:
    - asset_type: knowledge_base
      actions: [read_knowledge]
      optional: true
  mcp:
    - server_capability: web_search
      optional: true
network:
  allowed_domains: ["*"]
  blocked_domains: ["169.254.169.254", "metadata.google.internal"]
filesystem:
  allowed_roots: ["workspace://artifacts/**"]
  denied_roots: ["~/.ssh/**", "**/.env"]
safety:
  unattended_allowed: false
  approval_required_actions: [external_post, file_overwrite]
eval:
  required_suites:
    - suite_browser_research_smoke
```

## 核心契约草案

### SkillRiskPreview

```json
{
  "bundle_id": "browser-research",
  "bundle_revision": "2.0.0",
  "manifest_hash": "sha256:...",
  "trust_level": "restricted",
  "permission_summary": {
    "tools": ["browser.search", "browser.snapshot", "file.write"],
    "assets": ["knowledge_base:read_knowledge"],
    "network": "public_web",
    "filesystem": "task_artifacts_only"
  },
  "risk_level": "R2",
  "blocked_reasons": [],
  "requires_user_grant": true,
  "unattended_allowed": false
}
```

### SkillGrant

```json
{
  "skill_grant_id": "skgrant_001",
  "skill_id": "skill_browser_research",
  "subject_type": "member",
  "subject_id": "mem_xiaoyao",
  "allowed_tools": ["browser.search", "browser.snapshot", "file.write"],
  "allowed_asset_actions": ["knowledge_base:read_knowledge"],
  "denied_actions": ["external_post", "terminal.run"],
  "approval_policy": {
    "external_post": "always_ask",
    "file_overwrite": "always_ask"
  },
  "status": "active"
}
```

## 建议数据表

```text
skill_bundle_sources
skill_bundle_versions
skill_permission_previews
skill_grants
skill_static_analysis_reports
skill_eval_bindings
skill_rollback_points
skill_output_taint_records
```

## API 契约建议

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/skills/preview-install` | 安装前风险和权限预览 |
| POST | `/api/skills/install` | 安装但默认不授予高风险权限 |
| POST | `/api/skills/{id}/grants` | 创建 Skill 授权 |
| GET | `/api/skills/{id}/grants` | 授权列表 |
| POST | `/api/skills/{id}/revoke` | 撤销 Skill |
| POST | `/api/skills/{id}/upgrade` | 升级版本 |
| POST | `/api/skills/{id}/rollback` | 回滚版本 |
| GET | `/api/skills/{id}/analysis` | 静态分析报告 |
| GET | `/api/skills/{id}/eval-bindings` | 版本评测绑定 |

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 38.1 | manifest v2 与 schema | 权限、网络、文件、资产、MCP、eval 声明 |
| 38.2 | migration 与 repository | source/version/grant/analysis/eval/rollback |
| 38.3 | static analyzer | manifest、路径、命令、MCP、secret、风险扫描 |
| 38.4 | permission preview | 安装前风险、依赖、blocked reason |
| 38.5 | Skill grants | 成员/角色/部门授权与 Capability Graph 集成 |
| 38.6 | runtime enforcement | Skill run 前预检，执行中 binding，输出 DLP |
| 38.7 | upgrade/rollback | 版本兼容、回滚点、禁用旧版本 |
| 38.8 | eval/release gate | Skill 版本评测、未评测高风险阻断 |

## 静态分析要求

```text
检查 manifest 是否声明全部工具和资产依赖
拒绝通配高风险工具，例如 terminal.run:*、file:**、asset.secret:*
扫描脚本和 Skill 文档中的敏感路径、curl | sh、rm -rf、私钥、硬编码 token
检查 MCP 依赖是否存在、是否 ready、是否 restricted
检查网络域名 allow/deny 策略是否过宽
检查 unattended_allowed 与风险等级是否冲突
生成 stable reason_codes 和 remediation hints
```

## 运行时 enforcement

```text
Skill run 创建前读取 active grant
按 grant 限制可用 tools/assets/mcp
每个 step 调用 Tool Runtime，不直接执行脚本
每个 asset 请求走 Asset Broker
每个 action 走 Safety 和 Execution Boundary
高风险 action 进入 Approval，拒绝后 Skill run blocked
输出经过 DLP，写 taint record
```

## 必测用例

```text
manifest v2 正常解析
未知来源 Skill 安装后 trust_level=restricted
硬编码 secret 被 static analyzer 标记
terminal.run 通配权限被拒绝
Skill 未授权不能执行工具
Skill 授权后只能执行 grant 内工具
Skill 请求未授权资产被 Capability Graph 拒绝
Skill 输出含 api_key 被 DLP 脱敏
高风险 Skill 不允许 unattended scheduled task 使用
升级 Skill 后旧版本可回滚
release gate 标记未评测高风险 Skill
```

## 文件影响范围

| 模块 | 文件范围 |
|---|---|
| Schema | `apps/local-api/app/schemas/skill_governance.py`、`packages/core-types/core_types/skill_governance.py` |
| Migration | `apps/local-api/app/db/migrations/027_skill_governance.sql` |
| Repository | `apps/local-api/app/db/repositories/skill_governance_repo.py` |
| Services | `skill_plugin.py`、`capability.py`、`execution_boundary.py`、`design_alignment.py` |
| API | `routes_skills.py`、`routes_plugins.py` |
| Tests | `apps/local-api/tests/test_phase38_skill_governance.py` |

## 验收标准

```text
Skill 安装前能生成权限和风险预览
Skill 安装、启用、授权、运行、撤销、回滚有清晰状态
Skill 不能绕过 Asset Broker、Capability Graph、Safety、Tool Runtime
高风险或未评测 Skill 不能进入无人值守长期任务
Skill 输出和 MCP 输出都有 DLP/taint 记录
release report 增加 phase38 能力和风险摘要
不新增任何前端 UI 或桌面交互代码
```

## 与其他阶段关系

```text
第三十六阶段的 scheduled task 需要检查 Skill 是否允许 unattended
第三十七阶段的 browser_session 能力必须由 Skill grant 明确声明
第三十九阶段的 checkpoint/rollback 为 Skill 文件写入提供恢复保护
第四十阶段的外部消息 Skill 必须通过本阶段的治理后才能启用
```

