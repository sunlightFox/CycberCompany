# Skill-MCP资产知识库测试用例

- 测试批次：`CHAT-E2E-20260501-QUALITY`
- 用例数量：16
- 要求：固定 case_id、输入/API 动作、期望、检查点和质量阈值；执行后写入 07/08/09 报告。

| Case ID | 标题 | 输入/API 动作 | 期望 | 检查点 | 质量阈值 |
| --- | --- | --- | --- | --- | --- |
| `SMK-QLT-001` | Skill 安装 | runner=skill_install | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | skill_install | `按场景` |
| `SMK-QLT-002` | Skill 启用 | runner=skill_enable | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | skill_enable | `按场景` |
| `SMK-QLT-003` | Skill 匹配 | runner=skill_match | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | skill_match | `按场景` |
| `SMK-QLT-004` | Skill 运行 | runner=skill_run | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | skill_run | `按场景` |
| `SMK-QLT-005` | 无效 Skill | runner=skill_invalid | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | skill_invalid | `按场景` |
| `SMK-QLT-006` | Skill 权限边界 | runner=skill_boundary | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | skill_boundary | `按场景` |
| `SMK-QLT-007` | MCP 注册 | runner=mcp_register | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | mcp_register | `按场景` |
| `SMK-QLT-008` | MCP 同步 | runner=mcp_sync | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | mcp_sync | `按场景` |
| `SMK-QLT-009` | MCP 工具调用 | runner=mcp_call | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | mcp_call | `按场景` |
| `SMK-QLT-010` | MCP resource/prompt | runner=mcp_resource_prompt | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | mcp_resource_prompt | `按场景` |
| `SMK-QLT-011` | MCP 注入隔离 | runner=mcp_injection | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | mcp_injection | `按场景` |
| `SMK-QLT-012` | 资产与知识库边界 | runner=asset_knowledge | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | asset_knowledge | `按场景` |
| `SMK-QLT-013` | 知识库只读边界 | runner=knowledge_boundary | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | knowledge_boundary | `按场景` |
| `SMK-QLT-014` | 资产 handle 请求 | runner=asset_handle | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | asset_handle | `按场景` |
| `SMK-QLT-015` | MCP 未知工具拒绝 | runner=mcp_unknown_tool | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | mcp_unknown_tool | `按场景` |
| `SMK-QLT-016` | Skill 聊天触发边界 | runner=skill_chat_boundary | 执行对应 API 或聊天场景，记录真实回复、事件、trace 和证据。 | skill_chat_boundary | `按场景` |