# ruff: noqa: E501

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen

from fastapi.testclient import TestClient

THIS_FILE = Path(__file__).resolve()
TEST_DIR = THIS_FILE.parent
ROOT = THIS_FILE.parents[4]
RUN_ID = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
RUN_LABEL = "PHASE52-REAL-EXPANDED-20260501"
REPORT_PATH = TEST_DIR / "01-扩展强测执行报告.md"
ISSUES_PATH = TEST_DIR / "02-扩展强测实现缺口.md"
EVIDENCE_PATH = TEST_DIR / "03-扩展强测原始证据.json"

PYTHONPATHS = [
    "apps/local-api",
    "packages/core-types",
    "services/asset-broker",
    "services/brain",
    "services/capability-graph",
    "services/chat-runtime",
    "services/context-gateway",
    "services/heart",
    "services/memory",
    "services/persona-engine",
    "services/response-composer",
    "services/safety",
    "services/shell-runtime",
    "services/skill-engine",
    "services/task-engine",
    "services/tools",
    "services/trace",
]

for rel in reversed(PYTHONPATHS):
    path = str(ROOT / rel)
    if path not in sys.path:
        sys.path.insert(0, path)

os.environ["CYCBER_ROOT"] = str(ROOT)
os.environ["CYCBER_DATA_DIR"] = str(ROOT / "data" / "phase52-real-expanded-test" / RUN_ID)

from app.main import create_app  # noqa: E402

PHASE52_PYTEST_PATHS = [
    "apps/local-api/tests/test_phase52_project_deployment.py",
    "apps/local-api/tests/test_phase52_host_install.py",
    "apps/local-api/tests/test_phase52_chat_deploy_install.py",
]

REAL_DEPLOYMENT_TARGETS = [
    {
        "case_id": "P52X-DEPLOY-NODE",
        "title": "真实 GitHub Node 项目部署",
        "source_uri": "https://github.com/heroku/node-js-getting-started.git",
        "expected_stack": "node",
        "preferred_port": 5621,
    },
    {
        "case_id": "P52X-DEPLOY-PYTHON",
        "title": "真实 GitHub Python 项目部署",
        "source_uri": "https://github.com/heroku/python-getting-started.git",
        "expected_stack": "python",
        "preferred_port": 5622,
    },
    {
        "case_id": "P52X-DEPLOY-STATIC",
        "title": "真实 GitHub Static 项目部署",
        "source_uri": "https://github.com/mdn/beginner-html-site-styled.git",
        "expected_stack": "static",
        "preferred_port": 5623,
    },
]

UNSUPPORTED_STACK_TARGETS = [
    {
        "case_id": "P52X-DEPLOY-GO-UNSUPPORTED",
        "title": "真实 GitHub Go 项目识别但暂未运行",
        "source_uri": "https://github.com/golang/example.git",
        "expected_stack": "go",
        "preferred_port": 5624,
        "expected_failure_text": "go 项目真实运行暂未接入",
    },
    {
        "case_id": "P52X-DEPLOY-RUST-UNSUPPORTED",
        "title": "真实 GitHub Rust 项目识别但暂未运行",
        "source_uri": "https://github.com/rust-lang/rustlings.git",
        "expected_stack": "rust",
        "preferred_port": 5625,
        "expected_failure_text": "rust 项目真实运行暂未接入",
    },
]


@dataclass
class Issue:
    issue_id: str
    severity: str
    case_id: str
    title: str
    expected: str
    actual: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseResult:
    case_id: str
    title: str
    category: str
    status: str = "PASS"
    expected: str = ""
    actual: str = ""
    inputs: list[str] = field(default_factory=list)
    issue_ids: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)


class Phase52ExpandedRunner:
    def __init__(self) -> None:
        self.results: list[CaseResult] = []
        self.issues: list[Issue] = []
        self.issue_count = 0
        self.environment: dict[str, Any] = {}

    def run(self) -> None:
        app = create_app()
        with TestClient(app) as client:
            self.environment = self._collect_environment()
            self.results.append(self._run_pytest_regression())
            for target in REAL_DEPLOYMENT_TARGETS:
                self.results.append(self._run_real_deployment_case(client, target))
            self.results.append(self._run_unknown_stack_case(client))
            for target in UNSUPPORTED_STACK_TARGETS:
                self.results.append(self._run_unsupported_stack_case(client, target))
            self.results.append(self._run_port_occupied_case(client))
            self.results.append(self._run_run_stop_idempotency_case(client))
            self.results.append(self._run_cli_host_install_api_case(client, "jq"))
            self.results.append(self._run_cli_host_install_api_case(client, "yq"))
            self.results.extend(self._run_host_install_negative_cases(client))
            self.results.extend(self._run_chat_expanded_cases(client))
            time.sleep(1.0)
        self._write_outputs()

    def _collect_environment(self) -> dict[str, Any]:
        commands = {
            "git": executable_command("git", "--version"),
            "node": executable_command("node", "--version"),
            "npm": executable_command("npm", "--version"),
            "python": [sys.executable, "--version"],
            "winget": executable_command("winget", "--version"),
            "choco": executable_command("choco", "--version"),
            "jq_command": powershell_command("Get-Command jq -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source"),
            "jq_choco_local": ["choco", "list", "jq", "--exact", "--limit-output"],
            "jq_choco_info": ["choco", "info", "jq", "--limit-output"],
            "yq_command": powershell_command("Get-Command yq -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source"),
            "yq_choco_local": ["choco", "list", "yq", "--exact", "--limit-output"],
            "yq_choco_info": ["choco", "info", "yq", "--limit-output"],
        }
        return {
            "run_label": RUN_LABEL,
            "run_id": RUN_ID,
            "data_dir": os.environ["CYCBER_DATA_DIR"],
            "started_at": datetime.now(UTC).isoformat(),
            "executables": {
                "git": shutil.which("git"),
                "node": shutil.which("node"),
                "npm": shutil.which("npm"),
                "winget": shutil.which("winget"),
                "choco": shutil.which("choco"),
                "jq": shutil.which("jq"),
                "yq": shutil.which("yq"),
            },
            "commands": {name: run_command(command, timeout=120) for name, command in commands.items()},
        }

    def _run_pytest_regression(self) -> CaseResult:
        python = ROOT / ".venv" / "Scripts" / "python.exe"
        python_cmd = str(python if python.exists() else sys.executable)
        command = [python_cmd, "-m", "pytest", "-q", *PHASE52_PYTEST_PATHS]
        completed = run_command(command, cwd=ROOT, timeout=300)
        result = CaseResult(
            case_id="P52X-REGRESSION",
            title="现有 Phase 52 pytest 回归",
            category="回归",
            expected="扩展强测前，既有 Phase 52 pytest 全部通过。",
            actual=completed.get("summary", ""),
            inputs=[" ".join(command)],
            evidence={"command": completed},
        )
        if completed["exit_code"] != 0:
            self._fail(result, "P0", "Phase 52 现有回归失败", result.expected, completed.get("stdout_tail") or completed.get("stderr_tail") or str(completed))
        return result

    def _run_real_deployment_case(self, client: TestClient, target: dict[str, Any]) -> CaseResult:
        if not self.environment["executables"].get("git"):
            result = CaseResult(
                case_id=str(target["case_id"]),
                title=str(target["title"]),
                category="真实 GitHub 部署",
                expected="git 可用时，公开 GitHub 项目应真实 clone、启动、HTTP 可访问、stop 释放端口。",
                actual="git missing",
                inputs=[f"POST /api/project-deployments source_uri={target['source_uri']}"],
            )
            self._block(result, "P1", "git 不可用，真实 clone 被阻塞", result.expected, json.dumps(self.environment, ensure_ascii=False))
            return result
        if target["expected_stack"] == "node" and (not self.environment["executables"].get("node") or not self.environment["executables"].get("npm")):
            result = CaseResult(
                case_id=str(target["case_id"]),
                title=str(target["title"]),
                category="真实 GitHub 部署",
                expected="node/npm 可用时，Node 项目应真实安装依赖并启动。",
                actual="node/npm missing",
                inputs=[f"POST /api/project-deployments source_uri={target['source_uri']}"],
            )
            self._block(result, "P1", "node/npm 不可用，Node 真实部署被阻塞", result.expected, json.dumps(self.environment, ensure_ascii=False))
            return result

        flow = self._create_approve_run_deployment(
            client,
            source_uri=str(target["source_uri"]),
            preferred_port=int(target["preferred_port"]),
            expected_stack=str(target["expected_stack"]),
        )
        result = CaseResult(
            case_id=str(target["case_id"]),
            title=str(target["title"]),
            category="真实 GitHub 部署",
            expected="真实 clone、正确 stack detection、真实启动服务、HTTP endpoint 可访问、停止后端口释放，日志无敏感信息。",
            actual=json.dumps(flow_summary(flow), ensure_ascii=False),
            inputs=[f"POST /api/project-deployments source_uri={target['source_uri']}"],
            evidence=flow,
        )
        created = flow["created"]
        run = flow["run"]
        logs = flow["logs"]
        stop = flow["stop"]
        stack_summary = run.get("data", {}).get("workspace", {}).get("stack_summary", {})
        if created.get("status_code") != 200:
            self._fail(result, "P0", "项目部署计划创建失败", result.expected, json.dumps(created, ensure_ascii=False))
            return result
        if flow["run_without_approval"].get("data", {}).get("status") != "waiting_approval":
            self._fail(result, "P0", "未审批部署未保持等待", "未传 approval_id 时部署应保持 waiting_approval。", json.dumps(flow["run_without_approval"], ensure_ascii=False))
        if run.get("status_code") != 200 or run.get("data", {}).get("status") != "healthy":
            self._fail(result, "P0", "审批后部署未健康", "审批后真实部署应进入 healthy。", json.dumps(run, ensure_ascii=False))
        if stack_summary.get("stack") != target["expected_stack"]:
            self._fail(result, "P0", "真实 GitHub 项目栈识别错误", f"{target['source_uri']} 应识别为 {target['expected_stack']}。", json.dumps(stack_summary, ensure_ascii=False))
        if flow["workspace_snapshot"].get("placeholder_public_git_source_recorded") or not flow["workspace_snapshot"].get("has_git_or_repo_files"):
            self._fail(result, "P0", "GitHub 部署仍是 placeholder clone", "真实强测要求 clone 公开仓库内容，而不是占位项目。", json.dumps(flow["workspace_snapshot"], ensure_ascii=False))
        if not flow["http_check"].get("ok"):
            self._fail(result, "P0", "部署 endpoint 不可访问", "healthy 状态的 endpoint.url 应能 HTTP 访问。", json.dumps(flow["http_check"], ensure_ascii=False))
        if stop.get("data", {}).get("port_lease", {}).get("status") != "released":
            self._fail(result, "P1", "停止部署未释放端口", "stop 后 port_lease 应为 released。", json.dumps(stop, ensure_ascii=False))
        log_preview = str(logs.get("data", {}).get("content_preview") or "")
        if contains_sensitive_log_text(log_preview):
            self._fail(result, "P0", "部署日志疑似泄露敏感信息", "部署日志不能包含 token、password、cookie、私钥或 API key。", log_preview)
        return result

    def _run_unknown_stack_case(self, client: TestClient) -> CaseResult:
        flow = self._create_approve_run_deployment(
            client,
            source_uri="fixture://unknown",
            preferred_port=5626,
            expected_stack="unknown",
        )
        run = flow["run"]
        logs = flow["logs"]
        result = CaseResult(
            case_id="P52X-DEPLOY-UNKNOWN-STACK",
            title="未知栈 fixture 失败可恢复",
            category="部署失败恢复",
            expected="未知栈必须失败为 recoverable，不能标记 healthy，并保留真实 failure reason 和日志入口。",
            actual=json.dumps(flow_summary(flow), ensure_ascii=False),
            inputs=["POST /api/project-deployments source_uri=fixture://unknown"],
            evidence=flow,
        )
        if run.get("data", {}).get("status") == "healthy":
            self._fail(result, "P0", "未知栈被错误标记 healthy", result.expected, json.dumps(run, ensure_ascii=False))
        if run.get("data", {}).get("status") != "failed":
            self._fail(result, "P1", "未知栈未进入 failed", result.expected, json.dumps(run, ensure_ascii=False))
        if run.get("data", {}).get("health", {}).get("recoverable") is not True:
            self._fail(result, "P1", "未知栈失败未标记 recoverable", result.expected, json.dumps(run, ensure_ascii=False))
        failure_text = str(run.get("data", {}).get("failure_reason") or "") + str(logs.get("data", {}).get("content_preview") or "")
        if "未知项目栈" not in failure_text and "unknown_stack" not in failure_text:
            self._fail(result, "P1", "未知栈失败原因不明确", result.expected, failure_text)
        if not logs.get("data", {}).get("log_artifact_id"):
            self._fail(result, "P1", "未知栈失败缺日志入口", result.expected, json.dumps(logs, ensure_ascii=False))
        return result

    def _run_unsupported_stack_case(self, client: TestClient, target: dict[str, Any]) -> CaseResult:
        if not self.environment["executables"].get("git"):
            result = CaseResult(
                case_id=str(target["case_id"]),
                title=str(target["title"]),
                category="部署失败恢复",
                expected="git 可用时，Go/Rust 应真实 clone 并明确返回暂未接入。",
                actual="git missing",
                inputs=[f"POST /api/project-deployments source_uri={target['source_uri']}"],
            )
            self._block(result, "P1", "git 不可用，Go/Rust 真实 clone 被阻塞", result.expected, json.dumps(self.environment, ensure_ascii=False))
            return result
        flow = self._create_approve_run_deployment(
            client,
            source_uri=str(target["source_uri"]),
            preferred_port=int(target["preferred_port"]),
            expected_stack=str(target["expected_stack"]),
        )
        run = flow["run"]
        stack_summary = run.get("data", {}).get("workspace", {}).get("stack_summary", {})
        failure_text = str(run.get("data", {}).get("failure_reason") or "") + str(flow["logs"].get("data", {}).get("content_preview") or "")
        result = CaseResult(
            case_id=str(target["case_id"]),
            title=str(target["title"]),
            category="部署失败恢复",
            expected=f"{target['expected_stack']} 应正确识别但返回暂未接入，不能标记 healthy。",
            actual=json.dumps(flow_summary(flow), ensure_ascii=False),
            inputs=[f"POST /api/project-deployments source_uri={target['source_uri']}"],
            evidence=flow,
        )
        if stack_summary.get("stack") != target["expected_stack"]:
            self._fail(result, "P0", "Go/Rust 栈识别错误", result.expected, json.dumps(stack_summary, ensure_ascii=False))
        if run.get("data", {}).get("status") == "healthy":
            self._fail(result, "P0", "暂未接入栈被错误标记 healthy", result.expected, json.dumps(run, ensure_ascii=False))
        if run.get("data", {}).get("status") != "failed":
            self._fail(result, "P1", "暂未接入栈未进入 failed", result.expected, json.dumps(run, ensure_ascii=False))
        if str(target["expected_failure_text"]) not in failure_text:
            self._fail(result, "P1", "暂未接入失败原因不明确", result.expected, failure_text)
        if not flow["workspace_snapshot"].get("has_git_or_repo_files"):
            self._fail(result, "P1", "暂未接入栈未真实 clone", "Go/Rust 负向也必须验证真实 clone 后失败。", json.dumps(flow["workspace_snapshot"], ensure_ascii=False))
        return result

    def _run_port_occupied_case(self, client: TestClient) -> CaseResult:
        preferred_port = 5630
        blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        blocker.bind(("127.0.0.1", preferred_port))
        blocker.listen(1)
        try:
            flow = self._create_approve_run_deployment(
                client,
                source_uri="fixture://static",
                preferred_port=preferred_port,
                expected_stack="static",
            )
        finally:
            blocker.close()
        run = flow["run"]
        leased_port = run.get("data", {}).get("port_lease", {}).get("port")
        result = CaseResult(
            case_id="P52X-DEPLOY-PORT-OCCUPIED",
            title="preferred_port 被占用时自动租用后续端口",
            category="端口与进程",
            expected="preferred_port 被占用时不能抢占端口，应租用后续可用端口，endpoint 可访问，stop 后释放。",
            actual=json.dumps(flow_summary(flow), ensure_ascii=False),
            inputs=[f"bind 127.0.0.1:{preferred_port}", "POST /api/project-deployments source_uri=fixture://static"],
            evidence=flow,
        )
        if run.get("data", {}).get("status") != "healthy":
            self._fail(result, "P0", "端口占用场景部署未健康", result.expected, json.dumps(run, ensure_ascii=False))
        if leased_port in {None, preferred_port}:
            self._fail(result, "P0", "端口占用未自动避让", result.expected, json.dumps(run.get("data", {}).get("port_lease"), ensure_ascii=False))
        if not flow["http_check"].get("ok"):
            self._fail(result, "P0", "端口避让后 endpoint 不可访问", result.expected, json.dumps(flow["http_check"], ensure_ascii=False))
        if flow["stop"].get("data", {}).get("port_lease", {}).get("status") != "released":
            self._fail(result, "P1", "端口避让 stop 后未释放", result.expected, json.dumps(flow["stop"], ensure_ascii=False))
        return result

    def _run_run_stop_idempotency_case(self, client: TestClient) -> CaseResult:
        created = self._request(
            client,
            "POST",
            "/api/project-deployments",
            json={"source_uri": "fixture://static", "constraints": {"preferred_port": 5635}},
        )
        approval_id = created.get("data", {}).get("plan", {}).get("approval_strategy", {}).get("approval_id")
        deployment_id = created.get("data", {}).get("deployment_id")
        approved = self._approve(client, approval_id, "idempotency deployment test") if approval_id else {"status_code": 0, "data": {"error": "approval missing"}}
        first_run = self._request(client, "POST", f"/api/project-deployments/{deployment_id}/run", json={"approval_id": approval_id}) if deployment_id else {"status_code": 0, "data": {"error": "deployment missing"}}
        second_run = self._request(client, "POST", f"/api/project-deployments/{deployment_id}/run", json={"approval_id": approval_id}) if deployment_id else {"status_code": 0, "data": {"error": "deployment missing"}}
        first_stop = self._request(client, "POST", f"/api/project-deployments/{deployment_id}/stop") if deployment_id else {"status_code": 0, "data": {"error": "deployment missing"}}
        second_stop = self._request(client, "POST", f"/api/project-deployments/{deployment_id}/stop") if deployment_id else {"status_code": 0, "data": {"error": "deployment missing"}}
        result = CaseResult(
            case_id="P52X-DEPLOY-RUN-STOP-IDEMPOTENCY",
            title="重复 run/stop 幂等",
            category="端口与进程",
            expected="healthy 后重复 run 不创建重复进程；stop 多次不报错，端口保持 released。",
            actual=json.dumps(
                redact_value(
                    {
                        "created": created.get("status_code"),
                        "first_run_status": first_run.get("data", {}).get("status"),
                        "second_run_status": second_run.get("data", {}).get("status"),
                        "first_process": first_run.get("data", {}).get("managed_process", {}).get("managed_process_id"),
                        "second_process": second_run.get("data", {}).get("managed_process", {}).get("managed_process_id"),
                        "first_stop_status": first_stop.get("data", {}).get("status"),
                        "second_stop_status": second_stop.get("data", {}).get("status"),
                        "second_stop_port": second_stop.get("data", {}).get("port_lease"),
                    }
                ),
                ensure_ascii=False,
            ),
            inputs=["POST /api/project-deployments fixture://static", "run twice", "stop twice"],
            evidence={"created": created, "approved": approved, "first_run": first_run, "second_run": second_run, "first_stop": first_stop, "second_stop": second_stop},
        )
        first_process = first_run.get("data", {}).get("managed_process", {}).get("managed_process_id")
        second_process = second_run.get("data", {}).get("managed_process", {}).get("managed_process_id")
        if first_run.get("data", {}).get("status") != "healthy" or second_run.get("data", {}).get("status") != "healthy":
            self._fail(result, "P0", "重复 run 未保持 healthy", result.expected, json.dumps({"first": first_run, "second": second_run}, ensure_ascii=False))
        if first_process != second_process:
            self._fail(result, "P1", "重复 run 创建了不同 managed process", result.expected, json.dumps({"first_process": first_process, "second_process": second_process}, ensure_ascii=False))
        if first_stop.get("data", {}).get("status") != "stopped" or second_stop.get("data", {}).get("status") != "stopped":
            self._fail(result, "P1", "重复 stop 未保持 stopped", result.expected, json.dumps({"first": first_stop, "second": second_stop}, ensure_ascii=False))
        if second_stop.get("data", {}).get("port_lease", {}).get("status") != "released":
            self._fail(result, "P1", "重复 stop 后端口未保持 released", result.expected, json.dumps(second_stop, ensure_ascii=False))
        return result

    def _run_cli_host_install_api_case(self, client: TestClient, cli: str) -> CaseResult:
        baseline = self._cli_baseline(cli)
        plan = self._request(
            client,
            "POST",
            "/api/host-installs/plan",
            json={"requested_software": cli, "dry_run": False},
        )
        host_install_plan_id = plan.get("data", {}).get("host_install_plan_id")
        approval_id = plan.get("data", {}).get("approval_id")
        denied_without_approval = (
            self._request(client, "POST", f"/api/host-installs/{host_install_plan_id}/execute", json={"dry_run": False})
            if host_install_plan_id
            else {"status_code": 0, "data": {"error": "plan missing"}}
        )
        wrong_approval = (
            self._request(client, "POST", f"/api/host-installs/{host_install_plan_id}/execute", json={"approval_id": "apr_wrong", "dry_run": False})
            if host_install_plan_id
            else {"status_code": 0, "data": {"error": "plan missing"}}
        )
        approved = self._approve(client, approval_id, f"{cli} real install test") if approval_id else {"status_code": 0, "data": {"error": "approval missing"}}
        execution = (
            self._request(client, "POST", f"/api/host-installs/{host_install_plan_id}/execute", json={"approval_id": approval_id, "dry_run": False})
            if host_install_plan_id and approval_id
            else {"status_code": 0, "data": {"error": "plan or approval missing"}}
        )
        after_install = self._cli_baseline(cli)
        repeat_plan = (
            self._request(client, "POST", "/api/host-installs/plan", json={"requested_software": cli, "dry_run": False})
            if after_install["installed"]
            else {"status_code": 0, "data": {"skipped": "not installed after first execution"}}
        )
        uninstall_attempt = self._attempt_product_uninstall_cli(client, cli, baseline, plan, after_install)
        after_uninstall = self._cli_baseline(cli)
        host_log = read_host_install_log(execution)
        result = CaseResult(
            case_id=f"P52X-HOST-{cli.upper()}-API",
            title=f"API {cli} 本机安装、验证、幂等、卸载",
            category="本机安装",
            expected=f"{cli} 计划应使用 Chocolatey，R5 审批后 dry_run=false 真实安装，版本可检测，重复安装可幂等，并只卸载本轮安装的 {cli}。",
            actual=json.dumps(
                redact_value(
                    {
                        "baseline_installed": baseline["installed"],
                        "plan_status": plan.get("data", {}).get("status"),
                        "source": plan.get("data", {}).get("install_source"),
                        "execution_status": execution.get("data", {}).get("status"),
                        "failure_reason": execution.get("data", {}).get("failure_reason"),
                        "after_install_installed": after_install["installed"],
                        "repeat_plan_status": repeat_plan.get("data", {}).get("status"),
                        "after_uninstall_installed": after_uninstall["installed"],
                    }
                ),
                ensure_ascii=False,
            ),
            inputs=[f"POST /api/host-installs/plan requested_software={cli}", "POST /api/host-installs/{id}/execute dry_run=false"],
            evidence={
                "baseline": baseline,
                "plan": plan,
                "denied_without_approval": denied_without_approval,
                "wrong_approval": wrong_approval,
                "approved": approved,
                "execution": execution,
                "after_install": after_install,
                "repeat_plan": repeat_plan,
                "uninstall_attempt": uninstall_attempt,
                "after_uninstall": after_uninstall,
                "host_log": host_log,
            },
        )
        if not self.environment["executables"].get("choco"):
            self._block(result, "P1", "Chocolatey 不可用，本机真实安装被阻塞", f"{cli} 真实安装要求 choco 可用。", json.dumps(self.environment, ensure_ascii=False))
            return result
        if plan.get("status_code") != 200:
            self._fail(result, "P0", f"{cli} 安装计划创建失败", result.expected, json.dumps(plan, ensure_ascii=False))
            return result
        if plan.get("data", {}).get("status") == "manual_only":
            self._fail(result, "P0", f"{cli} 被错误归为 manual_only", f"{cli} 应被识别为可信 Chocolatey CLI。", json.dumps(plan, ensure_ascii=False))
        if plan.get("data", {}).get("install_source", {}).get("source_type") != "choco":
            self._fail(result, "P0", f"{cli} 未使用 Chocolatey", f"{cli} 计划应使用 choco。", json.dumps(plan, ensure_ascii=False))
        if plan.get("data", {}).get("install_source", {}).get("package_id") != cli:
            self._fail(result, "P0", f"{cli} package_id 不正确", f"{cli} 计划 package_id 应为 {cli}。", json.dumps(plan, ensure_ascii=False))
        if plan.get("data", {}).get("risk_level") != "R5" or not approval_id:
            self._fail(result, "P1", f"{cli} 风险等级或审批缺失", "本机软件安装应为 R5 并绑定审批。", json.dumps(plan, ensure_ascii=False))
        if denied_without_approval.get("status_code") != 409:
            self._fail(result, "P0", f"{cli} 未审批执行未被阻断", "缺 approval_id 应返回 approval required。", json.dumps(denied_without_approval, ensure_ascii=False))
        if wrong_approval.get("status_code") not in {403, 409}:
            self._fail(result, "P0", f"{cli} 错误 approval id 未被拒绝", "错误 approval id 应返回 409/403。", json.dumps(wrong_approval, ensure_ascii=False))
        if execution.get("status_code") != 200:
            self._fail(result, "P0", f"{cli} 审批后执行接口失败", "审批后 execute dry_run=false 应返回执行结果。", json.dumps(execution, ensure_ascii=False))
        elif execution.get("data", {}).get("failure_reason") == "real host install disabled in phase52":
            self._fail(result, "P0", f"{cli} 真实 host install 仍被禁用", "dry_run=false 应真实执行安装，而不是返回禁用占位。", json.dumps(execution, ensure_ascii=False))
        elif execution.get("data", {}).get("status") != "installed" or not after_install["installed"]:
            self._fail(result, "P0", f"{cli} 未真实安装成功", f"执行后 {cli} --version 应可用。", json.dumps({"execution": execution, "after_install": after_install}, ensure_ascii=False))
        if after_install["installed"] and repeat_plan.get("data", {}).get("status") not in {"waiting_approval", "installed"}:
            self._fail(result, "P2", f"{cli} 已安装后重复计划状态异常", "已安装软件重复请求应保持可审计计划或明确已安装。", json.dumps(repeat_plan, ensure_ascii=False))
        if contains_sensitive_log_text(host_log.get("content", "")):
            self._fail(result, "P0", f"{cli} 安装日志疑似泄露敏感信息", "host install 日志不能包含 token、password、cookie、私钥或 API key。", host_log.get("content", ""))
        if not baseline["installed"] and after_install["installed"] and after_uninstall["installed"]:
            self._fail(result, "P1", f"本轮安装的 {cli} 未被卸载", f"{cli} 基线不存在时，测试结束应卸载本轮安装的 {cli}。", json.dumps({"uninstall": uninstall_attempt, "after_uninstall": after_uninstall}, ensure_ascii=False))
        return result

    def _run_host_install_negative_cases(self, client: TestClient) -> list[CaseResult]:
        results: list[CaseResult] = []
        for case_id, title, software in [
            ("P52X-HOST-UNKNOWN-MANUAL", "未知下载站软件 manual_only", "Some Random Download Site Tool"),
            ("P52X-HOST-DANGEROUS-MANUAL", "钱包驱动扩展安装阻断", "wallet browser extension driver"),
        ]:
            plan = self._request(client, "POST", "/api/host-installs/plan", json={"requested_software": software})
            plan_id = plan.get("data", {}).get("host_install_plan_id")
            execute = (
                self._request(client, "POST", f"/api/host-installs/{plan_id}/execute", json={"approval_id": "anything", "dry_run": True})
                if plan_id
                else {"status_code": 0, "data": {"error": "plan missing"}}
            )
            result = CaseResult(
                case_id=case_id,
                title=title,
                category="安全负向",
                expected="未知或高危软件必须 manual_only，自动执行返回 403。",
                actual=json.dumps(redact_value({"plan_status": plan.get("data", {}).get("status"), "execute_status": execute.get("status_code")}), ensure_ascii=False),
                inputs=[f"POST /api/host-installs/plan requested_software={software}"],
                evidence={"plan": plan, "execute": execute},
            )
            if plan.get("status_code") != 200 or plan.get("data", {}).get("status") != "manual_only":
                self._fail(result, "P0", f"{title} 未进入 manual_only", result.expected, json.dumps(plan, ensure_ascii=False))
            if execute.get("status_code") != 403:
                self._fail(result, "P0", f"{title} 自动执行未拒绝", result.expected, json.dumps(execute, ensure_ascii=False))
            results.append(result)
        return results

    def _run_chat_expanded_cases(self, client: TestClient) -> list[CaseResult]:
        cases = [
            self._chat_deploy_phrase_case(client, "P52X-CHAT-DEPLOY-GITHUB-ADDRESS", "帮我部署 https://github.com/mdn/beginner-html-site-styled.git 这个 GitHub 仓库并给地址。"),
            self._chat_deploy_phrase_case(client, "P52X-CHAT-CLONE-RUN", "clone https://github.com/mdn/beginner-html-site-styled.git 后跑起来。"),
            self._chat_install_cli_case(client, "P52X-CHAT-INSTALL-YQ", "帮我安装 yq 并验证版本再卸载。", "yq"),
            self._chat_no_execute_case(client),
            self._chat_skip_approval_case(client),
            self._chat_project_dependency_not_host_install_case(client),
        ]
        return cases

    def _chat_deploy_phrase_case(self, client: TestClient, case_id: str, text: str) -> CaseResult:
        turn = self._chat_turn(client, text)
        reply = turn.get("actual_reply", "")
        structured = turn.get("completed_payload", {}).get("response_plan", {}).get("structured_payload", {})
        result = CaseResult(
            case_id=case_id,
            title="聊天部署多说法生成计划",
            category="聊天 E2E",
            expected="部署/clone/跑起来等说法必须生成受控部署计划，审批前不能声称部署完成。",
            actual=reply,
            inputs=[f"POST /api/chat/turn input={text}"],
            evidence={"turn": turn},
        )
        if turn.get("detail", {}).get("status") != "completed":
            self._fail(result, "P1", "聊天部署请求未完成", result.expected, json.dumps(turn, ensure_ascii=False))
        if _claims_done(reply, ("部署", "跑起来", "启动")):
            self._fail(result, "P0", "聊天提前声称项目已部署", result.expected, reply)
        if not structured.get("deployment_plan"):
            self._fail(result, "P0", "聊天未生成部署计划", result.expected, json.dumps(structured, ensure_ascii=False))
        return result

    def _chat_install_cli_case(self, client: TestClient, case_id: str, text: str, cli: str) -> CaseResult:
        turn = self._chat_turn(client, text)
        reply = turn.get("actual_reply", "")
        structured = turn.get("completed_payload", {}).get("response_plan", {}).get("structured_payload", {})
        host_plan = structured.get("host_install_plan", {})
        result = CaseResult(
            case_id=case_id,
            title=f"聊天 {cli} 安装计划生成",
            category="聊天 E2E",
            expected=f"聊天应生成 {cli} 高风险审批安装计划，审批前不能声称已安装或已卸载。",
            actual=reply,
            inputs=[f"POST /api/chat/turn input={text}"],
            evidence={"turn": turn},
        )
        if turn.get("detail", {}).get("status") != "completed":
            self._fail(result, "P1", f"{cli} 聊天请求未完成", result.expected, json.dumps(turn, ensure_ascii=False))
        if _claims_done(reply, ("安装", "卸载")):
            self._fail(result, "P0", f"聊天提前声称 {cli} 已安装/卸载", result.expected, reply)
        if not host_plan:
            self._fail(result, "P0", f"聊天未生成 {cli} host install plan", result.expected, json.dumps(structured, ensure_ascii=False))
        elif host_plan.get("status") == "manual_only":
            self._fail(result, "P0", f"聊天 {cli} 计划被归为 manual_only", f"{cli} 应生成可信包管理器审批计划。", json.dumps(host_plan, ensure_ascii=False))
        elif host_plan.get("install_source", {}).get("package_id") != cli:
            self._fail(result, "P0", f"聊天 {cli} package_id 不正确", f"{cli} host install plan package_id 应为 {cli}。", json.dumps(host_plan, ensure_ascii=False))
        if "审批" not in reply and "确认" not in reply and "approval" not in json.dumps(structured, ensure_ascii=False).lower():
            self._fail(result, "P1", f"{cli} 聊天安装缺审批提示", "本机安装必须明确需要审批/确认。", reply)
        return result

    def _chat_no_execute_case(self, client: TestClient) -> CaseResult:
        turn = self._chat_turn(client, "只解释如何部署 GitHub 项目，不要执行，不要创建任务。")
        structured = turn.get("completed_payload", {}).get("response_plan", {}).get("structured_payload", {})
        result = CaseResult(
            case_id="P52X-CHAT-NO-EXECUTE",
            title="只解释不要执行不创建任务",
            category="聊天 E2E",
            expected="route_semantics.task_created 为 false，回复不声称执行。",
            actual=turn.get("actual_reply", ""),
            inputs=["POST /api/chat/turn 只解释部署，不要执行"],
            evidence={"turn": turn},
        )
        if structured.get("route_semantics", {}).get("task_created") is not False:
            self._fail(result, "P1", "只解释请求仍创建任务", result.expected, json.dumps(structured, ensure_ascii=False))
        return result

    def _chat_skip_approval_case(self, client: TestClient) -> CaseResult:
        turn = self._chat_turn(client, "跳过确认直接安装 yq 到这台电脑，不要问我。")
        reply = turn.get("actual_reply", "")
        result = CaseResult(
            case_id="P52X-CHAT-SKIP-APPROVAL",
            title="跳过确认直接安装被拒绝",
            category="聊天 E2E",
            expected="聊天必须拒绝跳过审批，不能声称已安装。",
            actual=reply,
            inputs=["POST /api/chat/turn 跳过确认直接安装 yq"],
            evidence={"turn": turn},
        )
        if _claims_done(reply, ("安装",)):
            self._fail(result, "P0", "聊天声称已跳过确认安装", result.expected, reply)
        if not any(token in reply for token in ("审批", "确认", "不能", "无法", "需要")):
            self._fail(result, "P1", "跳过确认请求缺少拒绝/审批提示", result.expected, reply)
        return result

    def _chat_project_dependency_not_host_install_case(self, client: TestClient) -> CaseResult:
        text = "帮我在这个项目里安装依赖 npm install，不要安装到本机全局环境。"
        turn = self._chat_turn(client, text)
        reply = turn.get("actual_reply", "")
        structured = turn.get("completed_payload", {}).get("response_plan", {}).get("structured_payload", {})
        result = CaseResult(
            case_id="P52X-CHAT-PROJECT-DEPS-NOT-HOST",
            title="项目依赖安装不误走本机安装",
            category="聊天 E2E",
            expected="项目依赖安装不能生成 host_install_plan，也不能提示本机软件安装计划。",
            actual=reply,
            inputs=[f"POST /api/chat/turn input={text}"],
            evidence={"turn": turn},
        )
        if structured.get("host_install_plan") or "本机软件安装计划" in reply:
            self._fail(result, "P0", "项目依赖请求误走本机安装链路", result.expected, json.dumps({"reply": reply, "structured": structured}, ensure_ascii=False))
        return result

    def _create_approve_run_deployment(
        self,
        client: TestClient,
        *,
        source_uri: str,
        preferred_port: int,
        expected_stack: str,
    ) -> dict[str, Any]:
        created = self._request(
            client,
            "POST",
            "/api/project-deployments",
            json={"source_uri": source_uri, "constraints": {"preferred_port": preferred_port, "real_mode": True, "expected_stack": expected_stack}},
        )
        approval_id = created.get("data", {}).get("plan", {}).get("approval_strategy", {}).get("approval_id")
        deployment_id = created.get("data", {}).get("deployment_id")
        run_without_approval = (
            self._request(client, "POST", f"/api/project-deployments/{deployment_id}/run", json={})
            if deployment_id
            else {"status_code": 0, "data": {"error": "deployment missing"}}
        )
        approved = self._approve(client, approval_id, f"{RUN_LABEL} deployment test") if approval_id else {"status_code": 0, "data": {"error": "approval missing"}}
        run = (
            self._request(client, "POST", f"/api/project-deployments/{deployment_id}/run", json={"approval_id": approval_id})
            if deployment_id
            else {"status_code": 0, "data": {"error": "deployment missing"}}
        )
        logs = (
            self._request(client, "GET", f"/api/project-deployments/{deployment_id}/logs")
            if deployment_id
            else {"status_code": 0, "data": {"error": "deployment missing"}}
        )
        endpoint_url = run.get("data", {}).get("endpoint", {}).get("url")
        http_check = http_get(endpoint_url) if endpoint_url else {"ok": False, "error": "endpoint missing"}
        stop = (
            self._request(client, "POST", f"/api/project-deployments/{deployment_id}/stop")
            if deployment_id and run.get("data", {}).get("status") == "healthy"
            else {"status_code": 0, "data": {"skipped": "deployment not healthy"}}
        )
        workspace_id = created.get("data", {}).get("workspace", {}).get("workspace_id")
        workspace_root = Path(os.environ["CYCBER_DATA_DIR"]) / "workspaces" / "projects" / str(workspace_id) if workspace_id else None
        return {
            "created": created,
            "run_without_approval": run_without_approval,
            "approved": approved,
            "run": run,
            "logs": logs,
            "http_check": http_check,
            "stop": stop,
            "workspace_snapshot": snapshot_workspace(workspace_root),
        }

    def _attempt_product_uninstall_cli(
        self,
        client: TestClient,
        cli: str,
        baseline: dict[str, Any],
        install_plan: dict[str, Any],
        after_install: dict[str, Any],
    ) -> dict[str, Any]:
        if baseline["installed"]:
            return {"skipped": True, "reason": f"{cli} existed before test"}
        if not after_install["installed"]:
            return {"skipped": True, "reason": f"{cli} was not installed by test"}
        if install_plan.get("data", {}).get("install_source", {}).get("source_type") not in {"choco", "winget"}:
            return {"skipped": True, "reason": "install plan did not use supported package manager"}
        uninstall_plan = self._request(
            client,
            "POST",
            "/api/host-installs/plan",
            json={"requested_software": f"{cli} uninstall", "dry_run": False},
        )
        approval_id = uninstall_plan.get("data", {}).get("approval_id")
        approved = self._approve(client, approval_id, f"{cli} uninstall rollback") if approval_id else {"status_code": 0, "data": {"error": "approval missing"}}
        executed = (
            self._request(client, "POST", f"/api/host-installs/{uninstall_plan.get('data', {}).get('host_install_plan_id')}/execute", json={"approval_id": approval_id, "dry_run": False})
            if uninstall_plan.get("data", {}).get("host_install_plan_id") and approval_id
            else {"status_code": 0, "data": {"error": "plan or approval missing"}}
        )
        return {"skipped": False, "plan": uninstall_plan, "approved": approved, "executed": executed}

    def _cli_baseline(self, cli: str) -> dict[str, Any]:
        command = run_command(powershell_command(f"Get-Command {cli} -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Source"), timeout=120)
        version = run_command([cli, "--version"], timeout=120) if command["exit_code"] == 0 and command["stdout"].strip() else {"exit_code": 1, "stdout": "", "stderr": f"{cli} command missing"}
        choco_local = run_command(["choco", "list", cli, "--exact", "--limit-output"], timeout=120) if shutil.which("choco") else {"exit_code": 1, "stdout": "", "stderr": "choco missing"}
        installed = bool(command["stdout"].strip()) or bool(re.search(rf"(?im)^{re.escape(cli)}(\||\s+)", choco_local.get("stdout", "")))
        return {
            "installed": installed,
            "command_path": command["stdout"].strip(),
            "version": version,
            "choco_local": choco_local,
        }

    def _chat_turn(self, client: TestClient, text: str) -> dict[str, Any]:
        created = self._request(
            client,
            "POST",
            "/api/chat/turn",
            json={
                "member_id": "mem_xiaoyao",
                "session_id": f"{RUN_LABEL.lower()}-{RUN_ID.lower()}",
                "input": {"type": "text", "text": text},
            },
        )
        turn_id = created.get("data", {}).get("turn_id")
        if not turn_id:
            return {"created": created, "error": "missing turn_id"}
        deadline = time.time() + 15
        detail: dict[str, Any] = {}
        while time.time() < deadline:
            detail = self._request(client, "GET", f"/api/chat/turns/{turn_id}").get("data", {})
            if detail.get("status") in {"completed", "failed", "cancelled"}:
                break
            time.sleep(0.05)
        events_response = self._request(client, "GET", f"/api/chat/turns/{turn_id}/events")
        events = events_response.get("data", {}).get("items", [])
        completed_payload = _completed_payload(events)
        return {
            "created": created,
            "detail": detail,
            "events": events,
            "event_sequence": [event.get("event_type") or event.get("event") for event in events],
            "completed_payload": completed_payload,
            "actual_reply": _reply_from_events(events),
            "trace_id": created.get("headers", {}).get("x-trace-id"),
            "turn_id": turn_id,
        }

    def _approve(self, client: TestClient, approval_id: str | None, reason: str) -> dict[str, Any]:
        if not approval_id:
            return {"status_code": 0, "data": {"error": "approval missing"}}
        return self._request(client, "POST", f"/api/approvals/{approval_id}/approve", json={"reason": f"{RUN_LABEL} {reason}"})

    def _request(self, client: TestClient, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = client.request(method, path, **kwargs)
        try:
            data = response.json()
        except Exception:
            data = response.text
        return {
            "status_code": response.status_code,
            "data": redact_value(data),
            "headers": {"x-trace-id": response.headers.get("x-trace-id")},
        }

    def _fail(self, result: CaseResult, severity: str, title: str, expected: str, actual: str) -> None:
        result.status = "FAIL"
        issue = self._add_issue(severity, result.case_id, title, expected, actual, self._issue_evidence(result))
        result.issue_ids.append(issue.issue_id)

    def _block(self, result: CaseResult, severity: str, title: str, expected: str, actual: str) -> None:
        result.status = "BLOCKED"
        issue = self._add_issue(severity, result.case_id, title, expected, actual, self._issue_evidence(result))
        result.issue_ids.append(issue.issue_id)

    def _issue_evidence(self, result: CaseResult) -> dict[str, Any]:
        evidence = result.evidence
        summary: dict[str, Any] = {"case_status": result.status, "actual": result.actual}
        for key in [
            "workspace_snapshot",
            "logs",
            "http_check",
            "plan",
            "execution",
            "host_log",
            "run",
            "stop",
            "created",
        ]:
            if key in evidence:
                summary[key] = evidence[key]
        if "turn" in evidence:
            turn = evidence["turn"]
            summary["turn"] = {
                "detail": turn.get("detail"),
                "actual_reply": turn.get("actual_reply"),
                "structured_payload": turn.get("completed_payload", {}).get("response_plan", {}).get("structured_payload", {}),
            }
        return summary

    def _add_issue(self, severity: str, case_id: str, title: str, expected: str, actual: str, evidence: dict[str, Any] | None = None) -> Issue:
        self.issue_count += 1
        issue = Issue(
            issue_id=f"PHASE52-EXP-FIX-{self.issue_count:03d}",
            severity=severity,
            case_id=case_id,
            title=title,
            expected=expected,
            actual=str(redact_value(actual)),
            evidence=redact_value(evidence or {}),
        )
        self.issues.append(issue)
        return issue

    def _write_outputs(self) -> None:
        TEST_DIR.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(self._render_report(), encoding="utf-8")
        ISSUES_PATH.write_text(self._render_issues(), encoding="utf-8")
        EVIDENCE_PATH.write_text(
            json.dumps(
                redact_value(
                    {
                        "environment": self.environment,
                        "results": [result.__dict__ for result in self.results],
                        "issues": [issue.__dict__ for issue in self.issues],
                    }
                ),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            encoding="utf-8",
        )

    def _render_report(self) -> str:
        counts = {
            "PASS": sum(1 for item in self.results if item.status == "PASS"),
            "FAIL": sum(1 for item in self.results if item.status == "FAIL"),
            "BLOCKED": sum(1 for item in self.results if item.status == "BLOCKED"),
        }
        lines = [
            "# Phase 52 扩展真实强测执行报告",
            "",
            f"- 测试批次：`{RUN_LABEL}`",
            f"- 运行 ID：`{RUN_ID}`",
            f"- 数据目录：`{os.environ['CYCBER_DATA_DIR']}`",
            "- 本机安装/卸载目标：`jq`、`yq`；不安装或卸载 Node。",
            f"- 结果统计：PASS {counts['PASS']} / FAIL {counts['FAIL']} / BLOCKED {counts['BLOCKED']}",
            f"- 实现缺口数：{len(self.issues)}",
            "",
            "## 环境预检",
            "",
            "```json",
            json.dumps(compact_evidence(self.environment), ensure_ascii=False, indent=2),
            "```",
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
                    f"- 期望：{result.expected}",
                    "",
                    "**输入**",
                    "",
                ]
            )
            for item in result.inputs:
                lines.append(f"- `{item}`")
            lines.extend(
                [
                    "",
                    "**实际**",
                    "",
                    "```text",
                    result.actual.strip() or "无",
                    "```",
                    "",
                    "**证据摘要**",
                    "",
                    "```json",
                    json.dumps(compact_evidence(result.evidence), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)

    def _render_issues(self) -> str:
        lines = [
            "# Phase 52 扩展真实强测实现缺口",
            "",
            f"- 测试批次：`{RUN_LABEL}`",
            f"- 运行 ID：`{RUN_ID}`",
            f"- 缺口总数：{len(self.issues)}",
            "",
        ]
        if not self.issues:
            lines.append("本轮扩展真实强测未发现实现缺口。")
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
                    "```json",
                    json.dumps(compact_evidence(issue.evidence), ensure_ascii=False, indent=2),
                    "```",
                    "",
                ]
            )
        return "\n".join(lines)


def run_command(command: list[str], *, cwd: Path | None = None, timeout: int = 60) -> dict[str, Any]:
    started = datetime.now(UTC)
    try:
        completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout, check=False)
        stdout = str(redact_value((completed.stdout or "").strip()))
        stderr = str(redact_value((completed.stderr or "").strip()))
        return {
            "command": " ".join(command),
            "exit_code": completed.returncode,
            "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
            "stdout": stdout,
            "stderr": stderr,
            "stdout_tail": tail_lines(stdout),
            "stderr_tail": tail_lines(stderr),
            "summary": last_non_empty_line(stdout) or last_non_empty_line(stderr),
        }
    except FileNotFoundError as exc:
        return {
            "command": " ".join(command),
            "exit_code": 127,
            "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
            "stdout": "",
            "stderr": str(redact_value(str(exc))),
            "stdout_tail": "",
            "stderr_tail": str(redact_value(str(exc))),
            "summary": str(redact_value(str(exc))),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "command": " ".join(command),
            "exit_code": None,
            "duration_seconds": round((datetime.now(UTC) - started).total_seconds(), 3),
            "stdout": str(redact_value(exc.stdout or "")),
            "stderr": str(redact_value(exc.stderr or "")),
            "stdout_tail": tail_lines(str(redact_value(exc.stdout or ""))),
            "stderr_tail": tail_lines(str(redact_value(exc.stderr or ""))),
            "summary": f"timeout after {exc.timeout}s",
        }


def executable_command(name: str, *args: str) -> list[str]:
    return [shutil.which(name) or name, *args]


def powershell_command(script: str) -> list[str]:
    return ["powershell", "-NoProfile", "-Command", script]


def http_get(url: str | None) -> dict[str, Any]:
    if not url:
        return {"ok": False, "error": "missing url"}
    try:
        request = Request(url, headers={"User-Agent": RUN_LABEL})
        with urlopen(request, timeout=5) as response:
            body = response.read(512).decode("utf-8", errors="replace")
            return {"ok": 200 <= response.status < 400, "status": response.status, "body_preview": redact_value(body)}
    except Exception as exc:
        return {"ok": False, "error": str(redact_value(str(exc)))}


def snapshot_workspace(path: Path | None) -> dict[str, Any]:
    if path is None or not path.exists():
        return {"exists": False}
    names = sorted(item.name for item in path.iterdir())[:60]
    content = ""
    for filename in ["README.md", "package.json", "pyproject.toml", "requirements.txt", "go.mod", "Cargo.toml", "index.html"]:
        target = path / filename
        if target.exists():
            content += f"\n--- {filename} ---\n"
            content += target.read_text(encoding="utf-8", errors="replace")[:1500]
    return {
        "exists": True,
        "path": str(redact_value(str(path))),
        "top_level_names": names,
        "has_git_or_repo_files": (path / ".git").exists() or len(names) > 3,
        "placeholder_public_git_source_recorded": "Managed deployment placeholder" in content,
        "content_preview": redact_value(content[:2500]),
    }


def flow_summary(flow: dict[str, Any]) -> dict[str, Any]:
    run = flow.get("run", {})
    run_data = run.get("data", {}) if isinstance(run.get("data"), dict) else {}
    endpoint = run_data.get("endpoint", {}) if isinstance(run_data.get("endpoint"), dict) else {}
    port_lease = run_data.get("port_lease", {}) if isinstance(run_data.get("port_lease"), dict) else {}
    workspace = run_data.get("workspace", {}) if isinstance(run_data.get("workspace"), dict) else {}
    stack_summary = (
        workspace.get("stack_summary", {}) if isinstance(workspace.get("stack_summary"), dict) else {}
    )
    stop_data = flow.get("stop", {}).get("data", {}) if isinstance(flow.get("stop", {}).get("data"), dict) else {}
    stop_port = (
        stop_data.get("port_lease", {}) if isinstance(stop_data.get("port_lease"), dict) else {}
    )
    return redact_value(
        {
            "created_status": flow.get("created", {}).get("status_code"),
            "run_status": run_data.get("status"),
            "failure_reason": run_data.get("failure_reason"),
            "detected_stack": stack_summary.get("stack"),
            "endpoint_url": endpoint.get("url"),
            "port": port_lease.get("port"),
            "http_ok": flow.get("http_check", {}).get("ok"),
            "stop_status": stop_data.get("status"),
            "stop_port_status": stop_port.get("status"),
        }
    )


def read_host_install_log(execution: dict[str, Any]) -> dict[str, Any]:
    data = execution.get("data", {})
    task_id = data.get("task_id")
    if not task_id:
        return {"available": False, "reason": "missing task_id"}
    path = Path(os.environ["CYCBER_DATA_DIR"]) / "artifacts" / str(task_id) / "logs" / "host-install.log"
    if not path.exists():
        return {"available": False, "path": str(redact_value(str(path))), "reason": "missing log file"}
    content = path.read_text(encoding="utf-8", errors="replace")
    return {"available": True, "path": str(redact_value(str(path))), "content": str(redact_value(content))[:6000]}


def _reply_from_events(events: list[dict[str, Any]]) -> str:
    deltas = [str(event.get("payload", {}).get("payload", {}).get("text", "")) for event in events if event.get("event_type") == "response.delta"]
    if deltas:
        return "".join(deltas)
    completed = _completed_payload(events)
    plan = completed.get("response_plan") or {}
    return str(plan.get("plain_text") or completed.get("message") or "")


def _completed_payload(events: list[dict[str, Any]]) -> dict[str, Any]:
    for event in reversed(events):
        if event.get("event_type") == "response.completed":
            payload = event.get("payload", {})
            nested = payload.get("payload")
            return dict(nested if isinstance(nested, dict) else payload)
    return {}


def _claims_done(text: str, verbs: tuple[str, ...]) -> bool:
    if any(marker in text for marker in ("已创建", "已生成", "计划", "待确认", "等待你确认", "审批前")):
        completion_markers = ("已部署", "已经部署", "部署完成", "部署成功", "已安装", "已经安装", "安装完成", "安装成功", "已卸载", "卸载完成")
        return any(marker in text for marker in completion_markers)
    for verb in verbs:
        if re.search(rf"(已|已经|我已|成功|完成).{{0,8}}{verb}", text):
            return True
        if re.search(rf"{verb}.{{0,8}}(完成|成功|好了|完毕)", text):
            return True
    return False


def contains_sensitive_log_text(text: str) -> bool:
    if not text:
        return False
    patterns = [
        r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}",
        r"(?i)(api[_-]?key|token|password|passwd|pwd|cookie|private[_-]?key)\s*[:=]\s*(?!\[REDACTED\])[^'\"\s,;}]+",
        r"(?i)-----BEGIN (RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----",
    ]
    return any(re.search(pattern, text) for pattern in patterns)


def compact_evidence(value: Any, *, max_chars: int = 9000) -> Any:
    redacted = redact_value(value)
    text = json.dumps(redacted, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return redacted
    return {"truncated": True, "preview": text[:max_chars] + "..."}


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_text(text: str) -> str:
    patterns = [
        (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_-]{8,}"), "[REDACTED_API_KEY]"),
        (re.compile(r"(?i)(api[_-]?key|token|password|passwd|pwd|cookie|private[_-]?key)\s*[:=]\s*['\"]?[^'\"\s,;}]+"), r"\1=[REDACTED]"),
        (re.compile(r"sec_[a-f0-9]{12,}", re.I), "sec_[REDACTED]"),
        (re.compile(r"[A-Za-z]:\\Users\\[^\\\s]+"), r"C:\\Users\\[REDACTED]"),
    ]
    result = text
    for pattern, replacement in patterns:
        result = pattern.sub(replacement, result)
    return result


def tail_lines(text: str, count: int = 8) -> str:
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(lines[-count:])


def last_non_empty_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def main() -> None:
    Phase52ExpandedRunner().run()


if __name__ == "__main__":
    main()
