# 第六十六阶段 - Continuation 质量闸门与二次修订重构

## 阶段背景

`chat_continuation.py` 当前承担微信聊天续跑与二次修订，但实现方式以关键词和硬编码规则为主，规则增长后可维护性下降。它能拦截内部字段、emoji、假完成、多模态空泛回复等问题，但缺少统一质量诊断模型，也没有与 v4 prompt / ResponseComposer 契约完全对齐。

本阶段目标是破坏性重写 continuation：从“关键词触发修补”升级为“质量诊断 -> 修订策略 -> 最终验收 -> 诊断 payload”的小型质量闸门。

## 核心目标

完成后，Continuation 应满足：

```text
只在需要时启用，不拖慢普通闲聊
质量诊断维度清晰可测
二次修订 prompt 使用 v4 表达规范
多模态必须回应具体内容
不泄露内部字段和工具细节
不把未执行动作说成完成
微信渠道最终只投递一次高质量可见文本
```

## 破坏性调整

### 版本和命名

`ContinuationDecision`、`ContinuationEvaluation` 可保留类名，但内部字段允许重构。新 payload 建议为：

```json
{
  "enabled": true,
  "trigger_profile": "wechat_quality_gate",
  "reason_codes": [],
  "diagnostics": {
    "structure": "ok",
    "voice": "weak",
    "safety": "ok",
    "evidence": "missing",
    "multimodal": "ok"
  },
  "quality_verdict": "good|revise|block",
  "quality_tags": [],
  "iterations": 1,
  "used_revision": true,
  "latency": {}
}
```

具体字段可按现有测试兼容一轮，但新语义以 v4 为准。

### 删除旧续跑 prompt

`render_continuation_revision_prompt` 必须替换为 v4 修订 prompt：

```text
只重写上一条助手回复
先回应当前用户意图
保留事实边界和安全边界
不新增工具结果
不声称已执行未执行动作
多模态说具体识别点
严格格式保持纯净
删除内部字段和机械开头
```

## 实现要求

### 1. 启用策略重构

只在以下情况启用：

```text
ui_mode=wechat_chat
mode=direct 或 direct_with_memory
非严格格式请求
非固定边界模板
复杂度高
长输出
deep_reasoning
多模态上下文
工作台/上下文继续
高价值办公或线上用户主题
```

以下情况禁用：

```text
严格 JSON / 表格 / 代码块
已由 ChatQualityPolicy 固定回复
任务执行中间态已由 Task/Approval 模板接管
普通短闲聊且无多模态
```

### 2. 质量诊断维度

重写 `evaluate()`，从单纯 tag 堆叠改为诊断维度：

```text
content：是否回答用户问题，是否过短，是否空泛
structure：长回复是否可读，是否保留严格格式
voice：是否像微信人话，是否机械模板，是否过硬
safety：是否泄露内部字段，是否包含 secret，是否越权
evidence：是否假装已执行，是否缺少证据却说完成
multimodal：是否只说收到，是否回应识别内容
latency：是否超过预算
```

输出 tag 仍可保留，便于测试和诊断：

```text
missing_reply
too_short
weak_structure
robotic_template
internal_jargon
false_done
hard_boundary_tone
multimodal_generic_reply
strict_format_polluted
latency_slow
```

### 3. 修订调用策略

```text
最多 1 次二次模型调用
修订请求 temperature <= 0.25
timeout <= 20 秒
修订失败时回退初稿，但继续过滤可见文本
修订结果若仍包含 internal_jargon / false_done / strict_format_polluted，则拒收
```

### 4. 最终质量落盘

`response_plan.structured_payload.continuation` 必须写入：

```text
enabled
reason_codes
quality_verdict
quality_tags
diagnostics
iterations
used_revision
initial_latency_ms
revision_latency_ms
total_latency_ms
budget_exhausted
```

`response_plan.quality_markers` 必须写入：

```text
continuation_quality_verdict
continuation_quality_tags
```

## 验收标准

```text
普通微信短闲聊不启用 continuation
复杂微信长回复启用且只投递最终修订文本
多模态只说“收到图片/语音/文件”会触发修订
包含内部字段、假完成、圆脸 emoji 会触发修订或拒收
严格 JSON 不启用，不被符号污染
续跑失败不导致 turn 失败，除非用户取消
```

## 测试计划

```text
apps/local-api/tests/test_phase64_chat_continuation.py
apps/local-api/tests/test_phase62_wechat_chat_main_chain_benchmark.py
apps/local-api/tests/test_xiaowu_wechat_multimodal.py
apps/local-api/tests/test_phase65_wechat_multiturn_regression.py
```

新增 case：

```text
微信复杂办公方案低质量草稿触发修订
语音转写只回复“收到语音”触发修订
严格 JSON 请求禁用 continuation
边界类固定回复禁用 continuation
修订结果仍含 internal_jargon 时拒收
```

