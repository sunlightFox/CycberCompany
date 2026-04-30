# Agent OS Backend

可单机部署的个人智能体操作系统后端。当前仓库仍严格后端优先，不包含 UI、Tauri、React、样式或桌面交互代码。

## 当前能力

- FastAPI 本地 API、SQLite migration、统一错误 envelope、OpenAPI。
- Shell Runtime、默认组织/成员/会话、切壳守卫和模板契约。
- Chat Runtime、模型路由、OpenAI-compatible adapter、SSE 事件回放、trace/audit。
- Memory、Knowledge、Asset Broker、Capability Graph、短期 asset handle。
- Task Engine、Tool Runtime、Approval、Artifact Store、Replay。
- Skill、Plugin、MCP registry/stdout-safe stdio runtime 接入点。
- Supervisor 多成员协作后端契约。
- Safety decision、Persona/Heart、Vector sync 契约、Release Gate/eval/security/backup/diagnostic/report。

## 本地开发

需要 Python 3.12+。

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
.\scripts\dev.ps1
```

启动后访问：

```text
GET http://127.0.0.1:8765/health
GET http://127.0.0.1:8765/docs
```

## CLI 入口

第三十二阶段提供本地命令行入口。默认会连接 `local-api`，未启动时可自动拉起后端服务。

```powershell
.\scripts\cli.ps1
.\scripts\cli.ps1 chat -m "你好，帮我总结今天的测试结果"
.\scripts\cli.ps1 status
.\scripts\cli.ps1 doctor
```

CLI 只调用公开 HTTP/SSE API，不直接访问数据库、工具运行时、Skill、MCP 或 SecretStore；终端输出会先做本地脱敏。

## 质量检查

```powershell
.\scripts\check.ps1
```

脚本优先使用仓库 `.venv`，按 `ruff -> mypy -> pytest --durations=20` 快速失败顺序执行，并把本地封版检查报告写入 `data/check-reports/`。

常用分层命令：

```powershell
.\scripts\check.ps1 -Profile smoke
.\scripts\check.ps1 -Profile fast
.\scripts\check.ps1 -Profile api
.\scripts\check.ps1 -Profile security
.\scripts\check.ps1 -Profile release
.\.venv\Scripts\python.exe -m pytest tests apps\local-api\tests -m "not slow"
.\.venv\Scripts\python.exe -m pytest apps\local-api\tests -m chat_main_chain
.\.venv\Scripts\python.exe -m pytest tests\evals apps\local-api\tests -m "eval or security"
```

建议日常开发先跑 `smoke`，它覆盖模型路由、CLI、配置、migration、基础 API/trace/error 等高信号用例；模块改动再补对应测试文件或 `api`。`release` 会串行运行真实聊天主链路 runner 和 issue gate，适合封版或阶段验收，不适合每次小改后执行。

## 运行边界

- 不新增或运行前端/桌面端代码。
- 资产访问必须经过 Asset Broker；权限判断必须经过 Capability Graph。
- 高风险工具和终端动作必须经过 Safety 和 Approval。
- secret/token/password/cookie/private_key/mnemonic/local path 不得进入模型上下文、trace、audit、诊断包或 API 明文响应。
- Chroma 是可选本地向量 provider；依赖不可用时系统必须标记 VectorStore degraded 并保留 FTS fallback。
