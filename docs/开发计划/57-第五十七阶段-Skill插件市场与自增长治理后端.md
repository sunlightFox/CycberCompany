# 第五十七阶段 - Skill 插件市场与自增长治理后端

## 阶段背景

OpenClaw 和 Hermes 都很重视可扩展能力：前者偏工具、插件和多入口生态，后者偏 Skills 和渐进式能力增长。我们仓库已经有 Skill、MCP、plugin、repository 和 governance 的基础，但离“用户能安装、启停、回滚、检索、复用、评估一个能力包”还有距离。

本阶段继续只做后端、schema、migration、repository、service、API、tests、evals 和文档；不新增前端页面、组件、样式、Tauri 窗口或桌面端交互代码。

## 参考结论

### OpenClaw 采用点

OpenClaw 的技能/插件思路更像可运营能力市场：

```text
可分发、可安装、可配置
工具与插件可被统一编排
```

本项目采用：

```text
Skill 负责方法，Plugin 负责能力装配
安装、启用、回滚和审计都走统一治理
```

### Hermes Agent 采用点

Hermes 的重点是 self-improvement：

```text
agent 可以逐步学会更好的技能
经验能转成可复用的方法
能力增长与任务结果绑定
```

本项目采用：

```text
任务成功/失败后自动推荐 skill 经验沉淀
高质量流程可转成可安装 skill bundle
```

## 核心目标

本阶段完成后，后端应支持：

```text
Skill/Plugin 市场后端目录和检索
安装、启用、停用、升级、回滚和隔离
Skill 与 MCP 的能力声明、依赖和评测结果关联
用户可见的能力包状态、健康度和审计
失败经验转成治理提示或候选 skill
```

## 阶段原则

1. Skill 不是资源本身，不能绕过资产系统。
2. 插件只扩展能力，不拥有系统密钥。
3. 未通过安全和评测的能力包不能自动升级为可用。
4. 安装和启用必须可回滚。
5. 运行期发现问题要能退回到稳定版本。

## 阶段范围

### 本阶段必须完成

```text
Skill marketplace repository 和 API
插件安装、启用、升级、回滚语义
skill / mcp / tool 能力依赖图
skill governance 评测和健康状态
沉淀经验转 skill candidate 的后端管线
```

### 本阶段不做

```text
不直接把外部仓库当成已验证能力
不让 Skill 直接读取 secret
不跳过审批和评测自动启用高风险能力
不新增 UI
```

## 主要待补

```text
能力包检索
安装和回滚
评测门禁
能力依赖图
经验转技能
```
