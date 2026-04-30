# 第三十九阶段 - 任务 Checkpoint 回滚与工作区快照

## 阶段背景

当前系统已经有 task、task_steps、tool_calls、artifacts、trace、approval、backup/restore 和 replay。它能记录“做了什么”，但对复杂本地执行来说，仅记录还不够。用户会希望 agent 敢于整理文件、生成代码、批量转换、剪辑媒体、下载资料，同时在出错时能恢复。

对标 OpenClaw / Hermes Agent 的真实使用场景，checkpoint/rollback 是高信任执行的基础：

```text
执行前先保存状态
失败后能回滚
审批时能看到会影响哪些文件
任务回放能解释哪个 checkpoint 对应哪一步
```

第三十九阶段要把 checkpoint 从“备份恢复的一次性系统能力”细化为“任务步骤级工作区快照能力”。它不替代 Safety 和 Approval，而是在用户确认后为可恢复动作提供额外保护。

本阶段只做后端、schema、migration、repository、service、API、tests、evals 和文档，不新增 UI、Tauri、React、样式或桌面交互。

## 核心目标

本阶段完成后，后端应支持：

```text
任务或步骤执行前创建 checkpoint
checkpoint 记录受影响工件、文件 hash、metadata 和策略快照
文件写入、覆盖、移动、删除、媒体渲染、Skill 运行可声明 checkpoint requirement
失败、用户取消或审批拒绝后可按策略 rollback
rollback 本身有 task_event、trace、audit 和 replay
用户可手动请求恢复某个 checkpoint
不可回滚动作必须在审批中明确说明
```

## 阶段原则

1. Checkpoint 是安全增强，不是高风险免审批理由。
2. 只对受控工作区、任务 artifact 和授权 workspace 生效。
3. 任意本地路径、系统目录、secret 目录不纳入 checkpoint 来绕过路径策略。
4. Checkpoint 必须记录 checksum 和 policy snapshot。
5. Rollback 必须幂等，重复调用不能破坏状态。
6. 外部副作用不可回滚，例如发帖、付款、发送邮件、远程删除；只能记录 compensating action 建议。
7. 删除和覆盖必须先 checkpoint 或说明不可回滚。
8. 大文件 checkpoint 要有配额、压缩、TTL 和清理策略。
9. Checkpoint 内容不得泄露 secret 明文。
10. 不新增 UI；API 和 replay 为未来 UI 提供契约。

## 对标结论

### OpenClaw 采用点

```text
用户期待 agent 能处理真实文件和任务，但担心误删误改
多步骤执行需要中间状态可恢复
artifact 和任务证据必须能证明输出来自哪里
```

对我们的启发：

```text
把任务工件和 workspace 的可恢复性纳入计划
执行计划中标明哪些步骤需要 checkpoint
```

### Hermes Agent 采用点

```text
危险动作需要明确审批和阻断
容器/沙箱可以降低部分风险，但本机动作仍需保守
```

对我们的启发：

```text
checkpoint 不降低 hardline 阻断
rollback capability 要进入风险解释，而不是替代审批
```

## 当前基线判断

| 能力 | 当前状态 | 第三十九阶段目标 |
|---|---|---|
| Artifact | 有 checksum 和任务目录 | 支持 checkpoint copy/hash/diff |
| File tools | 受控读写、移动、删除、hash | 写/删/覆盖前可创建 checkpoint |
| Task replay | 能回放 steps/tools/artifacts | 加入 checkpoint/rollback timeline |
| Backup/restore | 系统级备份恢复 | 增加任务步骤级快照 |
| Safety/Approval | 高风险审批 | 审批摘要包含 rollback availability |

## 阶段范围

### 本阶段必须完成

```text
task_checkpoints / checkpoint_items / rollback_events schema 与 migration
CheckpointService
受控 workspace snapshot 策略
file.write/move/delete/copy checkpoint 集成
media.render_edit checkpoint 集成预留
Skill run checkpoint requirement 集成预留
approval 摘要展示 rollback availability 字段
rollback API
任务 replay 增加 checkpoint/rollback
checkpoint 配额、TTL、cleanup
测试和 eval
```

### 本阶段不做

```text
不做全盘快照
不做系统级还原点
不做任意目录递归备份
不对外部服务动作声称可回滚
不对数据库 migration 做自动 rollback
不新增 UI 差异查看器
不保存明文 secret 文件内容作为 checkpoint
```

## 核心契约草案

### TaskCheckpoint

```json
{
  "checkpoint_id": "chk_001",
  "organization_id": "org_default",
  "task_id": "tsk_001",
  "step_id": "step_001",
  "tool_call_id": "call_001",
  "checkpoint_type": "pre_mutation",
  "scope": "task_artifacts",
  "status": "ready",
  "item_count": 2,
  "size_bytes": 4096,
  "policy_snapshot": {
    "allowed_roots": ["artifact://tsk_001/outputs/**"],
    "denied_roots": ["**/.env"],
    "rollback_mode": "copy_restore"
  },
  "created_at": "2026-05-01T10:00:00+08:00"
}
```

### CheckpointItem

```json
{
  "checkpoint_item_id": "chki_001",
  "checkpoint_id": "chk_001",
  "target_uri": "artifact://tsk_001/outputs/report.md",
  "item_type": "file",
  "before_checksum": "sha256:...",
  "before_size_bytes": 1200,
  "snapshot_artifact_id": "art_checkpoint_report",
  "exists_before": true,
  "sensitivity": "low"
}
```

### RollbackEvent

```json
{
  "rollback_id": "rb_001",
  "checkpoint_id": "chk_001",
  "task_id": "tsk_001",
  "requested_by": "user_local_owner",
  "status": "completed",
  "restored_items": 2,
  "skipped_items": 0,
  "trace_id": "trc_001"
}
```

## API 契约建议

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/tasks/{task_id}/checkpoints` | 手动创建 checkpoint |
| GET | `/api/tasks/{task_id}/checkpoints` | checkpoint 列表 |
| GET | `/api/checkpoints/{checkpoint_id}` | checkpoint 详情 |
| POST | `/api/checkpoints/{checkpoint_id}/rollback` | 回滚到 checkpoint |
| GET | `/api/checkpoints/{checkpoint_id}/items` | 快照项 |
| GET | `/api/tasks/{task_id}/rollback-events` | 回滚历史 |

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 39.1 | schema、migration 与 repository | checkpoints/items/rollback_events |
| 39.2 | CheckpointService | 创建、读取、清理、配额 |
| 39.3 | File tool 集成 | 写/删/覆盖/移动前自动 checkpoint |
| 39.4 | Approval 摘要集成 | rollback availability、不可回滚提示 |
| 39.5 | RollbackService | copy_restore、missing/new file 处理、幂等 |
| 39.6 | Replay 与 diagnostic | checkpoint timeline、rollback event、泄漏扫描 |
| 39.7 | 媒体和 Skill 扩展钩子 | render_edit / skill_run checkpoint requirement |
| 39.8 | tests、evals 与 cleanup | 配额、TTL、失败恢复 |

## 关键实现要求

### checkpoint 创建

```text
输入必须是受控 URI 或 task artifact 相对路径
拒绝敏感路径、系统路径、任意绝对路径
每个 item 记录 exists_before、checksum、size、content_type、sensitivity
小文件可复制为 snapshot artifact
大文件优先 hash + metadata，必要时要求用户确认 checkpoint 大小
```

### file tool 集成

```text
file.write overwrite=true 前创建 pre_mutation checkpoint
file.delete 前创建 checkpoint，记录被删除文件内容或 hash
file.move 前记录 source 和 destination
file.copy 通常不需要 rollback，但可记录 destination exists_before
失败时不自动 rollback，除非策略为 rollback_on_failure 且动作可恢复
```

### rollback

```text
rollback 前检查当前文件是否仍在允许范围内
如果当前文件已被其他任务修改，进入 conflict，不强行覆盖
恢复缺失文件、还原覆盖文件、删除 checkpoint 时不存在但后来新增的文件
所有 restored/skipped/conflict 写 rollback_items
rollback 本身写 audit 和 trace
```

### 不可回滚动作

```text
外部发布、发送消息、付款、远程删除、浏览器提交、MCP 外部写入不可回滚
审批摘要必须写明“这个动作无法由本地 checkpoint 撤销”
可提供补偿建议，但不能声称自动恢复
```

## 必测用例

```text
file.write overwrite 前自动 checkpoint
file.delete 拒绝审批后不创建删除结果
file.delete 审批通过后可 rollback 恢复文件
file.move rollback 恢复 source/destination
checkpoint 拒绝路径逃逸和敏感路径
rollback 冲突时不强行覆盖
大文件超过配额时返回可解释错误
外部发布类动作显示不可回滚
task replay 包含 checkpoint 和 rollback timeline
diagnostic 不包含 secret 明文
```

## 文件影响范围

| 模块 | 文件范围 |
|---|---|
| Schema | `apps/local-api/app/schemas/checkpoints.py`、`packages/core-types/core_types/checkpoint.py` |
| Migration | `apps/local-api/app/db/migrations/028_task_checkpoints.sql` |
| Repository | `apps/local-api/app/db/repositories/checkpoint_repo.py` |
| Services | `apps/local-api/app/services/checkpoints.py`、`tools.py`、`tasks.py`、`approvals.py` |
| API | `apps/local-api/app/api/routes_checkpoints.py`、`main.py` |
| Tests | `apps/local-api/tests/test_phase39_checkpoints.py` |

## 验收标准

```text
任务可创建 checkpoint，file mutation 可自动创建 checkpoint
rollback 可恢复受控任务工件内的可恢复变更
不可回滚动作在审批和 replay 中明确标记
checkpoint/rollback 有 trace、audit、task_event、replay 证据
路径逃逸、敏感路径、大文件超配额被安全处理
release report 增加 phase39 摘要
不新增任何前端 UI 或桌面交互代码
```

## 与其他阶段关系

```text
第三十六阶段长期任务可配置 mutation 前必须 checkpoint
第三十七阶段浏览器下载进入 artifact quarantine 后可被 checkpoint 管理
第三十八阶段 Skill 写文件必须声明 checkpoint requirement
第三十五阶段媒体 render_edit 可在覆盖/导出前复用 checkpoint
```

