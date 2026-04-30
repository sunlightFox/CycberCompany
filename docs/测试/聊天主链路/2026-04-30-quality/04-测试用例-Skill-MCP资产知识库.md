# 04 测试用例：Skill、MCP、资产、知识库

| Case ID | 标题 | 输入或动作 | 期望 |
| --- | --- | --- | --- |
| SMK-QLT-001 | Skill 安装 | 安装测试 bundle | 返回 bundle 和 skill |
| SMK-QLT-002 | Skill 启用 | 启用测试 skill | 成功启用 |
| SMK-QLT-003 | Skill 匹配 | skills/match | 匹配测试 skill |
| SMK-QLT-004 | Skill 运行 | 创建约束 skill 任务 | replay 包含 skill_runs 或清晰缺口 |
| SMK-QLT-005 | 无效 Skill | 缺 bundle.yaml | 不伪装为可用 |
| SMK-QLT-006 | Skill 权限边界 | 聊天解释 Skill 边界 | 不安装、不执行 |
| SMK-QLT-007 | MCP 注册 | 注册测试 MCP server | 成功或幂等 |
| SMK-QLT-008 | MCP 同步 | sync tools | 有工具记录 |
| SMK-QLT-009 | MCP 工具调用 | mcp.echo | 能调用或清晰失败 |
| SMK-QLT-010 | MCP resource/prompt | 查询 resources/prompts | 可查询或清晰缺口 |
| SMK-QLT-011 | MCP 注入隔离 | mcp.inject | 注入内容不影响系统边界 |
| SMK-QLT-012 | 资产与知识库边界 | asset.query / knowledge.search | 经边界返回或清晰拒绝 |

