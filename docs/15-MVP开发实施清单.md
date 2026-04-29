# MVP 开发实施清单

## MVP 目标

MVP 不是做一个概念 demo，而是做出最小可长期使用闭环：

```text
本地能启动
能创建公司壳组织
能创建成员
能配置大脑
能聊天
能记忆
能创建任务
能调用文件/浏览器/终端基础工具
能管理五类资产
高风险确认
任务可回放
Skill/MCP 有基础框架
```

## 不进入 MVP

```text
多人 SaaS
社区市场
自动支付
多设备同步
多壳编辑器
实时视频
复杂图谱数据库
完全自治后台代理
```

## 实施总顺序

```text
0. 仓库和基础设施
1. 数据库和配置
2. 壳系统和默认组织
3. 前端主布局和聊天页
4. 聊天 API 和模型路由
5. Trace
6. Memory V1
7. 资产中心 V1
8. Task Engine V1
9. Tools V1
10. Safety / Approval V1
11. Skill / MCP V1
12. 多成员协作 V1
13. Evals 和打磨
```

## Milestone 0：仓库和基础设施

### 任务

| ID | 任务 | 输出 |
|---|---|---|
| M0-001 | 创建项目目录 | `apps/`、`services/`、`packages/`、`data/` |
| M0-002 | 初始化 Tauri + React | 桌面应用可启动 |
| M0-003 | 初始化 FastAPI | `/health` 可访问 |
| M0-004 | 创建配置系统 | `config/app.yaml` 可加载 |
| M0-005 | 创建日志系统 | 控制台和文件日志 |
| M0-006 | 创建 AGENTS.md | 开发约束落库 |

### 验收

```text
运行桌面端能看到空应用
运行 API 能返回 health
配置加载失败有清晰错误
```

## Milestone 1：数据库和核心 schema

### 任务

```text
创建 migration 框架
创建 shells 表
创建 organizations 表
创建 departments 表
创建 roles 表
创建 members 表
创建 conversations/messages 表
创建 traces/trace_spans 表
创建基础 repository
```

### 验收

```text
测试库可从零初始化
重复运行 migration 不破坏数据
repository 基础 CRUD 通过测试
```

## Milestone 2：壳系统和默认公司壳

### 任务

```text
创建 shells/company 配置
实现 ShellRuntime loader
实现菜单映射
实现术语映射
创建默认组织
创建默认部门
创建默认角色
创建默认小曜成员
```

### 验收

```text
首次启动自动创建一人公司
菜单显示公司壳映射
系统管理固定
资产二级分类固定
切壳不修改成员字段值
```

## Milestone 3：前端主布局和聊天页

### 任务

```text
实现 AppShell
实现 Sidebar
实现 ChatPage
实现 ChatHeader
实现 MessageList
实现 ChatInput
实现 ConversationList
实现 Onboarding
```

### 验收

```text
聊天页顶部只显示头像、人名、状态
聊天页不显示组织名
聊天页不显示壳名
左侧菜单可进入管理页
首次启动流程可进入聊天
```

## Milestone 4：聊天 API 和模型路由

### 任务

```text
实现 brains 表
实现模型设置 API
实现 OpenAI-compatible adapter
实现 /api/chat/turn
实现 Chat Runtime direct 模式
实现事件流
实现 Response Composer V1
```

### 验收

```text
配置模型后可聊天
回复可流式显示
消息落库
模型失败有错误提示
```

## Milestone 5：Trace V1

### 任务

```text
实现 TraceService
实现 span 写入
接入 Chat Runtime
接入模型调用
接入 Response Composer
实现 trace 查询 API
```

### 验收

```text
每次聊天都有 trace
trace 包含 turn、model.call、response.compose
trace 可在开发者页面查看
```

## Milestone 6：Memory V1

### 任务

```text
创建 memory_items 表
实现 MemoryWriter
实现 MemoryRetriever
实现 MemoryScorer
实现 ConflictResolver
接入 Context Gateway
实现记忆管理页面
```

### 验收

```text
用户明确要求记住的信息可写入
下次相关聊天能召回
用户能编辑或归档记忆
旧记忆能被 supersede
记忆写入有 source
```

## Milestone 7：资产中心 V1

### 任务

```text
创建 assets 表
创建 asset_grants 表
实现资产 CRUD API
实现大脑/账号/钱包/硬件/知识库页面
实现 Asset Broker
实现 Capability Graph V1
实现 secret_ref 存储接口
```

### 验收

```text
五类资产可创建
账号 secret 不回显
资产查询只返回句柄和摘要
权限拒绝有 reason
```

## Milestone 8：Task Engine V1

### 任务

```text
创建 tasks/task_steps 表
实现 TaskStateMachine
实现 TaskPlanner V1
实现 WorkflowRunner V1
实现任务 API
实现任务列表和详情
实现任务回放基础
```

### 验收

```text
聊天中可创建任务
任务状态可变化
任务步骤可查看
任务详情能看到 trace
```

## Milestone 9：Tools V1

### 任务

```text
实现 ToolRegistry
实现 ToolRuntime
实现 file.list/read/write
实现 browser.open/snapshot
实现 terminal.run
工具调用接入 Safety 和 Trace
```

### 验收

```text
文件读取限制在授权目录
写新文件成功
覆盖文件需要确认
浏览器可读取页面 snapshot
终端高风险命令被拦截
所有工具调用有 trace
```

## Milestone 10：Safety / Approval V1

### 任务

```text
创建 approvals 表
实现 RiskClassifier
实现 ApprovalService
实现 DLP V1
实现敏感目录 denylist
实现 approval UI
```

### 验收

```text
删除文件必须确认
外部发布必须确认
钱包动作强确认
用户拒绝后任务不继续执行原动作
审批记录可查
```

## Milestone 11：Skill / MCP V1

### 任务

```text
创建 skills 表
实现 bundle.yaml parser
实现 SKILL.md loader
实现 SkillRegistry
实现 SkillMatcher
实现 SkillRunner V1
实现 MCPServerRegistry
实现 MCP tool sync
实现 Skill 管理页面
实现 MCP 管理页面
```

### 验收

```text
Skill 包可安装
Skill 可启停
Skill 可匹配任务
MCP 服务可配置
MCP 工具可注册
高风险 Skill 动作触发确认
```

## Milestone 12：多成员协作 V1

### 任务

```text
实现 supervisor planner
实现参与成员选择
实现子任务上下文构建
实现并行/串行执行
实现主持人汇总
实现协作 trace
```

### 验收

```text
技术方案任务可调用产品和技术成员
子成员只拿必要上下文
输出由主持人汇总
不会出现多成员无意义闲聊
```

## Milestone 13：Evals 和打磨

### 任务

```text
建立 eval runner
建立聊天质量 eval
建立记忆 eval
建立安全 eval
建立任务 eval
建立壳系统 eval
性能优化
备份恢复
安装包
```

### 验收

```text
至少 30 条回归用例
核心安全用例全通过
聊天页无组织壳信息
应用可离线启动
数据可备份恢复
```

## MVP 必须通过的端到端用例

### E2E-001 首次启动

```text
打开应用
创建一人公司
选择模型
创建小曜
进入聊天页
```

通过标准：

```text
聊天页只显示小曜头像、人名、状态
不显示组织和壳
```

### E2E-002 记忆

```text
用户：以后回答先给结论，再展开
系统写入记忆
新会话询问设计方案
系统先给结论
```

### E2E-003 资产

```text
创建小红书账号资产
绑定内容草稿 Skill
墨白生成草稿
发布动作触发确认
```

### E2E-004 任务

```text
用户：帮我整理这个文件夹并生成报告
系统创建任务
扫描文件
生成报告
删除动作不自动执行
```

### E2E-005 壳规则

```text
成员岗位为技术经理
切换到宗门壳
字段标签变为身份
值仍是技术经理
```

### E2E-006 多成员

```text
用户：帮我做一个技术方案
小曜主持
宁宁补产品边界
阿珩补技术架构
小曜汇总
```

## 每次提交前检查

```text
后端测试通过
前端类型检查通过
lint 通过
涉及 DB 有 migration
涉及 API 有 schema
涉及工具有 trace
涉及高风险有 approval
涉及壳不改底层值
聊天页无组织和壳
```

## 建议测试命令

具体命令以后随技术栈调整，原则如下：

```bash
# 后端
pytest
ruff check .
mypy .

# 前端
npm run typecheck
npm run lint
npm run test

# E2E
npm run e2e

# Evals
python -m evals.run
```

