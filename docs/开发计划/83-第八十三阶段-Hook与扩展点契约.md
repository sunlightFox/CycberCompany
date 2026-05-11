# 第八十三阶段 - Hook 与扩展点契约

## 阶段定位

本阶段目标是给聊天主链路预留稳定、可追踪、不可越权的扩展点，让 Skill、MCP、插件、治理与评测层能接入，而不再继续把逻辑塞进 `chat.py` 或渠道入口。

## 直接依赖

```text
docs/14-Skill_MCP_Tool插件详细设计.md
docs/07-安全权限审计与评测.md
docs/开发计划/77-第七十七阶段-聊天运行时收口与主链路统一.md
docs/开发计划/79-第七十九阶段-ContextGateway能力化增强.md
docs/开发计划/80-第八十阶段-聊天内工具调用闭环.md
docs/开发计划/82-第八十二阶段-记忆写入与运行账本统一.md
```

## 设计原则

```text
hook 只能扩展，不得绕过核心边界
hook 结果必须可 trace、可审计
hook 不得直接暴露 secret
hook 不得直接写最终用户回复文本，除非契约明确允许
hook 要支持 fail closed
```

## 标准 hook 面

### 1. before_ingress

时机：

```text
渠道消息标准化完成后
进入 SessionRuntime / ChatRuntime 前
```

允许：

```text
补充 ingress tags
补充 dedupe hints
标记 trusted_level
补充审计元信息
```

禁止：

```text
直接创建 turn
直接绑定 approval
直接调用工具
```

### 2. after_context_build

时机：

```text
ContextPacket 已构建完成
进入 route / model 决策前
```

允许：

```text
附加只读 diagnostics
附加 prompt advisory metadata
附加 context visibility annotations
```

禁止：

```text
直接注入 secret
删除系统安全说明
覆盖 capability 真实结果
```

### 3. before_route_select

时机：

```text
上下文已就绪
route 决策前
```

允许：

```text
提供 advisory route hint
提供 should_clarify hint
提供 confidence adjustment
```

禁止：

```text
直接替代 Safety 结果
直接发起工具调用
```

### 4. before_model_call

时机：

```text
模型 prompt 组装完成
真正调用模型前
```

允许：

```text
附加 prepend context
附加 prompt diagnostics
附加 model selection advisory
```

禁止：

```text
注入明文 secret
移除固定安全规则
直接改写 conversation 归属
```

### 5. before_tool_call

时机：

```text
tool request 已生成
Safety / Approval 执行前或执行中
```

允许：

```text
补充参数校验
阻断非法调用
补充 redaction policy
```

允许返回：

```text
allow
block
rewrite_safe_params
```

明确要求：

```text
任何 block 都必须写 trace 与 reason_code
任何 rewrite 都必须可审计
```

### 6. after_tool_call

时机：

```text
ToolRuntime 返回结果后
结果回注上下文前
```

允许：

```text
裁剪结果
附加 evidence summary
标注 trusted_level
产生 artifact refs
```

禁止：

```text
伪造执行成功
补写不存在的结果字段
```

### 7. before_finalize

时机：

```text
ResponsePlan 已生成
最终可见输出前
```

允许：

```text
应用可见性治理
附加 channel render hint
做最后 redaction check
```

禁止：

```text
直接注入 trace_id
绕过 visible filter
把 pending 说成 completed
```

### 8. before_memory_write

时机：

```text
memory candidate 已生成
真正写入长期记忆前
```

允许：

```text
拒绝不合规写入
补充 source annotations
标记应归档或 supersede
```

禁止：

```text
删除 source
把短期执行态伪装成长期记忆
```

## hook 输入契约

统一输入至少包含：

```text
trace_id
conversation_id
turn_id
member_id
session_id
channel
hook_stage
payload
```

其中 `payload` 根据阶段不同可包含：

```text
ingress metadata
context packet
route decision draft
prompt metadata
tool request
tool result summary
response plan
memory candidate
```

## hook 输出契约

统一输出允许字段：

```text
status
reason_code
advisory_payload
blocked
rewritten_payload
trace_annotations
audit_annotations
```

### status 固定值

```text
pass
advisory
rewritten
blocked
failed
```

## 可中断行为

只有以下 hook 可以中断主链路：

```text
before_tool_call
before_memory_write
before_finalize   仅可因 redaction / safety block 中断
```

其他 hook 默认只能 advisory，不得终止主链路。

## trace 要求

每次 hook 触发必须有 trace 记录：

```text
hook_name
hook_stage
input_summary
output_summary
status
reason_code
duration_ms
```

如果 hook 阻断了主链路，还必须有：

```text
blocked_target
block_reason
fallback_behavior
```

## 核心越权禁令

hook 明确不能绕过：

```text
Asset Broker
Capability Graph
Safety
Approval
Response visible filter
Memory source contract
```

具体表现为：

```text
hook 不能直接取 secret
hook 不能自己决定高风险动作免审批
hook 不能直接调用 shell / browser / external account 而不经 ToolRuntime
hook 不能直接写用户可见完成态
```

## 验收标准

```text
hook 面稳定且阶段清晰
before_tool_call / after_tool_call / before_memory_write 可支撑治理层接入
所有 hook 都可 trace、可审计
任何 hook 都不能绕过核心安全边界
```

## 最小测试集

```text
before_tool_call 可阻断非法高风险调用
after_tool_call 可裁剪工具结果并保留 trace
before_finalize 可阻止 trace_id 泄漏到 visible text
before_memory_write 可拒绝无 source 的记忆写入
hook failed 不会导致 secret 泄漏或无痕越权
```
