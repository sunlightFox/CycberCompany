# AGENTS.md

## 适用范围

本文件约束仓库内所有后续开发。若子目录未来出现更近的 `AGENTS.md`，以更近文件为准；否则一律遵守本文件。

## 当前阶段

- 当前阶段只开发后端，不开发 UI。
- 不新增前端页面、组件、样式、Tauri 窗口或桌面端交互代码，除非用户明确解除本约束。
- 可以设计 API、schema、事件流和错误模型，为未来 UI 接入预留稳定契约。
- 后端优先级：schema -> migration -> repository -> service -> API -> tests -> evals。

## 项目目标

本项目是可单机部署的个人智能体操作系统。前台最终会是极简聊天，后台包含组织、成员、资产、技能、记忆、任务、工具、MCP、安全和审计。首发只做公司壳，但核心层必须按可换壳架构设计。

## 必读文档

开发前至少阅读相关设计文档：

- `docs/00-阅读结论与最终决策.md`
- `docs/02-开发设计.md`
- `docs/06-数据模型与接口契约.md`
- `docs/08-Codex开发约束与路线图.md`
- `docs/11-后端服务与模块详细设计.md`
- 涉及任务、记忆、资产、Skill/MCP 时阅读对应详细设计文档。

## 不可违背规则

- 聊天页最终只显示当前聊天对象的人名、头像、状态和消息，不显示组织和壳。
- 核心层使用 `Organization` / `Member` / `Department` / `Role` / `Shell` / `Asset` / `Skill` / `Task`。
- 不把 `Employee`、`Company`、`Boss` 等公司壳概念写死到核心层。
- 壳只改变 UI 标签、菜单、模板和文案，不自动修改底层数据值。
- 系统管理固定，资产二级分类固定为大脑、账号、钱包、硬件、知识库。
- 资产访问必须经过 `Asset Broker`。
- 权限判断必须经过 `Capability Graph`。
- 高风险动作必须经过 `Safety` 和 `Approval`。
- 每次模型调用、工具调用、审批和记忆写入都要有 trace。
- 记忆写入必须包含 `source`。
- Skill 负责做事方法，不负责绕过系统资源查询。

## 编码与文件格式

- 所有新增和修改的源码、配置、Markdown、JSON、YAML、TOML 文件统一使用 UTF-8 编码。
- 不使用 GBK、ANSI 或其他本地编码保存文件。
- Markdown 和配置文件可以使用中文；代码标识符、模块名、文件名优先使用英文和 ASCII。
- Python 源码不需要额外添加 `# -*- coding: utf-8 -*-`，除非未来工具链明确需要。
- 保持换行和格式稳定，不做无关的全文件重排。

## 后端编码规范

- API handler 只做请求解析、调用 service、返回 schema、统一错误处理。
- 业务逻辑放在 service，不放在 route handler。
- 数据访问放在 repository 或 unit of work，不在 service 中散落 SQL。
- 先定义 Pydantic schema 和数据库 migration，再实现业务逻辑。
- 所有跨模块请求和返回值使用明确类型，不传裸 dict 作为长期接口。
- 错误统一转换为项目错误模型，错误码参考设计文档。
- 日志和 trace 不记录明文 secret、token、私钥、cookie 或钱包敏感信息。
- 涉及外部输入、工具调用、文件系统、终端、浏览器和网络动作时，先接入权限、风险和 trace。
- 异步接口保持 async 调用链一致，不在事件循环中执行阻塞 I/O。
- 新增公共函数和服务要有最小测试；涉及安全、任务状态、trace、资产权限时必须补覆盖。

## 后端目录约束

建议目录遵循：

```text
apps/local-api/app/
  main.py
  core/
  api/
  db/
  schemas/
  services/
  workers/

services/
  chat-runtime/
  context-gateway/
  brain/
  memory/
  task-engine/
  skill-engine/
  asset-broker/
  capability-graph/
  tools/
  safety/
  response-composer/
  trace/
  shell-runtime/
```

可以按实际技术栈微调，但依赖方向必须保持清晰。

## 依赖方向

允许：

- `api -> application services`
- `chat-runtime -> context-gateway / brain / task-engine / response-composer / memory / trace`
- `task-engine -> skill-engine / asset-broker / safety / trace`
- `skill-engine -> asset-broker / tools / mcp registry`
- `asset-broker -> capability-graph / asset repo / secret store`
- `safety -> capability-graph / policy config / trace`
- `context-gateway -> memory / capability-graph / shell-runtime`

禁止：

- 工具直接访问 secret。
- 模型路由直接读取数据库密钥。
- Skill 绕过 `Asset Broker`。
- API handler 直接执行工具。
- UI 参数直接进入 shell command。
- Memory 直接修改任务状态。
- 壳系统直接修改成员字段值。

## 测试与验收

- 后端提交前优先运行 `pytest`。
- 若项目启用 lint/typecheck，同时运行 `ruff check .` 和 `mypy .`。
- 涉及 DB 必须有 migration 或初始化脚本。
- 涉及 API 必须有 schema 和错误模型。
- 涉及工具调用必须有 trace 测试。
- 涉及高风险动作必须有 approval 或 deny 测试。
- 涉及壳系统必须验证切壳不修改底层业务值。

## 开发边界

- 不做无关重构。
- 不为了当前功能引入大型框架或复杂抽象。
- 不把未实现能力伪装成已完成能力。
- 不提交硬编码密钥、测试 token 或个人路径。
- 不自动删除用户数据、文档或未确认的本地文件。
