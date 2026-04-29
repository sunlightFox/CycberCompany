# 第二十六阶段：高质量 Embedding 与本地优先语义检索增强

## 摘要

第二十六阶段聚焦“记忆和知识召回质量”。当前第二十阶段已经实现本地 `local_hash_v1` 向量、FTS fallback、rerank、suppressed item、retrieval diagnostics 和 provider registry；但 `ExternalEmbeddingProvider` 为 disabled，默认 embedding 仍是 deterministic hash，流程真实但语义表达能力有限。

本阶段目标是在本地优先、隐私优先的前提下，引入高质量 embedding provider，并建立 embedding 质量评测、隐私路由、索引重建和召回质量对比。

本阶段只做后端，不新增 UI。

## 阶段定位

第二十六阶段回答：

```text
默认本地运行是否仍然可用
高质量 embedding 是否能按隐私策略启用
高隐私文本是否不会发往外部 provider
Chroma/local model/external-compatible provider 是否有统一接口
旧 local_hash 索引如何迁移或并存
召回质量提升是否有 eval 证据
```

## 当前基线判断

| 能力 | 当前状态 | 缺口 |
|---|---|---|
| VectorStore | implemented | 默认 local_hash_v1，语义质量有限 |
| EmbeddingProviderResolver | implemented | provider seam 已有 |
| ExternalEmbeddingProvider | disabled | 外部/高质量 provider 未启用 |
| MemoryReranker | implemented | rerank 规则存在，embedding 输入质量有限 |
| KnowledgeReranker | implemented | 同上 |

## 阶段原则

1. 默认仍然本地可运行，不依赖外部服务。
2. privacy high 默认 local_only。
3. 外部 embedding 必须显式配置，并通过 SecretStore/Asset handle 解析。
4. embedding 请求、trace、audit 不记录 secret 明文。
5. provider 切换不能破坏旧索引，可重建、可回滚。
6. local_hash 保留为 fallback，不删除。

## 阶段范围

### 本阶段必须完成

```text
EmbeddingProvider interface 完整化
LocalModelEmbeddingProvider
Chroma provider 接入
ExternalCompatibleEmbeddingProvider
隐私路由策略
索引重建任务
多 provider collection metadata
召回质量 eval
embedding 成本和延迟指标
```

### 本阶段不做

```text
不默认启用云端 embedding
不把敏感文本发送到未授权 provider
不删除 local_hash fallback
不新增前端知识库页面
不做云端同步
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 26.1 | Provider interface 完整化 | embed_text/embed_batch/search/status |
| 26.2 | 本地模型 provider | local_model、模型路径、维度、健康检查 |
| 26.3 | Chroma provider 接入 | collection、upsert、query、metadata |
| 26.4 | External-compatible provider | OpenAI-compatible embedding，隐私路由 |
| 26.5 | 索引重建与回滚 | reindex job、dual write、provider migration |
| 26.6 | 召回质量评测 | recall/precision/supersede/sensitive gate |

## 小阶段 26.1：Provider interface 完整化

### 目标

统一 embedding provider 行为，避免 Memory/Knowledge 直接依赖具体实现。

### Interface

```text
provider_name
embedding_model
embedding_dim
privacy_policy
status
embed_text(text, metadata)
embed_batch(items)
upsert(collection, vectors)
search(collection, query_vector)
delete(target_id)
health_check()
```

### ProviderStatus

```text
available
disabled
degraded
misconfigured
privacy_blocked
```

### 验收

```text
Memory/Knowledge 只依赖 provider interface
provider status 可通过 API 查询
provider metadata 进入 runtime contract
```

## 小阶段 26.2：本地模型 provider

### 目标

支持本地高质量 embedding 模型，保持单机部署能力。

### 配置字段

```text
provider=local_model
model_path
model_name
embedding_dim
device
batch_size
timeout_seconds
max_text_tokens
```

### 验收

```text
无模型文件时 provider=degraded
模型加载失败不影响 local_hash fallback
本地模型向量可写入 vector collections
privacy high 可使用 local_model
```

## 小阶段 26.3：Chroma provider 接入

### 目标

把 Chroma 作为可选本地向量库，而不是只在 contract 中标记 availability。

### 能力

```text
create_collection
upsert
query
delete
collection_metadata
persist_directory
```

### 验收

```text
Chroma 缺失时不影响启动
Chroma 可用时 status=available
Chroma collection 记录 provider/model/dim
FTS fallback 仍可用
```

## 小阶段 26.4：External-compatible provider

### 目标

支持 OpenAI-compatible embedding provider，但严格受隐私路由约束。

### 安全约束

```text
secret_ref 解析必须经过 SecretStore
allow_cloud=false 时不可调用
privacy high 不可调用
敏感命中不可调用
trace 不记录原文
失败 fallback 到 local provider 或 FTS
```

### 验收

```text
未配置 secret_ref -> disabled/misconfigured
privacy high 请求被 privacy_blocked
外部调用 usage/latency 可追踪
raw text 不进入 trace/audit
```

## 小阶段 26.5：索引重建与回滚

### 目标

在 provider 切换时安全重建索引。

### Job 字段

```text
job_id
target_type
collection_name
source_provider
target_provider
status
item_count
completed_count
failed_count
rollback_available
trace_id
```

### 策略

```text
dual_write
shadow_index
validate_before_switch
rollback_to_previous_provider
```

### 验收

```text
重建失败不破坏旧索引
provider 切换有 audit
可查询重建进度
```

## 小阶段 26.6：召回质量评测

### 目标

证明高质量 embedding 带来真实提升，并不会增加敏感泄漏。

### 指标

```text
recall_at_3
precision_at_5
supersede_accuracy
sensitive_suppression_rate
fallback_rate
latency_p95
embedding_cost
```

### 必测 case

```text
同义表达偏好召回
跨语言查询
项目规则召回
旧记忆 supersede
知识库章节语义命中
外部 provider privacy blocked
local provider fallback
```

### 验收命令

```text
.venv\Scripts\python.exe -m pytest apps\local-api\tests\test_phase26_embedding_retrieval_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy .
```

## 阶段总验收标准

第二十六阶段完成时必须满足：

```text
高质量 embedding provider 可配置启用
local_hash 仍作为 fallback
privacy high 不外发
provider 切换可重建、可回滚
召回质量有指标和 eval evidence
runtime contracts 如实标注 provider 状态
```

