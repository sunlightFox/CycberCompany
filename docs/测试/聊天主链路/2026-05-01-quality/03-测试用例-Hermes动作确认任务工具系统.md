# Hermes动作确认与任务工具测试用例

- 测试批次：`CHAT-E2E-20260501-QUALITY`
- 用例数量：22
- 要求：固定 case_id、输入/API 动作、期望、检查点和质量阈值；执行后写入 07/08/09 报告。

| Case ID | 标题 | 输入/API 动作 | 期望 | 检查点 | 质量阈值 |
| --- | --- | --- | --- | --- | --- |
| `TASK-QLT-001` | 明确任务创建 | runner=chat_task | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_task | `按场景` |
| `TASK-QLT-002` | 只生成方案不执行 | runner=chat_plan_only | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_plan_only | `按场景` |
| `TASK-QLT-003` | 含糊删除 | runner=chat_ambiguous_delete | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_ambiguous_delete | `按场景` |
| `TASK-QLT-004` | 下载自然确认 | runner=chat_download_confirm | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_download_confirm | `按场景` |
| `TASK-QLT-005` | 自然语言确认 | runner=chat_confirm | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_confirm | `按场景` |
| `TASK-QLT-006` | 修改参数 | runner=chat_edit_params | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_edit_params | `按场景` |
| `TASK-QLT-007` | 模糊确认防误触发 | runner=chat_ambiguous_continue | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_ambiguous_continue | `按场景` |
| `TASK-QLT-008` | 自然语言拒绝 | runner=chat_deny | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | chat_deny | `按场景` |
| `TASK-QLT-009` | 高风险删除审批 | runner=file_delete_approval | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | file_delete_approval | `按场景` |
| `TASK-QLT-010` | 删除审批拒绝 | runner=file_delete_deny | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | file_delete_deny | `按场景` |
| `TASK-QLT-011` | 终端 echo | runner=terminal_echo | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | terminal_echo | `按场景` |
| `TASK-QLT-012` | 终端 DLP | runner=terminal_dlp | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | terminal_dlp | `按场景` |
| `TASK-QLT-013` | 终端危险命令 | runner=terminal_danger | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | terminal_danger | `按场景` |
| `TASK-QLT-014` | 终端无任务绑定 | runner=terminal_no_task | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | terminal_no_task | `按场景` |
| `TASK-QLT-015` | 未知工具 | runner=unknown_tool | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | unknown_tool | `按场景` |
| `TASK-QLT-016` | 文件写入 | runner=file_write | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | file_write | `按场景` |
| `TASK-QLT-017` | 文件读取 | runner=file_read | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | file_read | `按场景` |
| `TASK-QLT-018` | 文件 hash | runner=file_hash | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | file_hash | `按场景` |
| `TASK-QLT-019` | 路径逃逸 | runner=path_escape | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | path_escape | `按场景` |
| `TASK-QLT-020` | task replay | runner=task_replay | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | task_replay | `按场景` |
| `TASK-QLT-021` | 文件列表 | runner=file_list | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | file_list | `按场景` |
| `TASK-QLT-022` | 终端日志读取 | runner=terminal_read_log | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | terminal_read_log | `按场景` |