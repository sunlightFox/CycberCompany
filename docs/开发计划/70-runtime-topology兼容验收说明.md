# Phase70 Runtime Topology 兼容验收说明

本仓库当前存在一个历史阶段编号漂移：

- 正式阶段文档 `70-第七十阶段-SessionContext与上下文可见性治理重构.md` 讨论的是 `SessionContext / 上下文可见性`
- 代码与测试中的 `phase70` 则实际承载了 `runtime topology / 入口统一` 的兼容验收语义

为避免继续混淆，当前约定如下：

1. `apps/local-api/tests/test_phase70_runtime_topology.py` 保留现有文件名，不做大面积历史重命名
2. 该测试文件在语义上视为 `Phase70-runtime-topology compatibility acceptance`
3. 它只负责验证：
   - `SessionRuntime -> ChatRuntime` 的入口委托关系
   - `/api/chat/*` 与 channel ingress 的统一入口
   - `/api/system/runtime-topology` 与 `/api/system/session-runtime` 的诊断诚实性
4. 它不代表正式文档 Phase70 已被实现，也不替代 `SessionContext` / 可见性治理阶段本体
5. 后续主线以 Phase77/78 为准；若未来统一清理历史阶段编号，再集中处理测试文件命名
