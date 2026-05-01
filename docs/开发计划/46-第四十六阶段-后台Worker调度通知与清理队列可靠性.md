# 第四十六阶段 - 后台 Worker 调度通知与清理队列可靠性

## 阶段背景

第三十六阶段已经有 scheduled task、manual trigger 和 due scanner。第三十九阶段有 checkpoint TTL 字段。第四十阶段有 notification retry 和 failed/degraded 状态。但这些能力多数仍靠 API 或测试主动调用，缺少真正长期运行的后端 worker。

第四十六阶段把后台任务执行从“service 可调用”升级为“本地 API 生命周期内可持续扫描、可停止、可恢复、可诊断”的 worker 体系。

## 核心目标

```text
本地后端启动后可运行后台 due scanner
scheduled task 到期后自动创建普通 Task Engine task
通知失败可按策略重试，不无限重试
checkpoint 过期后可清理或标记 expired
stale task、memory job、scheduled run、notification attempt 有统一恢复策略
所有后台 worker 有 trace、audit、健康状态和 diagnostic
```

## 阶段原则

1. Worker 只触发系统服务，不直接执行工具。
2. 每次后台触发仍走 Task Engine、Safety、Approval、Trace。
3. 无人值守高风险继续 fail-closed。
4. Worker 可配置启停，测试环境可禁用或手动 tick。
5. 本地单机优先，不引入大型队列框架。
6. 任务必须幂等，重复 tick 不产生重复 run。
7. 不新增 UI。

## 本阶段必须完成

```text
新增 WorkerSupervisor 或 BackgroundWorkerService
lifespan 中启动和关闭 worker loop
scheduled_due_worker：定期 scan_due
notification_retry_worker：重试 queued/failed 可重试通知
checkpoint_cleanup_worker：过期 checkpoint 清理或标记 expired
stale_recovery_worker：恢复 stale task_jobs、memory_jobs、scheduled_task_runs
worker heartbeat / health API / diagnostic
测试环境提供 manual_tick，避免慢测试和不稳定 sleep
```

## 本阶段不做

```text
不引入 Redis、Celery、Kafka 等外部依赖
不做云端多设备调度
不让后台 worker 自动审批高风险动作
不自动删除用户原始文件或任务最终工件
不新增前端任务面板
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 46.1 | WorkerSupervisor 契约 | worker registry、heartbeat、manual_tick |
| 46.2 | scheduled due worker | active scheduled task 自动触发 |
| 46.3 | notification retry worker | queued/failed/retry_count/max_retries |
| 46.4 | checkpoint cleanup worker | expired 标记、快照 artifact 安全清理 |
| 46.5 | stale recovery worker | task/memory/scheduled run 统一恢复 |
| 46.6 | health 与 diagnostic | worker 状态、最近错误、trace 链路 |
| 46.7 | 测试与 release gate | 幂等、关闭、错误恢复、无泄漏 |

## 验收标准

```text
启动 API 后 worker 可按配置运行
manual_tick 可触发 due scheduled task 且幂等
通知 provider 失败后按 max_retries 重试并最终 degraded/failed
checkpoint expires_at 到期后被标记或清理，不影响未过期 checkpoint
worker 异常不会杀死 API，可在 diagnostic 中看到失败原因
所有 worker 动作有 trace/audit，payload 已脱敏
不新增任何前端 UI 或桌面交互代码
```

## 文件影响范围

| 模块 | 文件范围 |
|---|---|
| Worker | `apps/local-api/app/workers/` 或 `apps/local-api/app/services/workers.py` |
| Lifespan | `apps/local-api/app/core/lifespan.py` |
| Scheduled | `scheduled_tasks.py` |
| Notification | `notifications.py` |
| Checkpoint | `checkpoints.py` |
| Diagnostic | `release.py`、`routes_system.py` |
| Tests | `apps/local-api/tests/test_phase46_background_workers.py` |

## 与后续阶段关系

第四十七阶段的真实浏览器执行、第四十八阶段的治理清理和第四十九阶段的长期质量回归，都依赖后台 worker 能稳定运行。

