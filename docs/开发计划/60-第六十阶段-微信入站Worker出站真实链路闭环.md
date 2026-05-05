# 第六十阶段 - 微信入站、Worker 出站与真实入口链路闭环

## 阶段背景

当前仓库已经有 `ChannelBinding`、`WechatChannelGatewayService`、`BackgroundWorkerService`、`NotificationGatewayService` 和聊天主链路的服务契约，但真实入口链路仍出现 `no_turn` 这类结果，说明“消息进来 - 变成 turn - 被 worker 接住 - 再真正出站”这条链路还没有完全打穿。

这一阶段的重点不是再扩新能力，而是把真实入口做成闭环：入站消息必须稳定进入会话，worker 必须能恢复和继续处理，出站必须有明确证据，失败也必须是可回放、可诊断的失败。

本阶段继续只做后端、schema、migration、repository、service、API、tests、evals 和文档；不新增前端页面、组件、样式、Tauri 窗口或桌面端交互代码。

## 参考结论

### OpenClaw 采用点

OpenClaw 的强项在于入口和控制面稳定：

```text
统一入口网关
长连接或持续会话
消息、状态、执行反馈在同一条链路里闭环
```

本项目采用：

```text
WeChat / 通知 / 其他渠道都要统一进入 channel binding 与 chat turn
入口、执行、出站、回放都要有 trace 和审计
```

### Hermes Agent 采用点

Hermes 更强调连续会话的体验：

```text
消息进入后不会丢上下文
会话能延续
结果能回到同一工作流里
```

本项目采用：

```text
入站事件必须转成可追踪 turn
worker 恢复后能接着跑，不把未完成状态伪装成完成
```

## 核心目标

本阶段完成后，后端应支持：

```text
微信真实入站稳定转成 chat turn
worker 能拉起、恢复、重试和继续处理
出站投递有明确结果和失败原因
真实入口链路有完整 trace、audit、replay 和诊断包
```

## 阶段原则

1. 入站成功不等于链路成功，必须以 turn、worker 和出站都可见为准。
2. `no_turn` 不是业务结果，而是链路缺口。
3. 测试 mock 只能验证契约，不能替代真实入口。
4. worker 失败必须保留状态、证据和恢复点。
5. 出站失败必须可重试、可诊断、可审计。

## 阶段范围

### 本阶段必须完成

```text
微信入站事件标准化与 turn 创建闭环
worker 轮询、恢复、重试和死信处理
出站投递状态机和 delivery 证据
入口链路 trace、audit、replay、diagnostic
```

### 本阶段不做

```text
不新增 UI
不把 mock provider 的成功当成真实成功
不绕过 channel binding、approval、trace 或 audit
不把 worker 失败默认为已处理
```

## 主要待补

```text
微信入站到 turn 的可靠映射
worker 与聊天主链路的恢复接口
出站投递结果收敛
入口级别的诊断与回放
```

## 验收标准

```text
真实微信入站不再出现 no_turn
worker 中断后可恢复并继续处理未完成链路
出站投递有成功、失败、重试和拒绝状态
入口链路的每次动作都有 trace 和 audit
```

## 本轮开发记录

### 已完成

```text
POST /api/channels/inbound/wechat 在 no_pending_action 时会继续进入 WechatGateway route_to_chat
直连微信入站响应返回 turn_id、delivery_binding_id、chat_turns_created、delivery_status 和 diagnostic
直连私聊入站可建立受控 peer session，并保留 peer_ref 哈希和 direct_inbound audit
同步完成的直连 turn 会立即尝试一次出站投递并刷新 delivery binding，避免 pending 证据竞争
微信音频出站支持 send_audio / send_voice 兼容签名、语音气泡和媒体文件降级路径
第六十阶段恢复用例锚定真实 waiting_approval 任务，确认高风险审批不会被自动 recovery retry
```

### 已验证

```text
python -m pytest apps/local-api/tests/test_phase53_channel_bindings.py apps/local-api/tests/test_phase54_wechat_gateway_full_link.py apps/local-api/tests/test_phase60_turn_recovery.py apps/local-api/tests/test_phase62_wechat_chat_main_chain_benchmark.py -q
结果：28 passed
```
