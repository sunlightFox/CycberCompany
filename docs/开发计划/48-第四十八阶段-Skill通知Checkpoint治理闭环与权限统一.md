# 第四十八阶段 - Skill 通知 Checkpoint 治理闭环与权限统一

## 阶段背景

第三十八阶段实现了 Skill 安全治理，第三十九阶段实现了 checkpoint/rollback，第四十阶段实现了通知网关。这些能力都建立了后端契约，但它们之间仍需要统一：Skill 写文件时如何强制 checkpoint，后台任务使用 Skill 时如何验证 unattended，外部通知释放审批后如何恢复任务，Capability Graph 如何成为唯一权限判断入口。

第四十八阶段把 Skill、Notification、Checkpoint、Capability、Approval 和 Task 的边界收束为一条治理闭环。

## 核心目标

```text
Skill 执行前统一走 Capability Graph + Safety + Tool Runtime
Skill 写文件、覆盖、删除时自动要求 checkpoint 或说明不可回滚
unattended scheduled task 使用 Skill 时检查 Skill eval、grant、risk 和 unattended policy
Notification inbound 只能释放绑定 pending action，并能恢复对应 task run
Capability Graph 成为 Skill/Notification/External Platform 的共同权限入口
Release gate 能发现治理链路缺口
```

## 阶段原则

1. Skill 负责做事方法，不负责资源查询和密钥读取。
2. Notification 是系统网关，不是普通工具随手发消息。
3. Checkpoint 不替代审批，只增强可恢复性。
4. Capability Graph 是权限事实源，避免多个 service 各自判断。
5. 高风险动作必须 Safety + Approval。
6. 所有出入站外部内容默认 untrusted。
7. 不新增 UI。

## 本阶段必须完成

```text
Skill runtime preflight 统一调用 capability service
Skill grant 与 asset grants / capability edges 建立映射或同步策略
Skill step checkpoint requirement：file.write/delete/move/media.render_edit
Scheduled task 使用 Skill 前验证 unattended_allowed、eval binding、trust_level
Notification inbound approval resolver 与 TaskEngine resume 形成闭环
Checkpoint rollback result 可触发 notification summary
Release gate 增加 governance chain matrix
Diagnostic 输出 skill/notification/checkpoint/capability 交叉风险
```

## 本阶段不做

```text
不做公开 Skill 市场
不引入复杂企业 RBAC 控制台
不允许通知消息创建并自动执行高风险任务
不自动回滚外部副作用
不新增前端页面
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 48.1 | 权限事实源梳理 | Skill grant、asset grant、capability edge 关系图 |
| 48.2 | Skill preflight 统一 | 执行前 capability/safety/checkpoint/eval |
| 48.3 | Scheduled + Skill 策略 | unattended、trust、eval、risk gate |
| 48.4 | Notification pending 闭环 | inbound -> approval -> task resume -> notification |
| 48.5 | Checkpoint 强制策略 | 可恢复动作自动 checkpoint，不可恢复动作声明 |
| 48.6 | Governance release gate | 交叉矩阵、diagnostic、leakage scan |
| 48.7 | 回归测试 | Skill/通知/checkpoint/权限组合测试 |

## 验收标准

```text
未授权 Skill 不能执行工具或资产动作
高风险未评测 Skill 不能进入 unattended scheduled task
Skill 文件 mutation 前有 checkpoint 或明确不可回滚原因
外部通知“确认”只能释放唯一匹配 pending action
审批通过后对应 task 能恢复或明确进入 waiting/runnable 状态
rollback 完成后可生成通知摘要
governance release gate 能捕获越权、缺 eval、缺 checkpoint、泄漏风险
```

## 文件影响范围

| 模块 | 文件范围 |
|---|---|
| Skill | `skill_governance.py`、`skill_plugin.py` |
| Capability | `capability.py`、`asset_broker.py` |
| Notification | `notifications.py` |
| Checkpoint | `checkpoints.py`、`tools.py`、`media.py` |
| Task | `tasks.py`、`scheduled_tasks.py` |
| Release | `release.py` |
| Tests | `apps/local-api/tests/test_phase48_governance_closure.py` |

## 与后续阶段关系

第四十九阶段将做真实模型和真实数据质量封版。治理闭环不稳时，真实模型很容易通过自然语言绕出系统边界，因此第四十八阶段是质量封版前的安全底座。

