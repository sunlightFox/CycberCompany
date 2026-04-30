# ruff: noqa: E501

from __future__ import annotations

import json
import os
from typing import Any, cast

import run_chat_main_chain_cases as base
from fastapi.testclient import TestClient

TASK_CASE_DOC_PATH = base.TEST_DIR / "25-任务执行测试用例.md"
TASK_REPORT_PATH = base.TEST_DIR / "26-任务执行测试报告.md"
TASK_ISSUES_PATH = base.TEST_DIR / "27-任务执行待修复问题.md"


class TaskExecutionRunner(base.Runner):
    def __init__(self) -> None:
        super().__init__()
        self.task_sequence = 0

    def run(self) -> None:
        base.RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        app = base.create_app()
        with TestClient(app) as client:
            registry = cast(Any, client.app).state.registry
            registry.mcp_service.set_transport_factory(lambda _server: base.ChatE2EMCPTransport())
            preflight_ok = self._run_preflight(client)
            if preflight_ok:
                self._run_task_execution_cases(client)
            else:
                self._add_issue(
                    "P0",
                    "PREFLIGHT",
                    "真实模型预检失败，未执行任务执行用例",
                    "默认大脑验证成功，最小聊天出现 model.started 和 model.completed。",
                    json.dumps(self.preflight, ensure_ascii=False),
                    self.preflight,
                )
        self._write_outputs()

    def _run_task_execution_cases(self, client: TestClient) -> None:
        cases = [
            self._run_tool_registry_case,
            self._run_auto_task_idempotency_case,
            self._run_manual_start_case,
            self._run_task_cancel_replay_case,
            self._run_browser_snapshot_case,
            self._run_browser_screenshot_case,
            self._run_browser_download_case,
            self._run_browser_without_task_case,
            self._run_browser_dangerous_url_case,
            self._run_chat_browser_task_case,
            self._run_terminal_approved_echo_case,
            self._run_terminal_timeout_case,
            self._run_terminal_custom_cwd_denied_case,
            self._run_terminal_without_task_case,
            self._run_terminal_env_isolation_case,
            self._run_file_roundtrip_case,
            self._run_file_delete_deny_case,
            self._run_file_escape_write_case,
            self._run_sandbox_status_case,
        ]
        for case_func in cases:
            try:
                self.results.append(case_func(client))
            except Exception as exc:
                case_id = getattr(case_func, "case_id", case_func.__name__)
                self.results.append(
                    self._exception_result(
                        {
                            "case_id": str(case_id),
                            "category": "任务执行",
                            "title": case_func.__name__,
                            "turns": [],
                            "checks": [],
                        },
                        exc,
                    )
                )

    def _run_tool_registry_case(self, client: TestClient) -> base.CaseResult:
        tools = self._request(client, "GET", "/api/tools")
        policies = self._request(client, "GET", "/api/tools/policies")
        required = {
            "browser.snapshot",
            "browser.screenshot",
            "browser.download",
            "terminal.run",
            "file.write",
            "file.read",
            "file.list",
            "file.delete",
            "file.hash",
        }
        tool_names = {item.get("tool_name") for item in tools.get("data", {}).get("items", [])}
        policy_names = {item.get("tool_name") for item in policies.get("data", {}).get("items", [])}
        result = self._direct_result(
            "TASK-01",
            "任务与工具注册",
            "工具注册与策略",
            ["GET /api/tools", "GET /api/tools/policies"],
            {"tools": tools, "policies": policies},
            "browser/terminal/file tools and policies should be registered",
        )
        missing_tools = sorted(required - tool_names)
        if missing_tools:
            self._fail_case(result, "P1", "任务工具注册缺失", f"应注册工具：{', '.join(sorted(required))}。", f"missing={missing_tools}")
        if "terminal.run" not in policy_names:
            self._fail_case(result, "P2", "terminal.run 缺少工具策略", "系统任务终端工具应有策略记录。", json.dumps(policies, ensure_ascii=False))
        return result

    def _run_auto_task_idempotency_case(self, client: TestClient) -> base.CaseResult:
        request_id = f"{base.RUN_LABEL}:{base.RUN_ID}:task-auto-idempotency"
        payload = {
            "goal": f"{base.RUN_LABEL} 任务执行：生成一份只读任务测试摘要，不调用终端或浏览器。",
            "mode_hint": "workflow",
            "constraints": {"no_terminal": True, "no_browser": True},
            "auto_start": True,
            "client_request_id": request_id,
        }
        first = self._request(client, "POST", "/api/tasks", json=payload)
        second = self._request(client, "POST", "/api/tasks", json=payload)
        task_id = first.get("data", {}).get("task_id")
        replay = self._request(client, "GET", f"/api/tasks/{task_id}/replay") if task_id else {"status_code": 0, "data": {}}
        artifacts = self._request(client, "GET", f"/api/tasks/{task_id}/artifacts") if task_id else {"status_code": 0, "data": {}}
        result = self._direct_result(
            "TASK-02",
            "任务生命周期",
            "auto_start 任务与幂等创建",
            ["POST /api/tasks auto_start=true twice", f"GET /api/tasks/{task_id}/replay", f"GET /api/tasks/{task_id}/artifacts"],
            {"first": first, "second": second, "replay": replay, "artifacts": artifacts},
            "same client_request_id should replay same task and keep replay/artifact evidence",
        )
        first_data = cast(dict[str, Any], first.get("data") or {})
        second_data = cast(dict[str, Any], second.get("data") or {})
        replay_data = cast(dict[str, Any], replay.get("data") or {})
        if first.get("status_code") != 200 or second.get("status_code") != 200:
            self._fail_case(result, "P1", "auto_start 任务创建失败", "两次幂等创建都应成功。", json.dumps({"first": first, "second": second}, ensure_ascii=False))
        elif first_data.get("task_id") != second_data.get("task_id"):
            self._fail_case(result, "P1", "任务 client_request_id 未幂等", "同一 client_request_id 应返回同一 task_id。", json.dumps({"first": first, "second": second}, ensure_ascii=False))
        if first_data.get("status") not in {"completed", "waiting_approval", "paused", "failed"}:
            self._fail_case(result, "P2", "auto_start 任务未进入清晰状态", "任务应进入 completed/waiting_approval/paused/failed 等可解释状态。", json.dumps(first, ensure_ascii=False))
        if replay.get("status_code") != 200 or not replay_data.get("events"):
            self._fail_case(result, "P2", "auto_start 任务 replay 缺失", "任务 replay 应包含 events。", json.dumps(replay, ensure_ascii=False))
        if artifacts.get("status_code") != 200:
            self._fail_case(result, "P2", "auto_start 任务 artifacts 不可查", "任务 artifacts endpoint 应可查。", json.dumps(artifacts, ensure_ascii=False))
        return result

    def _run_manual_start_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：手动启动任务", auto_start=False)
        started = self._request(client, "POST", f"/api/tasks/{task.get('task_id')}/start")
        events = self._request(client, "GET", f"/api/tasks/{task.get('task_id')}/events")
        replay = self._request(client, "GET", f"/api/tasks/{task.get('task_id')}/replay")
        result = self._direct_result(
            "TASK-03",
            "任务生命周期",
            "手动创建后启动",
            [f"POST /api/tasks/{task.get('task_id')}/start", f"GET /api/tasks/{task.get('task_id')}/events"],
            {"task": task, "started": started, "events": events, "replay": replay},
            "manual task start should succeed and expose events/replay",
        )
        if started.get("status_code") != 200:
            self._fail_case(result, "P1", "手动任务启动失败", "created 任务应能 start。", json.dumps(started, ensure_ascii=False))
        if events.get("status_code") != 200 or not isinstance(events.get("data", {}).get("items"), list):
            self._fail_case(result, "P2", "手动任务 events 不可查", "任务 events 应返回列表。", json.dumps(events, ensure_ascii=False))
        if replay.get("status_code") != 200:
            self._fail_case(result, "P2", "手动任务 replay 不可查", "任务 replay 应可查。", json.dumps(replay, ensure_ascii=False))
        return result

    def _run_task_cancel_replay_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：取消回放", auto_start=False)
        cancelled = self._request(client, "POST", f"/api/tasks/{task.get('task_id')}/cancel", json={"reason": f"{base.RUN_LABEL} task execution cancel"})
        replay = self._request(client, "GET", f"/api/tasks/{task.get('task_id')}/replay")
        result = self._direct_result(
            "TASK-04",
            "任务生命周期",
            "任务取消与 replay",
            [f"POST /api/tasks/{task.get('task_id')}/cancel", f"GET /api/tasks/{task.get('task_id')}/replay"],
            {"task": task, "cancelled": cancelled, "replay": replay},
            "cancelled task should retain replay evidence",
        )
        if cancelled.get("status_code") != 200 or cancelled.get("data", {}).get("status") != "cancelled":
            self._fail_case(result, "P1", "任务取消失败", "取消后任务状态应为 cancelled。", json.dumps(cancelled, ensure_ascii=False))
        if replay.get("status_code") != 200:
            self._fail_case(result, "P2", "取消任务 replay 不可查", "取消后的任务 replay 应可查。", json.dumps(replay, ensure_ascii=False))
        return result

    def _run_browser_snapshot_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：browser snapshot", auto_start=False)
        snapshot = self._request(
            client,
            "POST",
            "/api/tools/execute",
            json={"task_id": task.get("task_id"), "tool_name": "browser.snapshot", "args": {"url": "https://example.com"}},
        )
        result = self._direct_result(
            "TASK-05",
            "浏览器任务",
            "browser.snapshot",
            ["POST /api/tools/execute browser.snapshot https://example.com"],
            {"task": task, "snapshot": snapshot},
            "browser.snapshot should complete with task-bound evidence",
        )
        if snapshot.get("status_code") != 200:
            self._fail_case(result, "P1", "browser.snapshot 执行失败", "应能访问 https://example.com 或返回清晰环境失败。", json.dumps(snapshot, ensure_ascii=False))
        return result

    def _run_browser_screenshot_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：browser screenshot", auto_start=False)
        payload = {"task_id": task.get("task_id"), "tool_name": "browser.screenshot", "args": {"url": "https://example.com"}}
        executed = self._approve_and_execute_tool(client, payload, "TASK-06 browser screenshot approval")
        screenshot = executed["executed"]
        result = self._direct_result(
            "TASK-06",
            "浏览器任务",
            "browser.screenshot",
            ["POST /api/tools/execute browser.screenshot https://example.com"],
            {"task": task, **executed},
            "browser.screenshot should complete and produce artifact evidence",
        )
        if screenshot.get("status_code") != 200:
            self._fail_case(result, "P2", "browser.screenshot 执行失败", "截图应成功或给出清晰环境失败。", json.dumps(screenshot, ensure_ascii=False))
        elif "artifact" not in json.dumps(screenshot.get("data", {}), ensure_ascii=False).lower():
            self._fail_case(result, "P2", "browser.screenshot 缺少 artifact", "截图成功应返回 artifact 证据。", json.dumps(screenshot, ensure_ascii=False))
        return result

    def _run_browser_download_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：browser download", auto_start=False)
        payload = {"task_id": task.get("task_id"), "tool_name": "browser.download", "args": {"url": "https://example.com", "display_name": f"{base.RUN_LABEL}-task-example.html"}}
        executed = self._approve_and_execute_tool(client, payload, "TASK-07 browser download approval")
        downloaded = executed["executed"]
        result = self._direct_result(
            "TASK-07",
            "浏览器任务",
            "browser.download",
            ["POST /api/tools/execute browser.download https://example.com"],
            {"task": task, **executed},
            "browser.download should complete and produce artifact evidence",
        )
        if downloaded.get("status_code") != 200:
            self._fail_case(result, "P2", "browser.download 执行失败", "下载应成功或给出清晰环境失败。", json.dumps(downloaded, ensure_ascii=False))
        elif "artifact" not in json.dumps(downloaded.get("data", {}), ensure_ascii=False).lower():
            self._fail_case(result, "P2", "browser.download 缺少 artifact", "下载成功应返回 artifact 证据。", json.dumps(downloaded, ensure_ascii=False))
        return result

    def _run_browser_without_task_case(self, client: TestClient) -> base.CaseResult:
        denied = self._request(client, "POST", "/api/tools/execute", json={"tool_name": "browser.download", "args": {"url": "https://example.com"}})
        result = self._direct_result(
            "TASK-08",
            "浏览器任务",
            "浏览器无任务绑定边界",
            ["POST /api/tools/execute browser.download without task_id"],
            {"denied": denied},
            "browser.download without task_id should be denied",
        )
        if denied.get("status_code") not in {403, 409, 422}:
            self._fail_case(result, "P1", "browser.download 未强制任务绑定", "浏览器下载应要求 task_id。", json.dumps(denied, ensure_ascii=False))
        return result

    def _run_browser_dangerous_url_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：browser dangerous url", auto_start=False)
        blocked = self._request(
            client,
            "POST",
            "/api/tools/execute",
            json={"task_id": task.get("task_id"), "tool_name": "browser.snapshot", "args": {"url": "data:text/html,<h1>CHAT-E2E-20260429</h1>"}},
        )
        result = self._direct_result(
            "TASK-09",
            "浏览器任务",
            "浏览器危险 URL 阻断",
            ["POST /api/tools/execute browser.snapshot data:text/html"],
            {"task": task, "blocked": blocked},
            "browser tools should reject data: URL",
        )
        if blocked.get("status_code") not in {403, 422}:
            self._fail_case(result, "P1", "browser.snapshot 未拒绝 data URL", "浏览器工具应拒绝 data: 等非 http(s) URL。", json.dumps(blocked, ensure_ascii=False))
        return result

    def _run_chat_browser_task_case(self, client: TestClient) -> base.CaseResult:
        case = {
            "case_id": "TASK-10",
            "category": "聊天触发任务",
            "title": "聊天创建浏览器任务",
            "turns": [f"{base.RUN_LABEL} 任务执行：请创建一个浏览器任务，打开 https://example.com，采集标题和截图证据。"],
            "checks": ["completed", "task_created"],
        }
        result = self._run_chat_scenario(client, case)
        if "已打开" in result.actual_reply and "browser." not in " ".join(result.event_sequence):
            self._fail_case(result, "P1", "聊天浏览器任务疑似伪执行", "没有浏览器工具事件时不应声称已经打开网页。", result.actual_reply)
        return result

    def _run_terminal_approved_echo_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：terminal echo", auto_start=False)
        payload = {
            "task_id": task.get("task_id"),
            "tool_name": "terminal.run",
            "args": {"command": f"echo {base.RUN_LABEL}-terminal-ok"},
        }
        executed = self._approve_and_execute_tool(client, payload, "TASK-11 terminal echo approval")
        tool_call_id = _tool_call_id(executed["executed"])
        boundary = self._request(client, "GET", f"/api/tools/calls/{tool_call_id}/boundary") if tool_call_id else {"status_code": 0, "data": {}}
        replay = self._request(client, "GET", f"/api/tasks/{task.get('task_id')}/replay")
        result = self._direct_result(
            "TASK-11",
            "系统/终端任务",
            "terminal.run 审批后执行",
            ["POST /api/tools/execute terminal.run echo", f"GET /api/tools/calls/{tool_call_id}/boundary"],
            {"task": task, **executed, "boundary": boundary, "replay": replay},
            "approved terminal.run should execute with boundary and replay evidence",
        )
        if executed["executed"].get("status_code") != 200:
            self._fail_case(result, "P1", "审批后 terminal.run 未执行成功", "审批后安全 echo 命令应执行成功。", json.dumps(executed, ensure_ascii=False))
        if not tool_call_id:
            self._fail_case(result, "P1", "terminal.run 缺少 tool_call_id", "执行结果应包含 tool_call_id。", json.dumps(executed, ensure_ascii=False))
        if boundary.get("status_code") != 200:
            self._fail_case(result, "P2", "terminal.run boundary 不可查", "tool_call boundary 应可查。", json.dumps(boundary, ensure_ascii=False))
        if "terminal.run" not in json.dumps(replay.get("data", {}), ensure_ascii=False):
            self._fail_case(result, "P2", "任务 replay 缺少 terminal.run", "任务回放应包含终端工具调用。", json.dumps(replay, ensure_ascii=False))
        return result

    def _run_terminal_timeout_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：terminal timeout", auto_start=False)
        payload = {
            "task_id": task.get("task_id"),
            "tool_name": "terminal.run",
            "args": {"command": "python -c \"import time; time.sleep(5)\"", "timeout_seconds": 1},
        }
        executed = self._approve_and_execute_tool(client, payload, "TASK-12 terminal timeout approval")
        status = self._request(client, "GET", "/api/execution-boundary/sandbox-status")
        result = self._direct_result(
            "TASK-12",
            "系统/终端任务",
            "终端超时诊断",
            ["POST /api/tools/execute terminal.run timeout_seconds=1", "GET /api/execution-boundary/sandbox-status"],
            {"task": task, **executed, "sandbox_status": status},
            "terminal timeout should return TOOL_TIMEOUT and sandbox diagnostics",
        )
        data = executed["executed"].get("data", {})
        serialized = json.dumps(data, ensure_ascii=False)
        if executed["executed"].get("status_code") != 504 and "TOOL_TIMEOUT" not in serialized:
            self._fail_case(result, "P1", "terminal.run 超时未返回 TOOL_TIMEOUT", "超时任务应返回 504/TOOL_TIMEOUT。", json.dumps(executed, ensure_ascii=False))
        diagnostic = status.get("data", {}).get("last_diagnostic_summary", {})
        if status.get("status_code") != 200 or not diagnostic:
            self._fail_case(result, "P2", "超时后缺少沙箱诊断", "sandbox status 应包含 last_diagnostic_summary。", json.dumps(status, ensure_ascii=False))
        return result

    def _run_terminal_custom_cwd_denied_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：terminal cwd denied", auto_start=False)
        denied = self._request(
            client,
            "POST",
            "/api/tools/execute",
            json={"task_id": task.get("task_id"), "tool_name": "terminal.run", "args": {"command": "echo cwd", "cwd": "C:\\"}},
        )
        result = self._direct_result(
            "TASK-13",
            "系统/终端任务",
            "终端自定义 cwd 拒绝",
            ["POST /api/tools/execute terminal.run cwd=C:\\"],
            {"task": task, "denied": denied},
            "terminal.run should reject custom cwd outside task sandbox",
        )
        if denied.get("status_code") not in {403, 422}:
            self._fail_case(result, "P1", "terminal.run 未拒绝自定义 cwd", "终端 cwd 不应跳出任务沙箱。", json.dumps(denied, ensure_ascii=False))
        return result

    def _run_terminal_without_task_case(self, client: TestClient) -> base.CaseResult:
        denied = self._request(client, "POST", "/api/tools/execute", json={"tool_name": "terminal.run", "args": {"command": f"echo {base.RUN_LABEL}"}})
        result = self._direct_result(
            "TASK-14",
            "系统/终端任务",
            "终端无任务绑定拒绝",
            ["POST /api/tools/execute terminal.run without task_id"],
            {"denied": denied},
            "terminal.run without task_id should be denied",
        )
        if denied.get("status_code") not in {403, 409, 422}:
            self._fail_case(result, "P1", "terminal.run 未强制任务绑定", "终端工具应要求 task_id。", json.dumps(denied, ensure_ascii=False))
        return result

    def _run_terminal_env_isolation_case(self, client: TestClient) -> base.CaseResult:
        old_api_key = os.environ.get("API_KEY")
        old_token = os.environ.get("TOKEN")
        os.environ["API_KEY"] = "sk-chatmaine2e-task-env-secret"
        os.environ["TOKEN"] = "token-chatmaine2e-task-env-secret"
        try:
            task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：terminal env isolation", auto_start=False)
            payload = {
                "task_id": task.get("task_id"),
                "tool_name": "terminal.run",
                "args": {
                    "command": "python -c \"import os; print('api='+str(os.getenv('API_KEY'))); print('token='+str(os.getenv('TOKEN'))); print('task='+str(os.getenv('CYCBER_TASK_ID')))\"",
                    "max_output_bytes": 300,
                },
            }
            executed = self._approve_and_execute_tool(client, payload, "TASK-15 terminal env isolation approval")
            log_artifact_id = executed["executed"].get("data", {}).get("result", {}).get("log_artifact_id")
            artifact = self._request(client, "GET", f"/api/artifacts/{log_artifact_id}") if log_artifact_id else {"status_code": 0, "data": {}}
        finally:
            _restore_env("API_KEY", old_api_key)
            _restore_env("TOKEN", old_token)
        result = self._direct_result(
            "TASK-15",
            "系统/终端任务",
            "环境变量 secret 隔离",
            ["POST /api/tools/execute terminal.run print env"],
            {"task": task, **executed, "artifact": artifact},
            "terminal sandbox should not inherit host API_KEY/TOKEN secrets",
        )
        raw = json.dumps({"executed": executed, "artifact": artifact}, ensure_ascii=False)
        if "sk-chatmaine2e-task-env-secret" in raw or "token-chatmaine2e-task-env-secret" in raw:
            self._fail_case(result, "P0", "终端任务泄漏宿主环境 secret", "系统任务不应继承或输出宿主 API_KEY/TOKEN。", raw)
        if executed["executed"].get("status_code") != 200:
            self._fail_case(result, "P1", "终端环境隔离命令未执行成功", "审批后环境隔离命令应执行成功。", json.dumps(executed, ensure_ascii=False))
        return result

    def _run_file_roundtrip_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：file roundtrip", auto_start=False)
        path = f"outputs/{base.RUN_LABEL}-task-roundtrip.txt"
        write = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.write", "args": {"path": path, "content": f"{base.RUN_LABEL} task file roundtrip"}})
        read = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.read", "args": {"path": path}})
        hashed = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.hash", "args": {"path": path}})
        listed = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.list", "args": {"path": "outputs"}})
        result = self._direct_result(
            "TASK-16",
            "文件任务",
            "文件写读 hash list",
            ["file.write", "file.read", "file.hash", "file.list"],
            {"task": task, "write": write, "read": read, "hashed": hashed, "listed": listed},
            "file tools should roundtrip content and checksum in task sandbox",
        )
        for name, response in {"write": write, "read": read, "hashed": hashed, "listed": listed}.items():
            if response.get("status_code") != 200:
                self._fail_case(result, "P1", f"file.{name} 调用失败", "文件工具链路应成功。", json.dumps(response, ensure_ascii=False))
        if "sha256:" not in json.dumps(hashed.get("data", {}), ensure_ascii=False):
            self._fail_case(result, "P2", "file.hash 缺少 sha256", "hash 结果应包含 sha256。", json.dumps(hashed, ensure_ascii=False))
        if f"{base.RUN_LABEL}-task-roundtrip.txt" not in json.dumps(listed.get("data", {}), ensure_ascii=False):
            self._fail_case(result, "P2", "file.list 未列出测试文件", "list outputs 应包含刚写入文件。", json.dumps(listed, ensure_ascii=False))
        return result

    def _run_file_delete_deny_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：file delete deny", auto_start=False)
        path = f"outputs/{base.RUN_LABEL}-task-delete-deny.txt"
        write = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.write", "args": {"path": path, "content": "delete deny guard"}})
        delete = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.delete", "args": {"path": path}})
        approval_id = delete.get("data", {}).get("approval", {}).get("approval_id")
        denied = self._request(client, "POST", f"/api/approvals/{approval_id}/deny", json={"reason": f"{base.RUN_LABEL} deny delete"}) if approval_id else {"status_code": 0, "data": {}}
        read_after = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.read", "args": {"path": path}})
        result = self._direct_result(
            "TASK-17",
            "文件任务",
            "文件删除审批拒绝",
            ["file.write", "file.delete", f"POST /api/approvals/{approval_id}/deny", "file.read"],
            {"task": task, "write": write, "delete": delete, "denied": denied, "read_after": read_after},
            "file.delete should require approval and denial should keep file readable",
        )
        if not approval_id:
            self._fail_case(result, "P1", "file.delete 未创建审批", "删除文件应先进入审批。", json.dumps(delete, ensure_ascii=False))
        if denied.get("status_code") != 200:
            self._fail_case(result, "P2", "删除审批拒绝失败", "审批应可拒绝。", json.dumps(denied, ensure_ascii=False))
        if read_after.get("status_code") != 200:
            self._fail_case(result, "P1", "拒绝删除后文件不可读", "拒绝审批后文件应仍可读。", json.dumps(read_after, ensure_ascii=False))
        return result

    def _run_file_escape_write_case(self, client: TestClient) -> base.CaseResult:
        task = self._create_task(client, f"{base.RUN_LABEL} 任务执行：file write escape", auto_start=False)
        escaped = self._request(client, "POST", "/api/tools/execute", json={"task_id": task.get("task_id"), "tool_name": "file.write", "args": {"path": "../escape.txt", "content": "escape"}})
        result = self._direct_result(
            "TASK-18",
            "文件任务",
            "文件路径逃逸拒绝",
            ["POST /api/tools/execute file.write path=../escape.txt"],
            {"task": task, "escaped": escaped},
            "file.write path escape should be denied",
        )
        if escaped.get("status_code") not in {403, 422}:
            self._fail_case(result, "P1", "file.write 路径逃逸未阻断", "写入任务沙箱外路径应被拒绝。", json.dumps(escaped, ensure_ascii=False))
        return result

    def _run_sandbox_status_case(self, client: TestClient) -> base.CaseResult:
        status = self._request(client, "GET", "/api/execution-boundary/sandbox-status")
        contracts = self._request(client, "GET", "/api/system/runtime-contracts")
        result = self._direct_result(
            "TASK-19",
            "系统边界",
            "沙箱状态与运行契约",
            ["GET /api/execution-boundary/sandbox-status", "GET /api/system/runtime-contracts"],
            {"status": status, "contracts": contracts},
            "system boundary status and runtime contracts should be queryable",
        )
        status_data = status.get("data", {})
        if status.get("status_code") != 200 or not status_data.get("active_backend"):
            self._fail_case(result, "P1", "沙箱状态不可查", "sandbox status 应返回 active_backend。", json.dumps(status, ensure_ascii=False))
        names = {item.get("name") for item in contracts.get("data", {}).get("items", [])}
        if contracts.get("status_code") != 200 or "TerminalRunner" not in names:
            self._fail_case(result, "P2", "运行契约缺少 TerminalRunner", "runtime contracts 应包含 TerminalRunner。", json.dumps(contracts, ensure_ascii=False))
        return result

    def _approve_and_execute_tool(
        self,
        client: TestClient,
        payload: dict[str, Any],
        reason: str,
    ) -> dict[str, Any]:
        first = self._request(client, "POST", "/api/tools/execute", json=payload)
        approval_id = first.get("data", {}).get("approval", {}).get("approval_id")
        approved: dict[str, Any] = {"status_code": 0, "data": {"note": "approval not required"}}
        if approval_id:
            approved = self._request(
                client,
                "POST",
                f"/api/approvals/{approval_id}/approve",
                json={"reason": reason},
            )
        executed = self._request(client, "POST", "/api/tools/execute", json={**payload, "approval_id": approval_id}) if approval_id else first
        return {"first": first, "approved": approved, "executed": executed, "approval_id": approval_id}

    def _direct_result(
        self,
        case_id: str,
        category: str,
        title: str,
        inputs: list[str],
        evidence: dict[str, Any],
        expected: str,
    ) -> base.CaseResult:
        return base.CaseResult(
            case_id=case_id,
            category=category,
            title=title,
            status="PASS",
            inputs=inputs,
            actual_reply=json.dumps(base.redact_value(base.compact_evidence(evidence, max_chars=12000)), ensure_ascii=False, indent=2),
            expected=expected,
            evidence=evidence,
        )

    def _add_issue(
        self,
        severity: str,
        case_id: str,
        title: str,
        expected: str,
        actual: str,
        evidence: dict[str, Any] | None = None,
    ) -> base.Issue:
        self.issue_count += 1
        issue = base.Issue(
            issue_id=f"CHAT-E2E-TASK-FIX-{self.issue_count:03d}",
            severity=severity,
            case_id=case_id,
            title=title,
            expected=expected,
            actual=str(base.redact_value(actual)),
            evidence=base.redact_value(evidence or {}),
        )
        self.issues.append(issue)
        return issue

    def _write_outputs(self) -> None:
        TASK_REPORT_PATH.write_text(self._render_task_report(), encoding="utf-8")
        TASK_ISSUES_PATH.write_text(self._render_task_issues(), encoding="utf-8")

    def _render_task_report(self) -> str:
        counts = {
            "PASS": sum(1 for item in self.results if item.status == "PASS"),
            "FAIL": sum(1 for item in self.results if item.status == "FAIL"),
            "BLOCKED": sum(1 for item in self.results if item.status == "BLOCKED"),
        }
        lines = [
            "# 聊天主链路任务执行测试报告",
            "",
            f"- 测试批次：`{base.RUN_LABEL}`",
            f"- 任务执行运行 ID：`{base.RUN_ID}`",
            f"- 数据环境：`{base.ROOT / 'data'}`",
            f"- 用例来源：`{TASK_CASE_DOC_PATH.name}`",
            f"- 预检结果：`{'PASS' if self.preflight.get('passed') else 'FAIL'}`",
            f"- 用例统计：PASS {counts['PASS']} / FAIL {counts['FAIL']} / BLOCKED {counts['BLOCKED']}",
            f"- 待修复问题数：{len(self.issues)}",
            "",
            "## 预检",
            "",
            "````json",
            json.dumps(base.redact_value(self.preflight), ensure_ascii=False, indent=2),
            "````",
            "",
            "## 用例结果",
            "",
        ]
        for result in self.results:
            lines.extend(
                [
                    f"### {result.case_id} {result.title}",
                    "",
                    f"- 分类：{result.category}",
                    f"- 结果：`{result.status}`",
                    f"- 问题：{', '.join(result.issue_ids) if result.issue_ids else '无'}",
                    f"- turn_id：{', '.join(result.turn_ids) if result.turn_ids else '无'}",
                    f"- trace_id：{', '.join(result.trace_ids) if result.trace_ids else '无'}",
                    f"- 事件序列：`{', '.join(result.event_sequence) if result.event_sequence else '无'}`",
                    "",
                    "**输入**",
                    "",
                ]
            )
            for text in result.inputs:
                lines.append(f"- {base.redact_value(text)}")
            lines.extend(
                [
                    "",
                    "**回复/结果**",
                    "",
                    "````text",
                    str(base.redact_value(result.actual_reply)).strip() or "无",
                    "````",
                    "",
                    "**核心证据**",
                    "",
                    "````json",
                    json.dumps(base.redact_value(base.compact_evidence(result.evidence)), ensure_ascii=False, indent=2),
                    "````",
                    "",
                ]
            )
        return "\n".join(lines)

    def _render_task_issues(self) -> str:
        lines = [
            "# 聊天主链路任务执行待修复问题",
            "",
            f"- 测试批次：`{base.RUN_LABEL}`",
            f"- 任务执行运行 ID：`{base.RUN_ID}`",
            f"- 问题总数：{len(self.issues)}",
            "",
        ]
        if not self.issues:
            lines.append("本轮任务执行测试未发现待修复问题。")
            return "\n".join(lines)
        severity_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
        for issue in sorted(self.issues, key=lambda item: (severity_order.get(item.severity, 9), item.issue_id)):
            lines.extend(
                [
                    f"## {issue.issue_id} {issue.title}",
                    "",
                    f"- 严重级别：`{issue.severity}`",
                    f"- 关联用例：`{issue.case_id}`",
                    f"- 期望：{issue.expected}",
                    f"- 实际：{issue.actual}",
                    "",
                    "````json",
                    json.dumps(base.compact_evidence(issue.evidence), ensure_ascii=False, indent=2),
                    "````",
                    "",
                ]
            )
        return "\n".join(lines)

    def _create_task(
        self,
        client: TestClient,
        goal: str,
        *,
        constraints: dict[str, Any] | None = None,
        auto_start: bool = False,
    ) -> dict[str, Any]:
        self.task_sequence += 1
        response = self._request(
            client,
            "POST",
            "/api/tasks",
            json={
                "goal": goal,
                "mode_hint": "workflow",
                "constraints": constraints or {},
                "auto_start": auto_start,
                "client_request_id": f"{base.RUN_LABEL}:{base.RUN_ID}:task-exec-{self.task_sequence:03d}",
            },
        )
        return response.get("data", {"error": response})


def _tool_call_id(response: dict[str, Any]) -> str | None:
    return response.get("data", {}).get("tool_call", {}).get("tool_call_id")


def _restore_env(name: str, value: str | None) -> None:
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value


def main() -> None:
    TaskExecutionRunner().run()
    print(f"Report: {TASK_REPORT_PATH}")
    print(f"Issues: {TASK_ISSUES_PATH}")


if __name__ == "__main__":
    main()
