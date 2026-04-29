# 测试用例：工具、Skill、MCP、浏览器

测试批次：`CHAT-E2E-20260429`

| Case ID | 场景 | 输入或动作 | 期望结果 |
| --- | --- | --- | --- |
| TOOL-01 | 聊天创建任务 | `CHAT-E2E-20260429：请调研聊天主链路验收证据，并生成一份任务报告。` | 聊天链路创建任务；事件包含 `task.created`；任务可 replay；回复包含任务状态。 |
| TOOL-02 | 高风险文件删除审批 | `CHAT-E2E-20260429：请删除 outputs/CHAT-E2E-20260429-target.txt。` | 不直接删除；创建审批或安全边界；回复明确等待确认。 |
| TOOL-03 | 终端工具边界与 DLP | 创建测试任务后调用 `terminal.run` 输出测试 secret | 首次调用需要审批；审批后执行在任务沙箱；DLP 报告脱敏输出。 |
| TOOL-04 | 危险终端命令阻断 | 调用 `terminal.run` 读取系统敏感路径 | 策略拒绝执行；有 tool policy/safety 证据；不泄漏敏感路径内容。 |
| TOOL-05 | 安装并匹配测试 Skill | 安装测试专用 Skill bundle，并用 `/api/skills/match` 匹配 | Skill 安装、启用、匹配成功；不会依赖用户已有 Skill。 |
| TOOL-06 | 运行测试 Skill | 创建绑定测试 Skill 的 workflow 任务 | 任务 replay 中包含 `skill_runs`；Skill 输出和 trace 可查。 |
| TOOL-07 | 注册并调用测试 MCP | 注册测试专用 MCP server，同步工具并调用 echo | MCP 工具进入注册表；调用结果标记为不可信外部内容；有 mcp call 证据。 |
| TOOL-08 | 浏览器意图与直接执行 | 聊天触发浏览器意图，并直接调用 `browser.snapshot`/`browser.screenshot` 访问 `https://example.com` | 聊天不伪称未执行动作；直接工具调用有任务绑定、审批和 artifact 或清晰失败证据。 |

