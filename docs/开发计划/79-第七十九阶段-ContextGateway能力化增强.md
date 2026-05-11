# 第七十九阶段 - ContextGateway 能力化增强

## 阶段定位

本阶段目标是把 `ContextGateway` 从“会聊天的上下文组装器”升级为“既能维持聊天质量，又能为执行决策提供安全上下文的运行时中层”。

## 直接依赖

```text
docs/05-资产中心与能力系统设计.md
docs/06-数据模型与接口契约.md
docs/11-后端服务与模块详细设计.md
docs/12-记忆系统详细设计.md
docs/开发计划/70-第七十阶段-SessionContext与上下文可见性治理重构.md
docs/开发计划/78-第七十八阶段-会话与渠道语义统一.md
```

## 当前问题

当前 `ContextPacket` 已包含：

```text
recent history
summary
memories
persona
heart
workbench
```

但仍存在以下不足：

```text
能力摘要缺失或零散
resource_handles 过于偏知识库
untrusted_context 基本为空
safety_notes 还是固定占位
session context 与 presence context 合流不稳定
```

## ContextPacket 目标分层

最终 `ContextPacket` 必须显式支持以下层次：

```text
recent_history
conversation_summary
session_context
memory
persona
heart
capability_summary
asset_handles
untrusted_context
workbench
safety_notes
```

### 1. recent_history

内容：

```text
当前 session 下最近消息
按 token budget 裁剪
保留 author_type、created_at、model_safe_text
```

要求：

```text
只能注入经过 redaction 的文本
不混入旧 session 的消息
```

### 2. conversation_summary

内容：

```text
长期 conversation 摘要
工作态摘要
上轮 continuity summary
```

要求：

```text
用于跨 turn 承接
不得覆盖当前显式改口
```

### 3. session_context

内容：

```text
当前 open loops
pending approval
running task
latest instruction override
continuity mode
assistant commitments
```

要求：

```text
这是运行时态，不是长期记忆
必须优先于旧 memory 参与当前回合裁决
```

### 4. memory

内容：

```text
episodic memory
semantic memory
procedure / experience memory
knowledge recall references
```

要求：

```text
必须保留 source
必须支持抑制过期或冲突记忆
不得把临时执行态误写成长期事实
```

### 5. persona

内容：

```text
成员长期人格
稳定语言风格
能力边界披露倾向
```

### 6. heart

内容：

```text
关系姿态
当前交流氛围
风险语气约束
```

### 7. capability_summary

内容：

```text
当前成员可用能力类别
是否具备工具能力
是否具备任务委托能力
是否具备浏览器、终端、知识检索、外部账号等能力
```

要求：

```text
给模型的是摘要，不是 secret 或完整资产详情
```

### 8. asset_handles

内容：

```text
可用资源句柄摘要
允许动作
需要审批的动作
资源来源与可信级别摘要
```

要求：

```text
默认不把 secret 或 credential 注入模型
通过 Asset Broker 查询
至少支持 brain / account / hardware / knowledge_base 类别摘要
```

### 9. untrusted_context

内容：

```text
网页抓取结果
外部平台非可信文本
用户上传内容
浏览器/MCP 返回的外部证据摘录
```

要求：

```text
必须标注 trusted_level
必须记录 source_ref
不得与 trusted memory 混淆
```

### 10. workbench

内容：

```text
最近工作台上下文
相关 artifact
关联 task / file / browser session 摘要
```

### 11. safety_notes

内容：

```text
privacy classification
当前风险级别摘要
需要保守处理的边界提示
```

要求：

```text
不得再使用固定占位文案
必须由 Safety / Privacy / Capability 结果动态生成
```

## token budget 分配规则

默认总预算按百分比分配：

```text
system / policy / stable instructions     15%
recent_history                            20%
conversation_summary + session_context    15%
memory                                    20%
persona + heart                           10%
capability_summary + asset_handles        10%
untrusted_context                         10%
```

### 压缩原则

```text
先压缩 recent_history，再压缩 untrusted_context，再压缩 memory 正文
persona / heart / safety note 不应被完全裁掉
session_context 必须保底保留
```

### 特殊场景调整

#### action / tool 请求

```text
提高 capability_summary、asset_handles、safety_notes 权重
降低闲聊型 memory 正文权重
```

#### 深聊 / 多轮 followup

```text
提高 recent_history、session_context、heart 权重
```

#### knowledge recall

```text
提高 memory / asset_handles / untrusted_context 权重
```

## asset_broker 查询增强要求

当前不再只查知识库。

必须支持以下摘要查询模式：

```text
knowledge retrieval handles
browser or external account read handles
host filesystem readonly handles
terminal readonly capability summary
task-executable capability summary
```

### 查询结果要求

对模型可见字段只允许：

```text
handle_id
asset_type
display summary
allowed_actions
approval_required_actions
freshness / verification summary
```

明确禁止：

```text
api_key
cookie
token
wallet secret
private key
raw credential material
```

## untrusted_context 的来源与标注

允许来源：

```text
browser page extract
MCP tool external output
user uploaded file extraction
external platform content
search result snippets
```

每条 untrusted context 必须包含：

```text
source_type
source_ref
trusted_level
captured_at
trace_ref
redaction_summary
```

### trusted_level 固定值

```text
local_runtime
trusted_internal
untrusted_external_content
user_provided_unverified
tool_generated_unverified
```

## session context 与 presence context 合并规则

### session context

偏运行时：

```text
open loops
pending approval
active commitments
current task status
latest instruction override
```

### presence context

偏对话姿态：

```text
relationship posture
emotional tone
conversation depth
interaction pressure
```

### 合并原则

```text
先用 session context 决定当前是否存在待执行/待确认/待承接事项
再用 presence context 决定话术姿态与解释密度
presence 不能覆盖 session 中的事实状态
```

## 记忆、工作台、渠道上下文优先级

统一优先级：

```text
当前用户显式输入
session_context
recent_history
workbench 当前关联上下文
memory
channel ingress metadata
长期 conversation summary
```

### 具体原则

```text
当前改口优先于旧记忆
当前 pending action 优先于历史总结
当前 workbench artifact 优先于远端知识库泛化记忆
渠道元信息只能辅助解释，不得主导回复内容
```

## ContextGateway 主接口要求

`ContextGateway.build(...)` 返回的 `ContextPacket` 必须满足：

```text
字段分层稳定
所有对模型可见文本都经过 redaction
memory 与 untrusted_context 区分清晰
asset_handles 与 capability_summary 可独立裁剪
能为 direct、tool、task 三类路由提供不同 profile
```

## 验收标准

```text
ContextPacket 分层清晰且字段稳定
resource_handles 不再只限知识库
capability_summary 可用于 action/tool route 判断
untrusted_context 不再是空壳
safety_notes 来自动态风险结果
session context 与 presence context 不再互相覆盖
```

## 最小测试集

```text
普通闲聊：recent_history + persona + heart 生效
多轮改口：session_context 压过旧 summary 与旧 memory
知识检索：knowledge handles 与 memory 同时入包
只读工具请求：capability_summary / asset_handles / safety_notes 进入上下文
外部网页内容：进入 untrusted_context，且带 trusted_level
高敏输入：safety_notes 反映 privacy classification
不同 session 的 recent_history 不串线
```
