# Skill MCP Tool 插件详细设计

## 核心分工

| 概念 | 职责 |
|---|---|
| Tool | 一次具体动作 |
| MCP | 外部工具和资源协议 |
| Skill | 可复用做事方法 |
| Plugin/Bundle | 打包 Skill、MCP 配置、脚本、测试和权限 |
| Asset | 被使用的资源 |
| Capability Graph | 判断能否使用 |
| Safety | 判断风险和确认 |

核心规则：

```text
Skill 不负责绕过资源权限
Tool 不直接读取资产密钥
MCP 不默认拥有全部资产
Plugin 安装前必须显示权限
所有工具调用必须 trace
```

## Tool 设计

Tool 必须小、明确、可审计。

Tool schema：

```json
{
  "name": "file.write",
  "description": "在授权工作区写入新文件",
  "input_schema": {
    "type": "object",
    "properties": {
      "path": {"type": "string"},
      "content": {"type": "string"},
      "overwrite": {"type": "boolean"}
    },
    "required": ["path", "content"]
  },
  "risk": {
    "default": "R2",
    "overwrite_true": "R3"
  }
}
```

执行流程：

```text
validate input
resolve handles
capability decide
safety evaluate
approval if needed
execute
redact result
trace
```

## 基础 Tool 列表

### 文件

```text
file.list
file.read
file.write
file.move
file.copy
file.delete
file.hash
```

### 浏览器

```text
browser.open
browser.snapshot
browser.click
browser.fill
browser.submit
browser.screenshot
browser.download
```

### 终端

```text
terminal.run
terminal.stop
terminal.read_log
```

终端高风险命令必须确认。

### 知识库

```text
knowledge.search
knowledge.add_source
knowledge.reindex
knowledge.get_chunk
```

### 资产

```text
asset.query
asset.verify
asset.request_handle
```

### 记忆

```text
memory.search
memory.write_candidate
memory.correct
```

## MCP 设计

MCP 服务注册：

```yaml
id: playwright
display_name: Playwright MCP
command: npx
args:
  - "@playwright/mcp"
env_refs: []
enabled: true
auto_start: false
allowed_skills:
  - browser_research
risk_policy:
  external_submit: R4
  file_download: R3
```

MCP 工具同步后进入 Tool Registry，但必须标记来源：

```json
{
  "tool_name": "mcp.playwright.browser_click",
  "source": "mcp",
  "server_id": "playwright",
  "risk_level": "R2"
}
```

MCP 不允许：

```text
默认读取所有本地文件
默认拿全部账号 secret
绕过 Asset Broker
绕过 Safety
```

## Skill 包结构

```text
bundles/
  content-xiaohongshu/
    bundle.yaml
    SKILL.md
    prompts/
      draft.md
      review.md
    scripts/
      normalize_tags.py
    mcp/
      servers.yaml
    tests/
      eval_cases.yaml
    signatures/
      bundle.sig
```

## bundle.yaml 详细字段

```yaml
id: content-xiaohongshu
version: 0.1.0
display_name: 小红书内容技能包
description: 生成小红书风格草稿，支持账号风格读取和发布前确认。
kind: skill_bundle
author: local
entry_skills:
  - xhs_draft
  - xhs_review
triggers:
  intents:
    - social_copywriting
    - content_draft
  keywords:
    - 小红书
    - 草稿
required_assets:
  - type: account
    platform: xiaohongshu
    optional: true
required_tools:
  - memory.search
  - asset.query
  - browser.open
permissions:
  net:
    allow_domains:
      - xiaohongshu.com
  fs:
    write:
      - workspace://artifacts/**
risk_policy:
  confirmation_required_for:
    - external_post
    - account_profile_edit
outputs:
  schema: ./schemas/xhs_draft.schema.json
evals:
  - tests/eval_cases.yaml
```

## SKILL.md 规范

必须包含：

```text
用途
何时使用
输入
输出
步骤
可用工具
风险规则
失败处理
不要做什么
```

示例结构：

```md
# 小红书草稿 Skill

## 何时使用

用户要求生成小红书草稿、标题、标签或发布前内容检查时使用。

## 输入

- 主题
- 目标用户
- 账号风格摘要，可选

## 步骤

1. 查询用户内容偏好。
2. 如果有账号资产句柄，读取账号风格摘要。
3. 生成草稿。
4. 检查敏感内容。
5. 输出草稿，不自动发布。

## 禁止

- 不自动发布。
- 不读取账号明文密码。
- 不修改账号资料。
```

## Skill 匹配

匹配信号：

```text
intent
keywords
member default skills
department default skills
asset bound skills
historical success
user preference
```

匹配输出：

```json
{
  "skill_id": "xhs_draft",
  "confidence": 0.88,
  "reason": "用户要求生成小红书草稿",
  "required_assets": ["account:xiaohongshu"],
  "required_tools": ["memory.search", "asset.query"]
}
```

## Skill 执行

流程：

```text
load manifest
validate input
query assets
capability decide
safety evaluate
execute steps
collect outputs
run lightweight eval
trace skill.run
```

Skill 输出：

```json
{
  "status": "success",
  "summary": "已生成小红书草稿",
  "artifacts": [],
  "memory_candidates": [],
  "next_actions": [
    {"type": "approval", "label": "发布草稿", "risk": "R4"}
  ]
}
```

## Plugin 安装

安装流程：

```text
读取 bundle.yaml
校验结构
检查签名
展示权限
检查依赖
写入 registry
默认启用低风险 Skill
高风险能力默认需确认
```

权限展示：

```text
这个技能包需要：
- 访问浏览器能力
- 写入任务工件目录
- 可查询小红书账号资产摘要
- 发布动作需要确认
```

## Skill 评测

每个 Skill 必须至少有 eval case。

`tests/eval_cases.yaml`：

```yaml
cases:
  - id: xhs_draft_basic
    input:
      topic: 个人智能体 OS
      style: 专业但有吸引力
    expected:
      contains:
        - 标题
        - 正文
        - 标签
      forbidden:
        - 自动发布
```

评测维度：

```text
是否触发正确
输出格式是否对
是否越权
是否需要确认
是否写 trace
```

## Skill 成长

候选来源：

```text
用户明确要求以后按此流程
任务多次成功
用户对输出高满意
同一流程反复出现
```

候选转正式：

```text
生成 Skill 草稿
显示步骤和权限
用户确认
生成 bundle
加入 registry
加入 eval case
```

## 实现文件

```text
services/tools/registry.py
services/tools/runtime.py
services/tools/file_tools.py
services/tools/browser_tools.py
services/tools/terminal_tools.py
services/tools/mcp_adapter.py
services/skill-engine/bundle_loader.py
services/skill-engine/manifest.py
services/skill-engine/skill_loader.py
services/skill-engine/matcher.py
services/skill-engine/runner.py
services/skill-engine/eval_runner.py
services/skill-engine/candidate.py
apps/local-api/app/api/routes_skills.py
apps/local-api/app/api/routes_mcp.py
```

## 验收用例

```text
安装合法 Skill 包成功
缺少 bundle.yaml 安装失败
高风险权限安装前展示
Skill 匹配小红书草稿任务
Skill 查询账号只拿到句柄摘要
发布动作触发 approval
MCP 工具断开后任务降级
Tool 调用写入 trace
Skill eval 可运行
```

