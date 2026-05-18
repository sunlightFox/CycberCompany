# 第一百一十阶段 - 渠道路由稳定性与 NoTurn 根因收敛

## 阶段定位

phase109 已经把真实长稳运行中的阻塞点说得很清楚了：

```text
real_world_evidence_p0_gaps_present
channel_long_run_no_turn_present
routing_path_not_stable
```

这说明问题已经不是“有没有渠道运行时”，而是“真实消息进来之后，是否稳定落到正确的 session / conversation / turn 主链路”。

这一阶段专门收敛渠道侧 `NoTurn` 与 routing path 的结构性问题，把偶发故障、配置问题和架构级不稳定分开处理。

## 目标

```text
把真实渠道中的 NoTurn 问题拆成可定位根因
稳定 channel -> session -> conversation -> turn 的主链路
补齐 routing 证据、诊断和回放
让 phase109 中 routing_path_not_stable 从 blocking 变为已收敛问题
```

## 重点

### 110.1 NoTurn 根因分类收口

```text
统一 no_turn reason taxonomy
区分 turn_not_created、turn_created_but_not_queued、channel_ingress_submit_failed、pairing_rejected_or_missing 等类别
让 evidence、runtime diagnostics、release summary 使用同一套原因口径
```

### 110.2 路由链路一致性

```text
收紧 channel_session_router、channel_gateway_router、channel_session_semantics 之间的职责边界
避免同一入站消息在不同分支上产生不同 conversation 归属
避免 routing fallback 偷偷吞掉 turn 创建失败
```

### 110.3 渠道证据与运行回放

```text
给关键 routing decision 增加稳定 evidence
让 trace、audit、channel diagnostics、readiness 之间能互相映射
补齐 NoTurn 事件的最小可回放信息
```

## 直接依赖

```text
docs/开发计划/88-第八十八阶段-渠道可靠性与NoTurn治理闭环.md
docs/开发计划/109-第一百零九阶段-真实场景长稳运行与成熟度复核.md
apps/local-api/app/services/channel_ingress_runtime.py
apps/local-api/app/services/channel_session_router.py
apps/local-api/app/services/channel_gateway_router.py
apps/local-api/app/services/channel_session_semantics.py
apps/local-api/app/services/wechat_gateway.py
apps/local-api/app/services/feishu_gateway.py
apps/local-api/app/services/chat_mainline_readiness.py
docs/测试/聊天主链路/2026-05-03-wechat-50-scenarios/evidence-smoke/*
docs/测试/聊天主链路/2026-05-03-wechat-real-scenarios/evidence-smoke/*
```

## 验收

```text
NoTurn 根因口径统一
routing_path_not_stable 不再出现在 phase109 的 likely_primary_causes 首位
真实渠道 smoke evidence 中 no_turn_count 明显下降或归零
release / readiness / diagnostics 能给出一致的 routing 诊断结果
```
