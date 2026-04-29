# Codex 开发约束与路线图

## 开发总原则

这个项目适合让 Codex 或其他 AI 编程工具参与开发，但必须给清楚边界。核心原则：

```text
契约优先
链路优先
安全优先
本地优先
可追踪优先
小步提交
不要越权重构
不要把概念写死到代码里
```

## Codex 必须遵守的硬规则

1. 先读设计文档，再写代码。
2. 先写 schema、migration、API 契约，再写业务逻辑，再写 UI。
3. 聊天主链路必须经过 Chat Runtime、Context Gateway、Brain、Safety、Response Composer。
4. 任何工具调用必须写 trace。
5. 任何资产访问必须经过 Asset Broker 和 Capability Graph。
6. 模型上下文不得包含明文 secret。
7. 记忆写入必须包含 source。
8. 高风险动作必须走 approval。
9. 聊天页禁止显示组织名、壳名、部门树、资产面板。
10. 壳只改展示，不改底层值。
11. 系统管理固定，不随壳变。
12. 资产二级分类固定为大脑、账号、钱包、硬件、知识库。
13. 代码核心对象使用 Organization、Member、Department、Role、Shell、Asset、Skill。
14. 不写死 Employee、Company、Boss 等公司壳概念到核心层。
15. 所有新增服务必须有最小测试或评测用例。

## 建议 AGENTS.md

后续可以把以下内容写入仓库根目录 `AGENTS.md`：

```md
# AGENTS.md

## 项目目标

本项目是可单机部署的个人智能体操作系统。前台是极简聊天，后台包含组织、成员、资产、技能、记忆、任务、工具、MCP、安全和审计。

## 不可违背规则

- 聊天页只显示当前聊天对象的人名、头像、状态和消息，不显示组织和壳。
- 核心层使用 Organization / Member / Department / Role / Shell / Asset / Skill / Task，不写死公司壳。
- 壳只改变 UI 标签、菜单、模板和文案，不自动修改底层数据值。
- 系统管理固定，资产二级分类固定。
- 资产访问必须经过 Asset Broker。
- 权限判断必须经过 Capability Graph。
- 高风险动作必须经过 Safety 和 Approval。
- 每次模型调用、工具调用、审批和记忆写入都要有 trace。
- 记忆写入必须包含 source。
- Skill 负责做事方法，不负责绕过系统资源查询。

## 开发顺序

1. schema
2. db migration
3. service
4. api
5. tests
6. ui
7. evals

## 测试

后端提交前运行 pytest。
前端提交前运行 typecheck 和 lint。
涉及聊天链路必须补 trace 测试。
涉及安全必须补 approval 或 deny 测试。
```

## 开发阶段

### Phase 0：仓库初始化

目标：建立可持续开发的骨架。

任务：

```text
创建 monorepo 目录
创建 Tauri + React 桌面端
创建 FastAPI 本地 API
创建 SQLite migration 框架
创建配置加载
创建日志系统
创建 AGENTS.md
创建基础 CI 或本地脚本
```

验收：

```text
桌面端能启动
本地 API 能启动
/health 返回成功
数据库能初始化
前后端能通信
```

### Phase 1：基础数据和壳系统

目标：公司壳可加载，核心对象可 CRUD。

任务：

```text
实现 shells 表
实现 organizations 表
实现 departments 表
实现 roles 表
实现 members 表
加载 shells/company 配置
创建默认一人公司
创建默认部门和角色
创建默认小曜成员
```

验收：

```text
首次启动生成公司壳组织
UI 能显示菜单映射
系统管理菜单固定
切换壳不修改成员字段值
```

### Phase 2：极简聊天页

目标：可以和默认成员聊天。

任务：

```text
实现 conversations/messages 表
实现 /api/chat/turn
实现事件流
实现 Chat Runtime 骨架
实现 Response Composer V1
实现聊天 UI
实现会话列表
```

验收：

```text
聊天页只显示人名头像状态
不显示组织和壳
消息可流式输出
消息落库
trace 落库
```

### Phase 3：模型路由和 Brain V1

目标：支持本地/云模型路由和意图分流。

任务：

```text
实现 brains 表
实现模型配置页面
实现 OpenAI-compatible adapter
实现 Intent Classifier
实现 Mode Selector
实现 Model Router
实现 direct answer 链路
```

验收：

```text
可以配置至少一个模型
普通聊天走 direct
任务请求能识别为 task
模型调用有 trace
模型失败可 fallback
```

### Phase 4：Memory V1

目标：偏好和任务经历可以跨会话召回。

任务：

```text
实现 memory_items 表
实现 memory writer
实现 memory retriever
实现向量集合
实现记忆候选评分
实现记忆面板
实现记忆纠错
```

验收：

```text
用户偏好可写入
下次聊天可召回
错误记忆可修改或归档
冲突记忆使用 supersede
```

### Phase 5：资产中心 V1

目标：五类资产可管理，资源句柄可查询。

任务：

```text
实现 assets 表
实现 asset_grants 表
实现大脑资产页面
实现账号资产页面
实现钱包资产页面
实现硬件资产页面
实现知识库页面
实现 Asset Broker
实现 Capability Graph V1
```

验收：

```text
五类资产可创建
资产详情可编辑
资产 secret 不明文暴露给模型
成员可查询可用资产句柄
权限判断能返回 reason
```

### Phase 6：Task Engine V1

目标：聊天能创建可追踪任务。

任务：

```text
实现 tasks/task_steps 表
实现 Task Planner
实现 workflow runner
实现 task state machine
实现任务列表页
实现任务详情页
实现任务回放
```

验收：

```text
用户一句话可创建任务
任务有状态
任务步骤可查看
任务可完成或失败
trace 可回放
```

### Phase 7：Tools V1

目标：文件、浏览器、终端三类核心能力可用。

任务：

```text
实现 file tools
实现 browser tools
集成 Playwright 或 Playwright MCP
实现 terminal runner
实现 tool_calls 表
实现工具风险分级
```

验收：

```text
可以读取授权文件
可以写入新文件
可以打开网页并获取 snapshot
可以执行低风险命令
高风险命令被拦截
所有工具调用有 trace
```

### Phase 8：Safety V1

目标：高风险动作确认，注入和泄密有基础防护。

任务：

```text
实现 approvals 表
实现 risk classifier
实现 approval API
实现安全确认 UI
实现敏感目录 denylist
实现 DLP 检查
实现 untrusted context 标记
```

验收：

```text
删除文件必须确认
外部发布必须确认
钱包动作必须强确认
网页注入不能触发 secret 外发
审批记录可审计
```

### Phase 9：Skill 与 MCP V1

目标：Skill 包可安装，MCP 服务可配置。

任务：

```text
实现 skills 表
实现 Skill registry
实现 bundle.yaml parser
实现 SKILL.md loader
实现 Skill matcher
实现 MCP server registry
实现 MCP tools sync
实现 Skill eval runner
```

验收：

```text
Skill 包可安装
Skill 可启停
Skill 可被任务匹配
MCP 服务可连接
MCP 工具进入能力注册表
Skill 运行有 trace
```

### Phase 10：多成员协作 V1

目标：支持 supervisor 模式。

任务：

```text
实现 participant selection
实现 supervisor planner
实现 subtask context packet
实现成员发言轮数限制
实现主持汇总
实现协作 trace
```

验收：

```text
技术方案类任务可分配给产品和技术成员
每个成员只拿必要上下文
最终由主持成员汇总
输出不戏剧化
```

## Sprint 规划

| Sprint | 目标 | 交付 |
|---|---|---|
| S1 | 基础骨架 | Tauri、FastAPI、SQLite、health |
| S2 | 壳和组织 | 公司壳、组织、成员、部门 |
| S3 | 聊天 V1 | 聊天 UI、消息、流式输出 |
| S4 | Brain V1 | 模型路由、意图识别、direct |
| S5 | Memory V1 | 记忆写入、召回、面板 |
| S6 | 资产 V1 | 五类资产、Asset Broker |
| S7 | Task V1 | 任务、步骤、状态、回放 |
| S8 | Tools V1 | 文件、浏览器、终端 |
| S9 | Safety V1 | 风险、确认、审计、DLP |
| S10 | Skill/MCP V1 | Skill 包、MCP 服务 |
| S11 | 多成员协作 | supervisor、子任务、汇总 |
| S12 | 打磨 | Evals、性能、备份、安装包 |

## 可直接拆给 Codex 的 Tickets

| Ticket | 描述 | 验收 |
|---|---|---|
| DB-001 | 创建 shells/organizations/departments/roles/members migration | 测试库能初始化 |
| SHELL-001 | 加载 shells/company 配置 | 能返回菜单和术语映射 |
| UI-CHAT-001 | 实现极简聊天页 | 顶部只显示头像、人名、状态 |
| API-CHAT-001 | 实现 `/api/chat/turn` | 返回 turn_id 和事件流 |
| TRACE-001 | 实现 trace 和 span 写入 | 每轮聊天有 trace |
| BRAIN-001 | 实现模型 adapter | 能调用 OpenAI-compatible endpoint |
| BRAIN-002 | 实现意图识别 | task/direct 分流通过测试 |
| MEM-001 | 实现 memory_items 表和 writer | 偏好可写入 |
| MEM-002 | 实现 memory retriever | 下次聊天可召回 |
| ASSET-001 | 实现资产 CRUD | 五类资产可创建 |
| CAP-001 | 实现 Capability Graph V1 | allow/deny 有 reason |
| TASK-001 | 实现 tasks/task_steps 表 | 任务可创建和查询 |
| TASK-002 | 实现 workflow runner | 固定步骤任务可完成 |
| TOOL-001 | 实现 file.read/write/list | 工具调用有 trace |
| TOOL-002 | 集成 browser snapshot | 能读取网页结构 |
| SAFETY-001 | 实现 risk matrix | 删除动作为 R5 |
| APPROVAL-001 | 实现 approval API | 可确认或拒绝 |
| SKILL-001 | 实现 bundle parser | 读取 bundle.yaml |
| MCP-001 | 实现 MCP server registry | 可登记服务和工具 |
| EVAL-001 | 建立最小回归集 | 10 条 eval 可运行 |

## 文件级实现建议

后端：

```text
apps/local-api/app/main.py
apps/local-api/app/core/config.py
apps/local-api/app/db/session.py
apps/local-api/app/db/migrations/
apps/local-api/app/api/routes_chat.py
apps/local-api/app/api/routes_members.py
apps/local-api/app/api/routes_assets.py
apps/local-api/app/api/routes_tasks.py
apps/local-api/app/api/routes_settings.py
apps/local-api/app/schemas/chat.py
apps/local-api/app/schemas/member.py
apps/local-api/app/schemas/asset.py
apps/local-api/app/schemas/task.py
```

服务：

```text
services/chat-runtime/runtime.py
services/context-gateway/builder.py
services/brain/intent.py
services/brain/model_router.py
services/memory/writer.py
services/memory/retriever.py
services/task-engine/runner.py
services/skill-engine/registry.py
services/asset-broker/broker.py
services/capability-graph/policy.py
services/safety/risk.py
services/safety/approval.py
services/response-composer/composer.py
services/trace/tracer.py
```

前端：

```text
apps/desktop-tauri/src/pages/chat/
apps/desktop-tauri/src/pages/members/
apps/desktop-tauri/src/pages/organization/
apps/desktop-tauri/src/pages/assets/
apps/desktop-tauri/src/pages/tasks/
apps/desktop-tauri/src/pages/settings/
apps/desktop-tauri/src/components/chat/
apps/desktop-tauri/src/components/layout/
apps/desktop-tauri/src/api/client.ts
apps/desktop-tauri/src/stores/chatStore.ts
```

## UI 开发约束

### 聊天页

必须：

```text
干净
少元素
输入框明显
消息可读
状态清楚
确认动作明确
```

禁止：

```text
右侧复杂卡片面板
组织树
壳名称
部门背景
资产卡片堆
大量功能说明文字
```

### 管理页

管理页要完整，但风格克制：

```text
列表
详情
表单
状态
筛选
日志
确认
```

不要做成营销页或过度装饰的仪表盘。

## 测试策略

### 后端测试

```text
schema 测试
migration 测试
service 单测
API 集成测试
安全策略测试
记忆写入测试
任务状态机测试
```

### 前端测试

```text
聊天页渲染
菜单映射
表单校验
审批弹窗
任务详情
设置页
```

### E2E

```text
首次启动
创建成员
发送聊天
写入记忆
创建资产
执行任务
高风险确认
切换壳
查看回放
```

## 性能目标

| 指标 | MVP 目标 |
|---|---|
| 冷启动 | 10 秒内进入 UI |
| 本地 API 启动 | 5 秒内 |
| 普通聊天首 token | 3 秒内，本地模型视硬件浮动 |
| direct 回复 trace 写入 | 100ms 内 |
| 记忆检索 | 500ms 内 |
| 资产句柄查询 | 200ms 内 |
| 任务列表加载 | 500ms 内 |

## 本地电脑适配

最低：

```text
16GB RAM
4 核 CPU
50GB 可用磁盘
可使用云模型
```

推荐：

```text
32GB RAM
8 核 CPU
8GB+ 显存或等效统一内存
100GB SSD
本地 7B-14B 日常模型
```

优化策略：

```text
向量库懒加载
MCP 按需启动
浏览器按任务启动
模型路由按需调用
任务结束后释放资源
日志定期压缩
```

## 世界级架构要求

要把产品做成世界级，不能只靠模型强。必须做到：

```text
聊天体验有温度
上下文干净
记忆可信
任务可完成
工具边界清楚
安全不是摆设
失败可解释
能力可沉淀
扩展不推翻
本地能跑
```

工程上对应：

| 能力 | 架构要求 |
|---|---|
| 温暖聊天 | Heart + Persona + Response Composer |
| 强上下文 | Context Gateway |
| 长期记忆 | 分层 Memory + source + supersede |
| 真执行 | Task Engine + Tools + MCP |
| 可控资源 | Asset Broker + Capability Graph |
| 安全 | Safety + Approval + DLP + Sandbox |
| 可回放 | Trace + Artifacts |
| 可成长 | Skill Candidate + Eval |
| 可换壳 | Shell Runtime |
| 单机可用 | SQLite + 本地向量库 + local-first runtime |

## 发布检查清单

```text
聊天页无组织/壳信息
公司壳默认可用
创建成员流程不超过 4 个必填项
五类资产可管理
模型配置可用
记忆可写入和纠错
任务可执行和回放
删除文件必须确认
外部发布必须确认
secret 不进入模型上下文
trace 可导出
Skill 包有最小 eval
MCP 服务可启停
SQLite 可备份
应用可离线启动
```

