# 第九十九阶段 - 内容平台Skill执行域聚焦打深与平台工作流闭环

## 阶段定位

代码仓和远程工程协作之外，另一个高频真实做事域是：

```text
内容平台工作流
```

这类需求表面上经常以“小红书发帖”出现，但系统不应该把任何单平台硬编码进核心层。

第九十九阶段要做的是：

```text
把小红书这类需求上升为内容平台 Skill 执行域
由 Skill + provider adapter + account asset + Safety / Approval 统一承载
```

## 直接依赖

```text
docs/开发计划/37-第三十七阶段-持久浏览器会话与网页登录资产化.md
docs/开发计划/42-第四十二阶段-通用外部平台动作编排与账号资产链路.md
docs/开发计划/47-第四十七阶段-浏览器持久执行真实化与外部平台Provider插件化.md
docs/开发计划/50-第五十阶段-无开放API外部平台浏览器MCP操作适配器闭环.md
docs/开发计划/55-第五十五阶段-持久浏览器会话与登录态资产化深化.md
docs/开发计划/73-第七十三阶段-Skill_MCP与渠道桥接运行时重构.md
docs/开发计划/96-第九十六阶段-AgentLoop主链路加厚与观察重规划闭环.md
```

## 阶段目标

```text
建立 content_platform_skill / social_platform_provider 抽象
把内容平台工作流收口成选题、素材、草稿、适配、发布、复盘的统一闭环
让小红书作为第一批 provider 样板，但不把平台名写死到核心层
把登录态、账号资产、发布审批、回读证据接入统一 task replay
```

## 本阶段范围

### 必须完成

```text
定义内容平台任务分类与 capability profile
定义 social_platform_provider 元数据与约束契约
把内容草稿、平台约束校验、发布候选、发布结果、互动回读做成结构化证据
把发布和外发动作统一走 Safety / Approval / Trace
建立内容平台工作流基准题库
建立受控测试账号注入机制，测试时通过账号资产和 secret 引用提供平台登录态
```

### 明确不做

```text
不把“小红书发帖服务”写成核心层对象
不在本阶段同时铺满所有内容平台特例
不把平台登录、浏览器自动化、账号资产链路绕出 Skill / Asset Broker / Safety 主链
不把任何平台测试账号、手机号、密码、cookie 或 token 以明文写入仓库文档、测试代码或 trace
```

## 核心抽象

```text
content_platform_skill
social_platform_provider
platform_profile
post_draft
publish_candidate
engagement_snapshot
deliverable
```

## 闭环主链

```text
选题/目标输入
素材收集
内容草拟
平台约束适配
发布前检查
发布或发布草稿
数据回读与复盘
```

## 实施拆解

### 99.1 平台任务分类与 provider 元数据

目标：

```text
把内容平台需求从普通浏览器任务中独立出来
```

交付：

```text
content_platform_draft_request
content_platform_publish_request
content_platform_review_request
content_platform_insight_request
```

要求：

```text
平台差异必须由 provider metadata 表达
不得把平台长度、媒体格式、发布权限等限制写死在核心判断分支
测试账号必须以 account asset + secret reference 方式注入，不允许明文硬编码
```

### 99.2 内容草稿与发布候选

目标：

```text
把“发什么”和“能不能发”拆成两个结构化阶段
```

交付：

```text
post_draft
publish_candidate
```

要求：

```text
草稿生成不等于直接发布
平台约束校验、风险检查、账号权限检查必须显式留痕
```

### 99.3 发布与回读

目标：

```text
把发布、发布草稿、互动回读和失败恢复做成统一工作流
```

交付：

```text
publish_result
engagement_snapshot
recovery_evidence
```

要求：

```text
登录态和账号资产必须经过授权校验
发布失败要保留草稿、平台反馈和恢复建议
中国大陆测试账号可以作为默认验收样板，但只能以受控账号资产形式登记，不能出现在仓库明文中
```

### 99.4 内容平台测试账号治理

目标：

```text
让平台联调和真实测试可重复，但不泄露测试账号凭据
```

交付：

```text
content_platform_test_account asset type
secret reference binding
provider-specific login bootstrap
sanitized replay / trace policy
```

要求：

```text
测试账号手机号、密码、cookie、token 统一存放在系统外 secret store 或本地受控配置
任务与 Skill 只拿 account handle / asset handle，不直接看到明文
trace、replay、diagnostics、失败快照都要脱敏，不能回显账号秘密
小红书等首批 provider 的联调测试遵循同一治理方式，不额外开后门
```

## 测试与验收

### 建议新增测试

```text
apps/local-api/tests/test_phase99_content_platform_skill_closure.py
```

### 最小回归集

```text
apps/local-api/tests/test_phase42_platform_action_orchestration.py
apps/local-api/tests/test_phase47_browser_provider_pluginization.py
apps/local-api/tests/test_phase50_platform_browser_adapter_closure.py
apps/local-api/tests/test_phase55_login_state_assetization.py
```

### 本阶段新增测试重点

```text
草稿生成
平台约束校验
发布审批
登录态/账号资产校验
provider 切换兼容
发布失败后的可恢复状态
成功交付断言不只看模块存在
测试账号凭据不会出现在仓库、日志、trace、replay 和结果摘要中
```

## 完成定义

```text
项目拥有首个通过 Skill 深打透的平台型执行域：内容平台工作流
小红书可作为首批 provider 样板接入，但核心层只认 content_platform_skill / social_platform_provider 抽象
系统能稳定输出草稿、发布候选、发布结果、互动回读和恢复证据
内容平台真实测试依赖受控账号资产，而不是仓库内明文账号密码
```
