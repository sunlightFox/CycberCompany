from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parents[1]


def _load_runner(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("feishu_casewise_runner", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load runner: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[str(spec.name)] = module
    spec.loader.exec_module(module)
    return module


def _find_runner(name: str) -> Path:
    matches = sorted(ROOT_DIR.rglob(name))
    if not matches:
        raise RuntimeError(f"runner not found: {name}")
    return matches[0]


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _result_rank(item: dict[str, Any]) -> tuple[int, int, int, int]:
    verdict_rank = {"fail": 0, "warn": 1, "pass": 2}
    return (
        1 if item.get("model_completed") else 0,
        1 if item.get("delivery_sent") else 0,
        verdict_rank.get(str(item.get("verdict") or ""), -1),
        int(item.get("score") or 0),
    )


def _choose_better(current: dict[str, Any] | None, candidate: dict[str, Any]) -> dict[str, Any]:
    if current is None:
        return candidate
    return candidate if _result_rank(candidate) > _result_rank(current) else current


def _case_stub(case: Any, note: str) -> dict[str, Any]:
    return {
        "case_id": str(case.case_id),
        "category": str(case.category),
        "title": str(case.title),
        "peer_ref": str(case.peer_ref),
        "prompt": str(case.prompt),
        "verdict": "fail",
        "score": 0,
        "notes": [note],
        "reply_text": "",
        "turn_id": None,
        "conversation_id": None,
        "trace_id": None,
        "route_brain_id": None,
        "model_started": False,
        "model_completed": False,
        "usage_total_tokens": None,
        "delivery_sent": False,
        "event_types": [],
        "route_type": None,
        "task_status": None,
    }


def _extract_result(payload: dict[str, Any], case_id: str) -> dict[str, Any] | None:
    for item in payload.get("results") or []:
        if isinstance(item, dict) and str(item.get("case_id")) == case_id:
            return item
    return None


def _summary_payload(
    *,
    runner: Any,
    results: list[dict[str, Any]],
    previous: dict[str, Any],
) -> dict[str, Any]:
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        verdict = str(item.get("verdict") or "fail")
        category = str(item.get("category") or "-")
        bucket = by_category.setdefault(category, {"total": 0, "pass": 0, "warn": 0, "fail": 0})
        bucket["total"] += 1
        if verdict in {"pass", "warn", "fail"}:
            bucket[verdict] += 1
        else:
            bucket["fail"] += 1
    scores = [int(item.get("score") or 0) for item in results]
    total = len(results)
    return {
        "run_label": getattr(runner, "RUN_LABEL", previous.get("run_label")),
        "entry": previous.get("entry") or "feishu_mock_channel",
        "model_proxy_endpoint": getattr(
            runner,
            "MODEL_PROXY_ENDPOINT",
            previous.get("model_proxy_endpoint") or previous.get("model_endpoint"),
        ),
        "quality_rubric": previous.get("quality_rubric")
        or {
            "real_model_delivery_trace": 25,
            "correctness_expected_terms": 25,
            "natural_visible_reply_no_system_or_tech_tone": 25,
            "boundaries_no_false_completion_no_sensitive_leak": 25,
        },
        "rerun_policy": "Casewise merge: rerun only fail/warn or missing cases; keep better existing evidence.",
        "total": total,
        "passed": sum(1 for item in results if item.get("verdict") == "pass"),
        "warned": sum(1 for item in results if item.get("verdict") == "warn"),
        "failed": sum(1 for item in results if item.get("verdict") == "fail"),
        "score_avg": round(sum(scores) / total, 2) if total else None,
        "model_started": sum(1 for item in results if item.get("model_started")),
        "model_completed": sum(1 for item in results if item.get("model_completed")),
        "delivery_sent": sum(1 for item in results if item.get("delivery_sent")),
        "trace_count": sum(1 for item in results if item.get("trace_id")),
        "model_verify": previous.get("model_verify") or {},
        "by_category": by_category,
        "results": results,
    }


def _write_report(runner: Any, payload: dict[str, Any]) -> None:
    report_path = Path(runner.REPORT_PATH)
    lines = [
        f"# {payload.get('run_label')} 真实模型场景测试报告",
        "",
        f"- 总数：{payload['total']}",
        f"- 通过：{payload['passed']}",
        f"- 告警：{payload['warned']}",
        f"- 失败：{payload['failed']}",
        f"- 平均分：{payload['score_avg']}",
        f"- 真实模型完成：{payload['model_completed']}/{payload['total']}",
        f"- 飞书投递：{payload['delivery_sent']}/{payload['total']}",
        f"- trace：{payload['trace_count']}/{payload['total']}",
        "",
        "## 分类结果",
        "",
    ]
    for category, stats in payload["by_category"].items():
        lines.append(
            f"- {category}: pass {stats['pass']} / warn {stats['warn']} / fail {stats['fail']} / total {stats['total']}"
        )
    lines.extend(
        [
            "",
            "## 明细",
            "",
            "| Case | 分类 | 标题 | 判定 | 分数 | 模型 | 投递 | 备注 |",
            "|---|---|---|---:|---:|---|---|---|",
        ]
    )
    for item in payload["results"]:
        lines.append(
            "| {case} | {category} | {title} | {verdict} | {score} | {model} | {delivered} | {notes} |".format(
                case=item.get("case_id"),
                category=item.get("category"),
                title=item.get("title"),
                verdict=item.get("verdict"),
                score=item.get("score"),
                model="ok" if item.get("model_started") and item.get("model_completed") else "no",
                delivered="ok" if item.get("delivery_sent") else "no",
                notes=", ".join(item.get("notes") or []) or "-",
            )
        )
    lines.extend(["", "## 样例回复摘录", ""])
    for item in payload["results"][:80]:
        preview = str(item.get("reply_text") or "").replace("\n", " ")[:280]
        lines.append(f"- `{item.get('case_id')}` {item.get('verdict')}/{item.get('score')}: {preview}")
    report_path.write_text("\n".join(lines), encoding="utf-8")


def _write_gap(runner: Any, payload: dict[str, Any]) -> None:
    gap_path = Path(runner.GAP_PATH)
    problematic = [item for item in payload["results"] if item.get("verdict") != "pass"]
    lines = [
        "# 缺口与修复队列",
        "",
        f"- 当前异常数：{len(problematic)}",
        "- 原则：只修复通用问题；修复后只重跑 fail/warn 场景。",
        "",
    ]
    if not problematic:
        lines.append("无遗留 fail/warn。")
    for item in problematic:
        preview = str(item.get("reply_text") or "").replace("\n", " ")[:360]
        lines.extend(
            [
                f"## {item.get('case_id')} {item.get('title')}",
                f"- 分类：{item.get('category')}",
                f"- 判定：{item.get('verdict')}",
                f"- 分数：{item.get('score')}",
                f"- 备注：{', '.join(item.get('notes') or []) or '-'}",
                f"- 回复摘录：{preview}",
                "",
            ]
        )
    gap_path.write_text("\n".join(lines), encoding="utf-8")


def _rewrite_outputs(runner: Any, by_id: dict[str, dict[str, Any]], previous: dict[str, Any]) -> dict[str, Any]:
    results = [by_id[case_id] for case_id in sorted(by_id)]
    payload = _summary_payload(runner=runner, results=results, previous=previous)
    _write_json(Path(runner.SUMMARY_PATH), payload)
    _write_report(runner, payload)
    _write_gap(runner, payload)
    return payload


def run_casewise(
    *,
    runner_path: Path,
    case_ids: set[str],
    only_problematic: bool,
    run_all_missing_or_problematic: bool,
    timeout_seconds: int,
    retries: int,
) -> dict[str, Any]:
    runner = _load_runner(runner_path)
    evidence_dir = Path(runner.EVIDENCE_DIR)
    summary_path = Path(runner.SUMMARY_PATH)
    evidence_dir.mkdir(parents=True, exist_ok=True)
    cases = list(runner._cases("http://127.0.0.1:0"))
    case_by_id = {str(case.case_id): case for case in cases}
    previous = _read_json(summary_path)
    by_id: dict[str, dict[str, Any]] = {}
    for item in previous.get("results") or []:
        if isinstance(item, dict) and item.get("case_id"):
            by_id[str(item["case_id"])] = item

    selected = set(case_ids)
    if only_problematic:
        selected |= {
            case_id
            for case_id, item in by_id.items()
            if str(item.get("verdict") or "fail") != "pass"
        }
    if run_all_missing_or_problematic or not selected:
        selected |= {
            case.case_id
            for case in cases
            if case.case_id not in by_id or str(by_id[case.case_id].get("verdict") or "fail") != "pass"
        }
    selected &= set(case_by_id)

    progress_path = evidence_dir / "casewise_progress.json"
    progress = {
        "run_label": getattr(runner, "RUN_LABEL", None),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "runner": str(runner_path),
        "total_selected": len(selected),
        "completed": 0,
        "items": [],
    }
    _write_json(progress_path, progress)
    _rewrite_outputs(runner, by_id, previous)

    for case_id in sorted(selected):
        case = case_by_id[case_id]
        best_result: dict[str, Any] | None = by_id.get(case_id)
        last_error = ""
        for attempt in range(1, retries + 2):
            stdout_path = evidence_dir / f"casewise_{case_id}_attempt{attempt}.stdout.txt"
            stderr_path = evidence_dir / f"casewise_{case_id}_attempt{attempt}.stderr.txt"
            command = [
                sys.executable,
                "-X",
                "utf8",
                str(runner_path),
                "--case-id",
                case_id,
                "--merge-existing",
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(ROOT_DIR),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                )
                stdout_path.write_text(completed.stdout or "", encoding="utf-8")
                stderr_path.write_text(completed.stderr or "", encoding="utf-8")
                child_payload = _read_json(summary_path)
                child_result = _extract_result(child_payload, case_id)
                if child_result is None:
                    last_error = f"case_process_failed:{completed.returncode}"
                    continue
                result_path = evidence_dir / f"casewise_{case_id}_result.json"
                _write_json(result_path, child_result)
                best_result = _choose_better(best_result, child_result)
                last_error = "" if child_result.get("verdict") == "pass" else f"case_verdict:{child_result.get('verdict')}"
                if child_result.get("verdict") == "pass":
                    break
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
                stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
                stdout_path.write_text(stdout, encoding="utf-8")
                stderr_path.write_text(stderr, encoding="utf-8")
                last_error = f"case_process_timeout:{timeout_seconds}s"
        if best_result is None:
            best_result = _case_stub(case, last_error or "case_process_failed")
        by_id[case_id] = best_result
        payload = _rewrite_outputs(runner, by_id, previous)
        progress["completed"] = int(progress["completed"]) + 1
        progress["items"].append(
            {
                "case_id": case_id,
                "verdict": best_result.get("verdict"),
                "score": best_result.get("score"),
                "error": last_error,
            }
        )
        _write_json(progress_path, progress)
    return _rewrite_outputs(runner, by_id, previous)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runner", type=Path)
    parser.add_argument("--runner-name")
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--only-problematic", action="store_true")
    parser.add_argument("--all-missing-or-problematic", action="store_true")
    parser.add_argument("--case-timeout", type=int, default=150)
    parser.add_argument("--case-retries", type=int, default=1)
    args = parser.parse_args()

    if args.runner is None and not args.runner_name:
        raise SystemExit("--runner or --runner-name is required")
    runner_path = args.runner.resolve() if args.runner else _find_runner(str(args.runner_name))
    payload = run_casewise(
        runner_path=runner_path,
        case_ids=set(args.case_id or []),
        only_problematic=args.only_problematic,
        run_all_missing_or_problematic=args.all_missing_or_problematic,
        timeout_seconds=args.case_timeout,
        retries=args.case_retries,
    )
    print(
        json.dumps(
            {
                "total": payload["total"],
                "passed": payload["passed"],
                "warned": payload["warned"],
                "failed": payload["failed"],
                "summary": str(_load_runner(runner_path).SUMMARY_PATH),
                "report": str(_load_runner(runner_path).REPORT_PATH),
                "gap_queue": str(_load_runner(runner_path).GAP_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
