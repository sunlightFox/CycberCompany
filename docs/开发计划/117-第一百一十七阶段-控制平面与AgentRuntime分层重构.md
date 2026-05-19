# 第一百一十七阶段 - 控制平面与 AgentRuntime 分层重构

## 阶段定位

phase86 到 phase112 已经连续完成了 ChatRuntime 宿主瘦身、主链路唯一化、动作状态与可见完成态统一、扩展运行时闭环补齐。当前系统的主要问题已经不再是“有没有主链路”，而是：

```text
chat_service 仍然挂载过多依赖与兼容逻辑
service_registry 仍然按功能堆叠，而不是按运行平面收口
persona / tone / response_quality 已独立成形，但仍集中在超大 service 内
tool / browser / shell / mcp / channel 的能力执行面仍缺少统一 runtime contract
```

这说明当前系统已经进入“架构成熟化”阶段：需要把控制平面、会话平面、AgentRuntime、能力执行平面、策略与数据平面彻底分层，避免后续继续在 `chat.py`、`registry.py`、`design_alignment.py` 里回流成长。

这一阶段不追求新增用户侧功能，而是把现有聊天主链、工具执行、Persona/Heart、记忆与质量门禁整理成长期可演化的最终态运行架构。

## 目标

```text
把当前单体宿主式聊天运行结构收敛成 control plane / session plane / agent runtime / capability plane / policy-data plane 五层架构
让 ChatService 彻底退化为 compat facade，不再承接新增业务编排
让 turn 执行链只存在一个 owner，并通过统一 TurnExecutionPlan 驱动
让 persona / tone / safety / response quality 形成独立 policy runtime，而不再继续堆入 design_alignment 超大文件
让 browser / shell / tool / mcp / skill / external platform 共享统一的执行契约、审批契约和证据契约
```

## 重点

### 117.1 控制平面与会话平面收口

```text
明确 API routes、channel ingress、approval orchestration、background worker、startup recovery 归属 control plane
明确 session_id / conversation_id / turn_id / retry / replay / cancel / queue / stream 归属 session plane
SessionRuntime 保持 proxy-only，不再回流业务逻辑
为 runtime-topology 增加平面级视图，避免只看到 service 名称，看不到 ownership
```

### 117.2 AgentRuntime 唯一执行 owner

```text
把 turn_bootstrap / turn_analysis / context_build / policy_resolution / route_dispatch / model_execution / finalize 串成唯一 AgentRuntime 执行链
引入统一 TurnExecutionPlan，承载 route、context policy、persona policy、capability intent、completion semantics
ChatTurnExecutionOrchestrator、ChatModelExecutionService、ChatTurnFinalizeService 围绕统一计划对象协作，而不是继续通过 facade 零散传值
direct route、readonly route、model route、tool route 都要回到同一执行骨架上
```

### 117.3 Policy Runtime 物理拆分

```text
从 design_alignment.py 中优先拆出 persona_runtime、tone_policy_runtime、response_quality_runtime、heart_runtime
保证 persona_summary -> tone_resolution -> response_quality 这条链保留现有能力，但不再增长成新的单体核心
高风险场景下的边界话术、去拟人化、完成态可见语义继续通过 policy runtime 注入，而不是散落在 chat host helper 中
```

### 117.4 Capability Plane 统一执行契约

```text
统一 browser / shell / tool / mcp / skill / external platform 的 plan / authorize / execute / summarize / emit_evidence 语义
把 capability runtime 与 visible response、approval、artifact evidence、delivery semantics 对齐
防止自然语言 surface 与能力执行细节继续耦合，尤其避免在 natural_chat、chat_runtime_host_helpers 中继续堆 provider 特例
```

### 117.5 Registry 分域与可观测性升级

```text
从单一 ServiceRegistry 逐步拆成 control_plane_registry、runtime_registry、capability_registry、policy_registry
让 create_app、lifespan、build_registry 的依赖图能直接反映最终架构，而不是维护历史功能堆栈
在 runtime-topology、readiness、diagnostic、trace span 中补充 plane、owner、delegates_to、growth_gate 元数据
让后续任何新增能力都能回答：它属于哪个平面、谁是 owner、通过什么契约接入
```

## 直接依赖

```text
docs/开发计划/86-第八十六阶段-ChatRuntime兼容壳瘦身与主链路唯一化.md
docs/开发计划/91-第九十一阶段-ChatRuntime物理拆分与宿主瘦身收尾.md
docs/开发计划/96-第九十六阶段-AgentLoop主链路加厚与观察重规划闭环.md
docs/开发计划/108-第一百零八阶段-ChatRuntime宿主瘦身与职责拆分封口.md
docs/开发计划/112-第一百一十二阶段-扩展运行时同步与执行闭环补齐.md
apps/local-api/app/main.py
apps/local-api/app/core/lifespan.py
apps/local-api/app/services/registry.py
apps/local-api/app/services/session_runtime.py
apps/local-api/app/services/chat.py
apps/local-api/app/services/chat_turn_execution.py
apps/local-api/app/services/chat_model_execution.py
apps/local-api/app/services/chat_turn_finalize.py
apps/local-api/app/services/design_alignment.py
apps/local-api/app/api/routes_system.py
apps/local-api/tests/test_phase86_*
apps/local-api/tests/test_phase91_*
apps/local-api/tests/test_phase112_*
```

## 借鉴约束

```text
借鉴 OpenClaw 的 control plane / runtime plane / gateway 边界
借鉴 Hermes 的 prompt assembly、session storage、context discipline
不复制 Hermes 的单体 AIAgent loop 结构
不引入未来一定要推翻的兼容过渡层
所有新分层都必须能映射回现有 runtime-topology 和 readiness 门禁
```

## 验收

```text
ChatService 在 runtime-topology 中仍为 compat_shell，且无新增业务 owner
SessionRuntime 继续保持 proxy-only，所有 turn 业务执行 owner 明确落在 AgentRuntime
TurnExecutionPlan 成为主链路统一中间产物，direct / readonly / tool / model / channel follow-up 全部共享
design_alignment.py 的 persona / tone / quality 核心逻辑完成首轮物理拆分，并保持现有对外契约不破
至少一组 browser、shell、tool、mcp、skill 的执行路径接入统一 capability contract
runtime-topology、readiness、diagnostic 可以直接展示 plane、owner、delegates_to、growth_gate
新增架构后不降低 phase84、phase86、phase111、phase112 相关测试通过率
```
