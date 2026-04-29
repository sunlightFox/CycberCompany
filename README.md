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

## 质量检查

```powershell
.\scripts\check.ps1
```

脚本优先使用仓库 `.venv`，按 `ruff -> mypy -> pytest --durations=20` 快速失败顺序执行，并把本地封版检查报告写入 `data/check-reports/`。

常用分层命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests apps\local-api\tests -m "not slow"
.\.venv\Scripts\python.exe -m pytest apps\local-api\tests -m chat_main_chain
.\.venv\Scripts\python.exe -m pytest tests\evals apps\local-api\tests -m "eval or security"
```

## 运行边界

- 不新增或运行前端/桌面端代码。
- 资产访问必须经过 Asset Broker；权限判断必须经过 Capability Graph。
- 高风险工具和终端动作必须经过 Safety 和 Approval。
- secret/token/password/cookie/private_key/mnemonic/local path 不得进入模型上下文、trace、audit、诊断包或 API 明文响应。
- Chroma 是可选本地向量 provider；依赖不可用时系统必须标记 VectorStore degraded 并保留 FTS fallback。
