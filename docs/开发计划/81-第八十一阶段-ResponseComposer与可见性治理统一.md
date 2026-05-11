# 第八十一阶段 - ResponseComposer 与可见性治理统一

## 阶段定位

本阶段目标是把聊天主链路中“用户可见回复”与“内部诊断、证据、trace、恢复信息”彻底分层，避免后续 runtime、tool、task、memory 新能力继续把内部状态泄漏到最终回复。

## 直接依赖

```text
docs/16-聊天质量目标架构设计.md
docs/17-聊天质量核心接口契约设计.md
docs/开发计划/65-第六十五阶段-ResponseComposer与可见话术Catalog重构.md
docs/开发计划/67-第六十七阶段-自然语言动作确认与安全边界话术重构.md
docs/开发计划/80-第八十阶段-聊天内工具调用闭环.md
```

## ResponsePlan 标准结构

`ResponsePlan` 必须固定为“可见层 + 内部结构层”双层结构。

### 可见层

仅包含：

```text
plain_text
reply_blocks
approval_prompt
action_buttons
visible_status_hint
channel_render_overrides
```

### 内部结构层

仅供 runtime / trace / diagnostics 使用：

```text
structured_payload
response_filter
quality_guard
route_semantics
task_status_semantics
tool_status_semantics
memory_write_hints
prompt_assembly_metadata
```

### 强约束

```text
用户可见层不得直接读取 trace_id、internal error signature、raw diagnostics
内部结构层允许存这些信息，但默认不出现在 plain_text
```

## response_filter 统一规则

`response_filter` 必须成为所有最终用户可见文本输出前的统一关口。

职责：

```text
剔除内部 trace
剔除 recovery diagnostics
剔除原始 tool debug 信息
剔除 secret / token / cookie / credential
剔除不允许直接展示的 raw artifact 路径
```

### 输出模式

统一返回：

```text
visible_text
filtered_segments
suppression_reason_codes
```

## visible guard 统一规则

visible guard 负责判定：

```text
回复是否过度系统化
是否泄漏执行态内部细节
是否把内部确认态说成已完成
是否过度模板化
```

主要针对：

```text
tool reply
approval reply
failure reply
recovery reply
continuation rewrite
```

## pending action honesty 统一规则

以下状态必须有固定真实语义：

```text
pending_action
waiting_approval
running
failed
completed
cancelled
no_pending_action
multiple_pending_actions
```

### 统一话术原则

#### pending_action / waiting_approval

```text
只能说“我准备这样做 / 需要你确认”
不能说“我已经继续了”
```

#### running

```text
只能说“我正在处理 / 已经开始执行”
不能说“已经完成”
```

#### failed

```text
必须说清楚未完成
可给替代方案
```

#### completed

```text
只能在真实成功闭环后使用完成态
```

## 不同场景的回答策略

### 1. 闲聊

```text
自然、轻量、少系统感
不附带任务状态板
```

### 2. 说明 / 方案 / 分析

```text
结论优先
不假装执行
可结构化说明
```

### 3. 执行中

```text
说明当前动作与尚未完成的部分
不给“已完成”错觉
```

### 4. 待审批

```text
明确动作、风险、等待确认
给选择项但不泄漏内部审批记录
```

### 5. 失败

```text
明确失败影响
说明未完成范围
可建议重试/缩小/转任务
```

### 6. 完成

```text
总结真实完成结果
如有产物或证据，给可见摘要或安全引用
不附带内部 trace
```

## 禁止向用户泄漏的内容

明确禁止在任何渠道最终回复中出现：

```text
trace_id
internal recovery attempt
error_signature
tool raw stdout/stderr 大段原文
未经清洗的外部页面抓取全文
绝对本地路径
secret_ref
token / cookie / private key / wallet secret
```

## 渠道差异化输出约束

### local API / CLI

允许：

```text
更完整的结构化 payload
通过额外接口查询 diagnostics
```

但默认 `plain_text` 仍不能泄漏内部信息。

### 微信 / 飞书

要求：

```text
输出短、直接、移动端可读
不能暴露 JSON 结构
不能暴露 trace / diagnostics
approval 提示必须自然语言可确认
```

### 通用原则

```text
渠道差异只影响渲染形态，不改变真实性约束
```

## ResponseComposer 与 runtime 的边界

### runtime 负责

```text
告诉 Composer 当前真实状态
提供结构化事实
```

### Composer 负责

```text
把事实转成自然、诚实、可见的回复
按渠道裁剪格式
应用 visible filter 与 visible guard
```

### 明确禁止

```text
runtime 在多个分支手写长文案
channel gateway 自己重写主回复语义
tool runtime 直接拼最终聊天回复
```

## 验收标准

```text
ResponsePlan 可见层与内部结构层彻底分离
pending action honesty 规则统一
不同场景的可见回复自然且诚实
本地与渠道输出都不泄漏内部运行细节
tool / task / approval / failure / recovery 的可见语义统一
```

## 最小测试集

```text
plain_text 中不出现 trace_id
approval.required 场景不说成已继续
tool.failed 场景不说成已处理好
running 场景不说成已完成
local API 可查 structured_payload，但微信/飞书最终文案不暴露
response_filter 能清理 secret/token/path/diagnostic 文本
```
