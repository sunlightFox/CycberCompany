# 第九十八阶段 - Git托管协作执行域聚焦打深与远程工程闭环

## 阶段定位

第九十七阶段已经把本地代码仓执行闭环打透：

```text
read repo
plan
patch
verify
repair once
summarize
```

但成熟工程代理不能只停在本地工作区。

真正高频的工程协作，还包括：

```text
分支管理
提交整理
远程同步
Issue / PR / Review / Comment
发布说明与交付证据
```

所以第九十八阶段不重复做 phase97 的“本地改码”，而是在它之上新增：

```text
local repo -> git -> remote forge -> issue/pr/review/release evidence
```

## 直接依赖

```text
docs/开发计划/21-第二十一阶段-工具MCP终端沙箱与执行边界硬化.md
docs/开发计划/27-第二十七阶段-OS级终端沙箱与本地执行隔离.md
docs/开发计划/38-第三十八阶段-Skill插件安全治理与能力市场后端.md
docs/开发计划/71-第七十一阶段-ToolRuntime终端队列与执行语义拆分重构.md
docs/开发计划/73-第七十三阶段-Skill_MCP与渠道桥接运行时重构.md
docs/开发计划/97-第九十七阶段-代码仓执行域聚焦打深与工程代理闭环.md
```

## 阶段目标

```text
把 Git 托管协作定义为 phase97 之后的首个远程工程执行域
建立 code_hosting_skill / forge_provider_adapter 统一抽象
打通 branch、commit、push、issue、PR、review、release note 的协作闭环
把远程协作证据接入 task replay / final_result / readiness / release
让 GitHub 作为首批 provider 样板，而不是核心层硬编码语义
```

## 本阶段范围

### 必须完成

```text
定义 code_hosting_skill capability profile
定义 forge_provider_adapter 契约
统一本地仓库状态与远程协作状态的任务结果语义
把 issue / PR / review / comment / status / release note 收口成远程协作工件
为 push / merge / release / repo admin 动作补齐 Safety / Approval / Trace
建立远程工程协作基准题库
```

### 明确不做

```text
不把 GitHub 特定 API 字段写死到核心层
不把普通终端 git 命令直接暴露成唯一产品语义
不在本阶段扩展到所有代码托管平台细节
不把 CI/CD 全自动发布和运维变更一并塞进本阶段
```

## 核心抽象

### 通用抽象

```text
code_hosting_skill
forge_provider_adapter
remote_artifacts
branch_state
commit_summary
pr_summary
review_outcome
publish_blockers
deliverable
```

### Provider 约束

```text
GitHub 只作为第一实现样板
后续 GitLab、Gitea、Bitbucket 应复用相同核心契约
provider 差异只放在 provider_type / provider_capabilities / adapter metadata
```

## 实施拆解

### 98.1 远程工程任务分类

目标：

```text
把远程工程动作从 phase97 的本地 repo 执行中独立出来
```

交付：

```text
code_hosting_readonly_request
code_hosting_sync_request
code_hosting_pr_request
code_hosting_review_request
code_hosting_release_request
```

### 98.2 本地与远程状态联动

目标：

```text
让本地修改、git 状态与远程协作对象形成一条连续证据链
```

交付：

```text
branch_state
commit_summary
remote_artifacts
```

要求：

```text
不能把本地 files_changed 与远程 PR 工件割裂
dirty workspace 要继续沿用 phase97 的谨慎规则
```

### 98.3 远程协作闭环

目标：

```text
把 issue / PR / review / comment / status / release note 做成统一工程协作主链
```

交付：

```text
pr_summary
review_outcome
publish_blockers
```

要求：

```text
查询状态和同步可低风险直通
push / merge / release / repo admin 必须审批
provider 不可用时要能留下清晰降级证据
```

### 98.4 结果与证据语义

目标：

```text
让远程工程任务输出像工程协作结果，而不是终端命令摘要
```

交付：

```text
remote_artifacts
branch_state
commit_summary
pr_summary
review_outcome
publish_blockers
deliverable
```

## 测试与验收

### 建议新增测试

```text
apps/local-api/tests/test_phase98_code_hosting_execution_closure.py
```

### 最小回归集

```text
apps/local-api/tests/test_phase21_execution_boundary.py
apps/local-api/tests/test_phase38_skill_plugin_security.py
apps/local-api/tests/test_phase73_skill_channel_runtime.py
apps/local-api/tests/test_phase97_repo_execution_closure.py
```

### 本阶段新增测试重点

```text
PR 创建与评论链路
review 请求与状态回读
push / merge 审批阻断
本地改动与远程协作证据联动
provider 不可用时的优雅降级
成功交付断言不只看模块存在
```

## 完成定义

```text
项目拥有本地代码仓之后的第二个被打透执行域：远程工程协作
系统能把本地 repo 改动、git 状态与远程 forge 协作结果串成统一证据链
GitHub 可以作为首批样板接入，但核心层不依赖 github_* 硬编码语义
```
