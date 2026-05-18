# 第一百零八阶段 - ChatRuntime宿主瘦身与职责拆分封口

## 阶段定位

phase91 已经明确了 ChatRuntime 宿主拆分方向，但宿主层仍然偏厚，`chat.py`、`natural_chat.py` 和若干兼容 facade 还承担了过多实际职责，导致 readiness 中 phase91 仍难以稳定转为 ready。

这一阶段不是新加运行时能力，而是把已有能力真正放回各自归属模块，完成 host decomposition 的封口。

## 目标

```text
完成 phase91 还没收完的 host decomposition
拆薄 chat.py
拆薄 natural_chat.py
让 compat facade 真正只保留壳层职责
```

## 重点

### 108.1 chat.py 宿主瘦身

```text
把不属于宿主编排的业务逻辑继续下沉到专属 service
宿主层只负责拼装、转发、生命周期与边界控制
```

### 108.2 natural_chat.py 职责剥离

```text
自然语言交互层不再承担额外的执行域逻辑
格式决策、路由、执行建议、状态解释应回到各自模块
```

### 108.3 compat facade 封口

```text
compat facade 保留兼容壳职责
不再偷偷承接真实业务执行与状态派生
ownership split status 要能清晰落到模块边界
```

## 直接依赖

```text
docs/开发计划/91-第九十一阶段-ChatRuntime物理拆分与宿主瘦身收尾.md
apps/local-api/app/services/chat.py
apps/local-api/app/services/natural_chat.py
apps/local-api/app/services/chat_runtime_host_helpers.py
apps/local-api/app/services/chat_turn_execution.py
apps/local-api/app/services/chat_session_runtime.py
apps/local-api/app/services/chat_mainline_readiness.py
apps/local-api/tests/test_phase91_host_decomposition_governance.py
```

## 验收

```text
/api/system/chat-mainline-readiness 中 phase91 从 partial 变 ready
host size budget 达标
ownership split status 达标
compat facade 不再承担超出壳层边界的真实业务职责
```
