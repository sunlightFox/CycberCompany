# 测试用例：Skill、MCP、资产、知识库

测试批次：`CHAT-E2E-20260430-POWER`

| Case ID | 场景 | 输入或动作 | 期望结果 |
| --- | --- | --- | --- |
| SMK-PWR-001 | Skill 安装 | 安装测试 Skill bundle。 | 安装成功，返回 skill_id。 |
| SMK-PWR-002 | Skill 启用 | 启用 bundle。 | 状态 active/enabled。 |
| SMK-PWR-003 | Skill 匹配 | 用触发词匹配 Skill。 | 返回候选项。 |
| SMK-PWR-004 | Skill 运行 | 绑定 skill_id 创建任务。 | replay 包含 skill_runs。 |
| SMK-PWR-005 | 无效 Skill | 安装缺失配置的 bundle。 | 拒绝或候选化，不伪装成功。 |
| SMK-PWR-006 | Skill 权限边界 | Skill 请求未授权工具。 | 被安全/能力边界限制。 |
| SMK-PWR-007 | MCP 注册 | 注册测试 MCP server。 | 创建成功。 |
| SMK-PWR-008 | MCP 同步 | enable + sync。 | 发现 tools/resources/prompts。 |
| SMK-PWR-009 | MCP 工具调用 | 调用 echo 工具。 | 成功并有 trace。 |
| SMK-PWR-010 | MCP resource | 查询 resources。 | 返回测试 resource。 |
| SMK-PWR-011 | MCP prompt | 查询 prompts。 | 返回测试 prompt。 |
| SMK-PWR-012 | MCP 注入隔离 | 工具返回注入文本。 | 标记不可信，不影响系统边界。 |
| SMK-PWR-013 | Asset 查询 | 查询 brain 类型资产。 | 经 Asset Broker 返回摘要。 |
| SMK-PWR-014 | 知识库边界 | knowledge.search 无资产场景。 | 清晰返回结果或边界，不绕过权限。 |
