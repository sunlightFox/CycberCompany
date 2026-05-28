from __future__ import annotations

# ruff: noqa: E501
import json
import re
from dataclasses import dataclass, field
from typing import Any

from brain.adapters import CancelToken, ModelChatRequest
from core_types import Goal, GoalProgressSnapshot, ScheduledTask

from app.schemas.goals import GoalIntakeUpdateRequest


@dataclass(frozen=True)
class GoalDomainSpec:
    domain_label: str
    missing_fields: tuple[str, ...]
    milestones: tuple[dict[str, Any], ...]
    routines: tuple[dict[str, Any], ...]
    risk_notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class GoalPlanDraft:
    summary: str
    success_criteria: list[str]
    assumptions: list[str]
    risk_notes: list[str]
    items: list[dict[str, Any]]
    milestones: list[dict[str, Any]]
    routines: list[dict[str, Any]]
    model_call: dict[str, Any]
    fallback_used: bool = True


@dataclass(frozen=True)
class GoalChatOutcome:
    intent: str
    visible_text: str
    structured_payload: dict[str, Any]
    memory_candidates: list[dict[str, Any]] = field(default_factory=list)

    @property
    def text(self) -> str:
        return self.visible_text

    @property
    def payload(self) -> dict[str, Any]:
        return self.structured_payload


class GoalDomainRegistry:
    def __init__(self) -> None:
        self._specs = {
            "exam_certification": GoalDomainSpec(
                domain_label="exam_certification",
                missing_fields=("exam_name", "target_date", "available_time"),
                milestones=(
                    {"title": "摸清考试结构", "description": "确认考试科目、题型、分值和资料范围。"},
                    {"title": "完成第一轮覆盖", "description": "按资料目录完成一轮知识覆盖和基础笔记。"},
                    {"title": "真题与案例专项", "description": "用真题发现薄弱点，集中拆案例和错题。"},
                    {"title": "冲刺压缩复盘", "description": "考前压缩重点、错题和高频模板。"},
                    {"title": "考后复盘", "description": "记录有效方法，为后续证书目标沉淀经验。"},
                ),
                routines=(
                    {"title": "每日复习块", "description": "按可投入时间推进教材、视频或题目。", "estimated_minutes": 45},
                    {"title": "错题记录", "description": "把不会的题记录成可复盘条目。", "estimated_minutes": 15},
                    {"title": "每周小复盘", "description": "每周检查进度、卡点和下周重点。", "estimated_minutes": 30},
                ),
                risk_notes=("考试政策、报名时间和证书规则以官方信息为准。",),
            ),
            "language_learning": GoalDomainSpec(
                domain_label="language_learning",
                missing_fields=("target_language", "current_level", "target_level", "available_time"),
                milestones=(
                    {"title": "确认水平和目标场景", "description": "明确当前水平、目标能力和最常用场景。"},
                    {"title": "建立输入节奏", "description": "稳定听读输入，积累高频表达。"},
                    {"title": "建立输出练习", "description": "加入口语或写作输出，避免只看不练。"},
                    {"title": "场景化复盘", "description": "围绕真实场景复盘表达、词汇和卡点。"},
                ),
                routines=(
                    {"title": "每日输入", "description": "听读短材料并标记生词和句型。", "estimated_minutes": 25},
                    {"title": "每日输出", "description": "用目标语言说或写一个小段落。", "estimated_minutes": 15},
                    {"title": "每周场景练习", "description": "围绕一个真实场景做集中练习。", "estimated_minutes": 30},
                ),
            ),
            "programming_learning": GoalDomainSpec(
                domain_label="programming_learning",
                missing_fields=("language_or_track", "current_level", "project_goal", "available_time"),
                milestones=(
                    {"title": "确认方向和环境", "description": "明确语言、方向、开发环境和练习方式。"},
                    {"title": "基础语法和小练习", "description": "用小题和示例建立基础手感。"},
                    {"title": "做一个小项目", "description": "把知识串到可运行的小项目里。"},
                    {"title": "复盘代码和补短板", "description": "根据项目暴露的问题补基础和工程习惯。"},
                ),
                routines=(
                    {"title": "每日编码练习", "description": "写一小段能运行的代码。", "estimated_minutes": 30},
                    {"title": "错误记录", "description": "记录报错、原因和修复方法。", "estimated_minutes": 10},
                    {"title": "每周项目推进", "description": "给项目补一个可验证功能。", "estimated_minutes": 60},
                ),
            ),
            "fitness": GoalDomainSpec(
                domain_label="fitness",
                missing_fields=("current_level", "available_time"),
                milestones=(
                    {"title": "确认身体状态和目标", "description": "明确目标、可训练时间和不适边界。"},
                    {"title": "建立低门槛动作", "description": "先稳定开始，再逐步增加强度。"},
                    {"title": "记录反馈", "description": "记录完成、疲劳、不适和恢复情况。"},
                    {"title": "按周微调", "description": "根据反馈调整频率和强度。"},
                ),
                routines=(
                    {"title": "固定运动块", "description": "安排低门槛运动动作。", "estimated_minutes": 30},
                    {"title": "状态反馈", "description": "记录疲劳、疼痛和完成情况。", "estimated_minutes": 5},
                ),
                risk_notes=("如果有疼痛、受伤或明显不适，先停止相关动作并考虑咨询专业人士。",),
            ),
            "general": GoalDomainSpec(
                domain_label="general",
                missing_fields=("available_time",),
                milestones=(
                    {"title": "明确目标和衡量标准", "description": "把目标改写成可判断进展的版本。"},
                    {"title": "建立低门槛执行节奏", "description": "先安排一个容易开始的固定动作。"},
                    {"title": "监督追问和记录反馈", "description": "按约定时间记录完成情况。"},
                    {"title": "复盘并调整下一步", "description": "根据最近反馈更新进度和重点。"},
                ),
                routines=(
                    {"title": "最小行动", "description": "每天或每周完成一个小动作。", "estimated_minutes": 20},
                    {"title": "复盘记录", "description": "记录完成、没完成或卡住的原因。", "estimated_minutes": 10},
                ),
            ),
        }

    def classify(self, text: str, preferred_domain: str | None = None) -> str:
        if preferred_domain in self._specs:
            return str(preferred_domain)
        clean = str(text or "").lower()
        if any(
            marker in clean
            for marker in (
                "jlpt",
                "ielts",
                "toefl",
                "tef",
                "tcf",
                "dele",
                "cils",
                "celi",
                "goethe",
                "telc",
                "\u6b4c\u5fb7",
                "siele",
                "celpip",
                "pte academic",
                "duolingo english test",
            )
        ) or re.search(r"(?<![a-z0-9])gre(?![a-z0-9])", clean):
            return "language_learning"
        if any(
            marker in clean
            for marker in (
                "delf",
                "dalf",
                "hsk",
                "topik",
                "托福",
                "雅思",
                "persian",
                "farsi",
                "swedish",
                "dutch",
                "toeic",
                "\u6ce2\u65af\u8bed",
                "\u745e\u5178\u8bed",
                "\u8377\u5170\u8bed",
                "\u8377\u862d\u8a9e",
                "\u5e0c\u4f2f\u6765\u8bed",
                "\u5e0c\u4f2f\u4f86\u8a9e",
                "\u6ce2\u5170\u8bed",
                "\u6ce2\u862d\u8a9e",
                "\u632a\u5a01\u8bed",
                "\u632a\u5a01\u8a9e",
                "\u4e4c\u514b\u5170\u8bed",
                "\u70cf\u514b\u862d\u8a9e",
                "\u6377\u514b\u8bed",
                "\u6377\u514b\u8a9e",
                "\u82ac\u5170\u8bed",
                "\u82ac\u862d\u8a9e",
                "\u7f57\u9a6c\u5c3c\u4e9a\u8bed",
                "\u7f85\u99ac\u5c3c\u4e9e\u8a9e",
                "\u5308\u7259\u5229\u8bed",
                "\u5308\u7259\u5229\u8a9e",
                "\u7acb\u9676\u5b9b\u8bed",
                "\u7acb\u9676\u5b9b\u8a9e",
            )
        ):
            return "language_learning"
        if any(
            marker in clean
            for marker in (
                "flutter",
                "dart",
                "r 语言",
                "r语言",
                "数据可视化",
                "unreal",
                "c++",
                "php",
                "laravel",
                "solidity",
                "web3",
                "\u667a\u80fd\u5408\u7ea6",
                "c \u8bed\u8a00",
                "c\u8bed\u8a00",
                "\u6570\u636e\u7ed3\u6784",
                "\u94fe\u8868",
                "\u6808",
                "elixir",
                "phoenix",
                "scala",
                "akka",
                "power bi",
                "powerbi",
                "\u4eea\u8868\u76d8",
                "\u6570\u636e\u5efa\u6a21",
                "terraform",
                "iac",
                "aws iac",
                "\u57fa\u7840\u8bbe\u65bd\u6a21\u677f",
                "graphql",
                "apollo",
                "kafka",
                "\u6d41\u5904\u7406",
                "\u5b9e\u65f6\u8ba2\u5355",
                "consumer group",
                "offset",
                "redis",
                "\u7f13\u5b58",
                "\u7f13\u5b58\u8bbe\u8ba1",
                "\u7f13\u5b58\u4f18\u5316",
                "\u7f13\u5b58\u51fb\u7a7f",
                "nuxt",
                "vue",
                "sveltekit",
                "langchain",
                "rag",
                "chroma",
                "blazor",
                ".net",
                "dotnet",
                "vector database",
                "\u5411\u91cf\u6570\u636e\u5e93",
                "\u68c0\u7d22\u589e\u5f3a\u751f\u6210",
                "fastapi",
                "postgresql",
                "postgres",
                "airflow",
                "etl",
                "dag",
                "remix",
                "supabase",
                "elasticsearch",
                "\u5168\u6587\u68c0\u7d22",
                "\u641c\u7d22\u5f15\u64ce",
                "\u641c\u7d22",
                "mapping",
                "\u5206\u8bcd\u5668",
                "\u6570\u636e\u7ba1\u9053",
                "\u8c03\u5ea6\u7cfb\u7edf",
                "\u84dd\u961f",
                "\u65e5\u5fd7\u5206\u6790",
                "\u544a\u8b66",
                "kubernetes",
                "k8s",
                "prometheus",
                "grafana",
                "\u53ef\u89c2\u6d4b\u6027",
                "\u76d1\u63a7\u9762\u677f",
            )
        ):
            return "programming_learning"
        if any(marker in clean for marker in ("notion", "知识库")):
            return "general"
        if any(
            marker in clean
            for marker in (
                "\u7761\u524d",
                "\u7761\u7720",
                "\u4f5c\u606f",
                "\u5c4f\u5e55\u65f6\u95f4",
                "\u51cf\u5c11\u5c4f\u5e55",
                "\u653e\u677e\u6d41\u7a0b",
            )
        ):
            return "general"
        if any(
            marker in clean
            for marker in (
                "考证",
                "证书",
                "资格证",
                "认证",
                "拿证",
                "考试",
                "备考",
                "软考",
                "考研",
                "法考",
                "司法考试",
                "cfa",
                "cpa",
                "pmp",
                "acca",
                "frm",
                "sat",
                "sat math",
            )
        ):
            return "exam_certification"
        if any(
            marker in clean
            for marker in (
                "\u6500\u5ca9",
                "\u62b1\u77f3",
                "\u5ca9\u9986",
                "\u5ca9\u58c1",
                "\u63e1\u529b",
                "\u8def\u7ebf\u9605\u8bfb",
                "\u80a9\u9888",
                "\u62c9\u4f38",
                "\u653e\u677e",
                "\u4e45\u5750",
                "\u592a\u6781",
                "\u592a\u6975",
                "\u5e73\u8861",
            )
        ):
            return "fitness"
        if any(
            marker in clean
            for marker in (
                "英语",
                "日语",
                "韩语",
                "法语",
                "西班牙语",
                "德语",
                "俄语",
                "意大利语",
                "葡萄牙语",
                "阿拉伯语",
                "泰语",
                "越南语",
                "印尼语",
                "印度尼西亚语",
                "土耳其语",
                "persian",
                "farsi",
                "swedish",
                "dutch",
                "toeic",
                "\u6ce2\u65af\u8bed",
                "\u745e\u5178\u8bed",
                "\u8377\u5170\u8bed",
                "\u8377\u862d\u8a9e",
                "\u5e0c\u4f2f\u6765\u8bed",
                "\u5e0c\u4f2f\u4f86\u8a9e",
                "\u6ce2\u5170\u8bed",
                "\u6ce2\u862d\u8a9e",
                "\u632a\u5a01\u8bed",
                "\u632a\u5a01\u8a9e",
                "\u4e4c\u514b\u5170\u8bed",
                "\u70cf\u514b\u862d\u8a9e",
                "\u6377\u514b\u8bed",
                "\u6377\u514b\u8a9e",
                "\u82ac\u5170\u8bed",
                "\u82ac\u862d\u8a9e",
                "\u7f57\u9a6c\u5c3c\u4e9a\u8bed",
                "\u7f85\u99ac\u5c3c\u4e9e\u8a9e",
                "\u5308\u7259\u5229\u8bed",
                "\u5308\u7259\u5229\u8a9e",
                "\u7acb\u9676\u5b9b\u8bed",
                "\u7acb\u9676\u5b9b\u8a9e",
                "中文",
                "汉语",
                "普通话",
                "语言",
                "口语",
                "听力",
                "阅读",
                "topik",
                "雅思",
                "托福",
                "hsk",
            )
        ):
            return "language_learning"
        if any(
            marker in clean
            for marker in (
                "编程",
                "python",
                "java",
                "go",
                "golang",
                "rust",
                "typescript",
                "javascript",
                "node",
                "node.js",
                "kotlin",
                "android",
                "swift",
                "ios",
                "unreal",
                "c++",
                "flutter",
                "dart",
                "linux",
                "shell",
                "php",
                "laravel",
                "命令行",
                "终端",
                "c#",
                "csharp",
                "unity",
                "spark",
                "r 语言",
                "r语言",
                "数据工程",
                "vba",
                "宏",
                "自动化",
                "cli",
                "docker",
                "devops",
                "ci/cd",
                "容器",
                "部署",
                "流水线",
                "机器学习",
                "模型",
                "调参",
                "react",
                "sql",
                "数据分析",
                "代码",
                "开发",
                "算法",
                "前端",
                "后端",
                "api",
                "solidity",
                "web3",
                "\u667a\u80fd\u5408\u7ea6",
                "\u94fe\u4e0a",
                "\u533a\u5757\u94fe",
                "c \u8bed\u8a00",
                "c\u8bed\u8a00",
                "\u6570\u636e\u7ed3\u6784",
                "\u94fe\u8868",
                "\u6808",
                "\u6307\u9488",
                "malloc",
                "elixir",
                "phoenix",
                "scala",
                "akka",
                "actor",
                "future",
                "power bi",
                "powerbi",
                "\u4eea\u8868\u76d8",
                "\u6570\u636e\u5efa\u6a21",
                "\u9500\u552e\u4eea\u8868\u76d8",
                "terraform",
                "iac",
                "aws iac",
                "\u57fa\u7840\u8bbe\u65bd\u6a21\u677f",
                "\u53ef\u590d\u7528\u57fa\u7840\u8bbe\u65bd",
                "graphql",
                "apollo",
                "resolver",
                "kafka",
                "\u6d41\u5904\u7406",
                "\u5b9e\u65f6\u8ba2\u5355",
                "consumer group",
                "offset",
                "redis",
                "\u7f13\u5b58",
                "\u7f13\u5b58\u8bbe\u8ba1",
                "\u7f13\u5b58\u4f18\u5316",
                "\u7f13\u5b58\u51fb\u7a7f",
                "\u8fc7\u671f\u7b56\u7565",
                "nuxt",
                "vue",
                "sveltekit",
                "langchain",
                "rag",
                "chroma",
                "blazor",
                ".net",
                "dotnet",
                "vector database",
                "\u5411\u91cf\u6570\u636e\u5e93",
                "\u68c0\u7d22\u589e\u5f3a\u751f\u6210",
                "fastapi",
                "postgresql",
                "postgres",
                "airflow",
                "etl",
                "dag",
                "remix",
                "supabase",
                "elasticsearch",
                "\u5168\u6587\u68c0\u7d22",
                "\u641c\u7d22\u5f15\u64ce",
                "\u641c\u7d22",
                "mapping",
                "\u5206\u8bcd\u5668",
                "\u6570\u636e\u7ba1\u9053",
                "\u8c03\u5ea6\u7cfb\u7edf",
                "\u84dd\u961f",
                "\u65e5\u5fd7\u5206\u6790",
                "\u5b89\u5168\u5206\u6790",
                "\u544a\u8b66",
                "\u68c0\u6d4b\u89c4\u5219",
                "kubernetes",
                "k8s",
                "prometheus",
                "grafana",
                "\u53ef\u89c2\u6d4b\u6027",
                "\u76d1\u63a7\u9762\u677f",
            )
        ):
            return "programming_learning"
        if any(
            marker in clean
            for marker in (
                "健身",
                "运动",
                "减脂",
                "增肌",
                "跑步",
                "瑜伽",
                "游泳",
                "骑行",
                "羽毛球",
                "普拉提",
                "健康饮食",
                "备餐",
                "饮食",
                "外卖",
                "力量训练",
                "体能",
                "耐力",
                "训练",
                "\u8df3\u821e",
                "\u821e\u8e48",
                "\u8282\u594f",
                "\u8eab\u4f53\u534f\u8c03",
                "\u6500\u5ca9",
                "\u62b1\u77f3",
                "\u5ca9\u9986",
                "\u63e1\u529b",
                "\u80a9\u9888",
                "\u62c9\u4f38",
                "\u653e\u677e",
                "\u4e45\u5750",
                "\u592a\u6781",
                "\u592a\u6975",
                "\u5e73\u8861",
                "\u559d\u6c34",
                "\u996e\u6c34",
                "\u542b\u7cd6\u996e\u6599",
                "\u751c\u996e",
            )
        ):
            return "fitness"
        return "general"

    def spec(self, domain_label: str) -> GoalDomainSpec:
        return self._specs.get(domain_label) or self._specs["general"]

    def missing_fields(self, domain_label: str, intake: dict[str, Any]) -> list[str]:
        missing: list[str] = []
        raw = dict(intake.get("raw_answers") or {})
        for field_name in self.spec(domain_label).missing_fields:
            if intake.get(field_name) or raw.get(field_name):
                continue
            if field_name == "available_time" and intake.get("available_time"):
                continue
            missing.append(field_name)
        return missing


class GoalPlanner:
    def __init__(
        self,
        domain_registry: GoalDomainRegistry,
        *,
        brain_repo: Any | None = None,
        model_gateway: Any | None = None,
    ) -> None:
        self._domains = domain_registry
        self._brains = brain_repo
        self._model_gateway = model_gateway

    async def build_plan(
        self,
        *,
        title: str,
        description: str,
        domain_label: str,
        intake: dict[str, Any],
        planning_mode: str,
        trace_id: str | None = None,
    ) -> GoalPlanDraft:
        spec = self._domains.spec(domain_label)
        if planning_mode != "template_only":
            draft = await self._try_model_plan(
                spec=spec,
                title=title,
                description=description,
                domain_label=domain_label,
                intake=intake,
                trace_id=trace_id,
            )
            if draft is not None:
                return draft
        fallback_reason = (
            "template_only_requested"
            if planning_mode == "template_only"
            else "no_routable_goal_planner_model"
        )
        return self._template_plan(
            spec=spec,
            title=title,
            description=description,
            domain_label=domain_label,
            intake=intake,
            fallback_reason=fallback_reason,
        )

    async def _try_model_plan(
        self,
        *,
        spec: GoalDomainSpec,
        title: str,
        description: str,
        domain_label: str,
        intake: dict[str, Any],
        trace_id: str | None,
    ) -> GoalPlanDraft | None:
        if self._brains is None or self._model_gateway is None:
            return None
        try:
            brains = await self._brains.list_routable_brains()
            candidates = [
                brain
                for brain in brains
                if bool(brain.get("is_local")) or bool(brain.get("allow_cloud"))
            ]
            if not candidates:
                return None
            brain = candidates[0]
            request = ModelChatRequest(
                model=str(brain["model_name"]),
                messages=_goal_planner_messages(
                    title=title,
                    description=description,
                    domain_label=domain_label,
                    intake=intake,
                    spec=spec,
                ),
                temperature=0.2,
                max_output_tokens=int(brain.get("default_max_output_tokens") or 1200),
                top_p=float(brain.get("default_top_p") or 0.9),
                timeout_seconds=min(int(brain.get("timeout_seconds") or 60), 90),
                stream=False,
                trace_id=trace_id or "goal_planner",
                turn_id="goal:planner",
                route_id=f"goal_planner:{brain['brain_id']}",
                privacy_level="normal",
                retry_count=0,
                metadata={"purpose": "goal_planner", "brain_id": brain["brain_id"]},
            )
            result = await self._model_gateway.complete_chat(brain, request, CancelToken())
            payload = _parse_goal_model_json(result.text)
            return _draft_from_model_payload(
                payload,
                title=title,
                description=description,
                domain_label=domain_label,
                intake=intake,
                brain=brain,
                usage=result.usage,
                finish_reason=result.finish_reason,
            )
        except Exception as exc:
            return self._template_plan(
                spec=spec,
                title=title,
                description=description,
                domain_label=domain_label,
                intake=intake,
                fallback_reason=f"model_planner_failed:{type(exc).__name__}",
            )

    def _template_plan(
        self,
        *,
        spec: GoalDomainSpec,
        title: str,
        description: str,
        domain_label: str,
        intake: dict[str, Any],
        fallback_reason: str,
    ) -> GoalPlanDraft:
        model_call = {
            "call_type": "goal_planner",
            "status": "fallback",
            "model_route": {},
            "input_redacted": {
                "title": title,
                "description": description,
                "domain_label": domain_label,
                "intake": intake,
            },
            "output_redacted": {"fallback_template": domain_label},
            "fallback_reason": fallback_reason,
        }
        milestones = [
            {
                **item,
                "acceptance_criteria": [f"能说明“{item['title']}”的完成证据"],
            }
            for item in spec.milestones
        ]
        routines = [
            {
                **item,
                "cadence": {"type": "daily_or_weekly"},
                "difficulty": "easy" if index == 0 else "medium",
            }
            for index, item in enumerate(spec.routines)
        ]
        items = [
            {
                "title": milestone["title"],
                "description": milestone["description"],
                "item_type": (
                    "planning"
                    if index == 0
                    else "routine"
                    if index == 1
                    else "checkin"
                    if index == 2
                    else "review"
                ),
                "cadence": {"type": "once" if index == 0 else "daily_or_weekly"},
                "success_metric": {"type": "evidence", "target": 1},
            }
            for index, milestone in enumerate(milestones[:4])
        ]
        return GoalPlanDraft(
            summary=f"围绕「{title}」按“阶段目标 + 固定动作 + 反馈复盘”推进。",
            success_criteria=[
                "目标被拆成可追踪阶段",
                "每周至少产生一次可复盘反馈",
                "遇到 missed 或 blocked 时能调小下一步动作",
            ],
            assumptions=["先用领域模板生成可执行版本；模型规划接入后可升级为更细的个性化计划。"],
            risk_notes=list(spec.risk_notes),
            items=items,
            milestones=milestones,
            routines=routines,
            model_call=model_call,
            fallback_used=True,
        )


def _goal_planner_messages(
    *,
    title: str,
    description: str,
    domain_label: str,
    intake: dict[str, Any],
    spec: GoalDomainSpec,
) -> list[dict[str, str]]:
    schema = {
        "summary": "short natural-language plan summary",
        "success_criteria": ["observable success criterion"],
        "assumptions": ["planning assumption"],
        "risk_notes": ["risk or safety note"],
        "items": [
            {
                "title": "plan item title",
                "description": "plan item description",
                "item_type": "planning|routine|checkin|review",
                "cadence": {"type": "once|daily_or_weekly"},
                "success_metric": {"type": "evidence", "target": 1},
            }
        ],
        "milestones": [
            {
                "title": "milestone title",
                "description": "milestone description",
                "target_date": None,
                "acceptance_criteria": ["evidence"],
            }
        ],
        "routines": [
            {
                "title": "routine title",
                "description": "routine description",
                "cadence": {"type": "daily_or_weekly"},
                "estimated_minutes": 30,
                "difficulty": "easy|medium|hard",
            }
        ],
    }
    user_payload = {
        "title": title,
        "description": description,
        "domain_label": domain_label,
        "intake": intake,
        "domain_missing_fields": list(spec.missing_fields),
        "expected_schema": schema,
    }
    return [
        {
            "role": "system",
            "content": (
                "You are the Goal Engine planner. Return only strict JSON matching the schema. "
                "Do not suggest real-world actions like payment, registration, purchases, browser use, or tool execution."
            ),
        },
        {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
    ]


def _parse_goal_model_json(text: str) -> dict[str, Any]:
    clean = str(text or "").strip()
    if clean.startswith("```"):
        clean = re.sub(r"^```(?:json)?\s*", "", clean)
        clean = re.sub(r"\s*```$", "", clean)
    start = clean.find("{")
    end = clean.rfind("}")
    if start >= 0 and end > start:
        clean = clean[start : end + 1]
    parsed = json.loads(clean)
    if not isinstance(parsed, dict):
        raise ValueError("goal planner returned non-object JSON")
    return parsed


def _draft_from_model_payload(
    payload: dict[str, Any],
    *,
    title: str,
    description: str,
    domain_label: str,
    intake: dict[str, Any],
    brain: dict[str, Any],
    usage: dict[str, Any],
    finish_reason: str,
) -> GoalPlanDraft:
    items = _list_of_dicts(payload.get("items"))
    milestones = _list_of_dicts(payload.get("milestones"))
    routines = _list_of_dicts(payload.get("routines"))
    if not items or not milestones or not routines:
        raise ValueError("goal planner JSON missing items, milestones, or routines")
    model_call = {
        "call_type": "goal_planner",
        "status": "succeeded",
        "model_route": {
            "brain_id": brain.get("brain_id"),
            "provider": brain.get("provider"),
            "model_name": brain.get("model_name"),
            "is_local": bool(brain.get("is_local")),
        },
        "input_redacted": {
            "title": title,
            "description": description,
            "domain_label": domain_label,
            "intake": intake,
        },
        "output_redacted": {
            "summary": payload.get("summary"),
            "item_count": len(items),
            "milestone_count": len(milestones),
            "routine_count": len(routines),
            "usage": usage,
            "finish_reason": finish_reason,
        },
        "fallback_reason": None,
    }
    return GoalPlanDraft(
        summary=str(payload.get("summary") or f"Plan for {title}"),
        success_criteria=[str(item) for item in list(payload.get("success_criteria") or [])],
        assumptions=[str(item) for item in list(payload.get("assumptions") or [])],
        risk_notes=[str(item) for item in list(payload.get("risk_notes") or [])],
        items=_ensure_plan_item_types([_normalize_plan_item(item) for item in items]),
        milestones=[_normalize_milestone(item) for item in milestones],
        routines=[_normalize_routine(item) for item in routines],
        model_call=model_call,
        fallback_used=False,
    )


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalize_plan_item(item: dict[str, Any]) -> dict[str, Any]:
    item_type = str(item.get("item_type") or "routine")
    if item_type not in {"planning", "routine", "checkin", "review"}:
        item_type = "routine"
    return {
        "title": str(item.get("title") or "Next action"),
        "description": str(item.get("description") or ""),
        "item_type": item_type,
        "cadence": dict(item.get("cadence") or {"type": "daily_or_weekly"}),
        "success_metric": dict(item.get("success_metric") or {"type": "evidence", "target": 1}),
    }


def _ensure_plan_item_types(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    required = {
        "planning": {
            "title": "确认目标边界",
            "description": "明确目标、投入时间、约束和下一步执行范围。",
            "item_type": "planning",
            "cadence": {"type": "once"},
            "success_metric": {"type": "goal_scope_confirmed"},
        },
        "routine": {
            "title": "执行固定行动",
            "description": "按计划推进一个可重复的小行动。",
            "item_type": "routine",
            "cadence": {"type": "daily_or_weekly"},
            "success_metric": {"type": "routine_completed"},
        },
        "checkin": {
            "title": "回复目标监督",
            "description": "按提醒反馈完成、部分完成、未完成或卡住。",
            "item_type": "checkin",
            "cadence": {"type": "daily_or_weekly"},
            "success_metric": {"type": "checkin_reply"},
        },
        "review": {
            "title": "阶段复盘调整",
            "description": "根据最近反馈复盘阻碍、节奏和下一步。",
            "item_type": "review",
            "cadence": {"type": "daily_or_weekly"},
            "success_metric": {"type": "review_done"},
        },
    }
    present = {str(item.get("item_type") or "") for item in items}
    result = list(items)
    if "planning" not in present:
        result.insert(0, dict(required["planning"]))
    for item_type in ("routine", "checkin", "review"):
        if item_type not in present:
            result.append(dict(required[item_type]))
    return result


def _normalize_milestone(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": str(item.get("title") or "Milestone"),
        "description": str(item.get("description") or ""),
        "target_date": item.get("target_date"),
        "acceptance_criteria": [
            str(value) for value in list(item.get("acceptance_criteria") or [])
        ],
    }


def _normalize_routine(item: dict[str, Any]) -> dict[str, Any]:
    minutes = item.get("estimated_minutes")
    return {
        "title": str(item.get("title") or "Routine"),
        "description": str(item.get("description") or ""),
        "cadence": dict(item.get("cadence") or {"type": "daily_or_weekly"}),
        "estimated_minutes": int(minutes) if isinstance(minutes, int | float) else None,
        "difficulty": str(item.get("difficulty") or "medium"),
    }


class GoalProgressEvaluator:
    def parse_status(self, text: str) -> str:
        clean = str(text or "")
        if any(
            marker in clean
            for marker in (
                "卡住",
                "卡在",
                "卡点",
                "瓶颈",
                "不会",
                "不懂",
                "不理解",
                "不知道",
                "困难",
                "困惑",
                "阻碍",
                "阻塞",
                "难点",
            )
        ):
            return "blocked"
        if (
            any(marker in clean for marker in ("\u53ea\u8bfb\u4e86", "\u8bfb\u4e86"))
            and any(marker in clean for marker in ("\u5237\u624b\u673a", "\u5237\u77ed\u89c6\u9891"))
        ):
            return "partial"
        if any(
            marker in clean
            for marker in (
                "\u6ca1\u63a7\u5236\u4f4f",
                "\u5237\u77ed\u89c6\u9891",
                "\u5237\u624b\u673a",
                "\u8d85\u65f6",
                "\u6ca1\u8bad\u7ec3",
                "\u6ca1\u8df3",
                "\u6ca1\u53bb\u8bad\u7ec3",
                "\u6ca1\u62c9\u4f38",
                "\u6ca1\u505a\u7761\u524d\u653e\u677e",
                "\u6ca1\u8bb0\u5f55\u996e\u6c34",
                "\u5fd8\u8bb0\u559d\u6c34",
                "\u4e70\u4e86\u5976\u8336",
                "\u4e70\u751c\u996e",
                "\u5c0f\u817f\u6709\u70b9\u7d27",
                "\u811a\u8e1d\u6709\u70b9\u4e0d\u8212\u670d",
                "\u4e0d\u592a\u8212\u670d",
                "\u6709\u70b9\u4e0d\u8212\u670d",
            )
        ):
            return "missed"
        if (
            any(marker in clean for marker in ("\u4e70\u4e86\u83dc", "\u5907\u83dc", "\u98df\u6750"))
            and any(
                marker in clean
                for marker in (
                    "\u6ca1\u6765\u5f97\u53ca\u505a\u5b8c",
                    "\u6ca1\u65f6\u95f4\u505a\u5b8c",
                    "\u6ca1\u505a\u5b8c",
                    "\u6ca1\u6765\u5f97\u53ca\u5b8c\u6210",
                )
            )
        ):
            return "partial"
        if any(
            marker in clean
            for marker in (
                "\u6ca1\u65f6\u95f4\u5b8c\u6210",
                "\u6ca1\u7a7a\u5b8c\u6210",
                "\u6ca1\u6765\u5f97\u53ca\u5b8c\u6210",
                "\u6765\u4e0d\u53ca\u5b8c\u6210",
                "\u6ca1\u65f6\u95f4\u505a\u5b8c",
                "\u6ca1\u6765\u5f97\u53ca\u505a\u5b8c",
            )
        ):
            return "missed"
        if any(
            marker in clean
            for marker in (
                "做了一半",
                "练了一半",
                "一半",
                "部分",
                "一部分",
                "一点",
                "只",
                "只练",
                "但",
                "还差",
                "没听完",
                "没看完",
                "没做完",
                "推进",
                "有进展",
                "没全部",
                "还没",
                "未完全",
                "停了",
            )
        ):
            return "partial"
        if any(
            marker in clean
            for marker in (
                "没做",
                "没完成",
                "没时间",
                "没来得及",
                "没练",
                "没跑",
                "没看",
                "没复习",
                "没背",
                "没刷题",
                "没刷",
                "没去训练馆",
                "\u6ca1\u8bad\u7ec3",
                "\u6ca1\u53bb\u8bad\u7ec3",
                "\u6ca1\u62c9\u4f38",
                "没去",
                "没控制住",
                "超时",
                "被临时会议打断",
                "打断",
                "来不及",
                "忘了",
                "耽搁",
                "拖延",
                "没有",
                "临时加班",
                "下雨堵车",
                "膝盖酸",
                "有点酸",
                "休息了",
                "太忙",
                "太累",
                "很累",
                "脑子很累",
                "工作太满",
                "会议太多",
                "状态不好",
                "状态不太好",
                "状态一般",
                "沮丧",
            )
        ):
            return "missed"
        if any(
            marker in clean
            for marker in (
                "完成",
                "做完",
                "\u5199\u5b8c",
                "练完",
                "刷完",
                "读完",
                "听完",
                "看完",
                "学完",
                "听写完",
                "整理完",
                "搭完",
                "拍完",
                "背完",
                "跟读完",
                "练习完",
                "搞定",
                "已做",
                "打卡",
                "按计划",
                "done",
                "finished",
            )
        ):
            return "done"
        return "unclear"


class GoalResponsePresenter:
    def intake_question(self, domain_label: str, missing_fields: list[str]) -> str:
        labels = {
            "exam_name": "具体考哪个证",
            "target_date": "预计什么时候考试或达成",
            "available_time": "每天或每周能投入多少时间",
            "target_language": "目标语言",
            "current_level": "当前基础",
            "target_level": "目标水平",
            "language_or_track": "想学的语言或方向",
            "project_goal": "希望做出的项目或应用",
        }
        wanted = "、".join(labels.get(item, item) for item in missing_fields[:3])
        if not wanted:
            return ""
        return f"为了把计划做准，我还需要确认：{wanted}。你可以一句话补充。"

    def created_reply(
        self,
        *,
        title: str,
        plan_items: list[Any],
        domain_label: str,
        missing_fields: list[str],
    ) -> str:
        prefix = f"可以。我先把「{title}」设成一个目标（长期）"
        if domain_label != "general":
            prefix += f"，领域是 {domain_label}"
        lines = [prefix + "，并生成一版可执行计划："]
        for item in plan_items:
            lines.append(f"{item.sort_order}. {item.title}：{item.description}")
        question = self.intake_question(domain_label, missing_fields)
        if question:
            lines.append(question)
        lines.append("你确认后，我可以按你指定的节奏开始温和监督、提醒、记录进度和复盘。")
        return "\n".join(lines)

    def checkin_reply(
        self,
        *,
        goal: Goal,
        progress: GoalProgressSnapshot,
        status: str,
        intervention_summary: str | None = None,
    ) -> str:
        advice = {
            "done": "保持这个节奏，下一次继续同样的小动作。",
            "partial": "已经有进展了，下一次把动作再缩小一点，优先保证能开始。",
            "missed": "没关系，先把下一步降到最小动作，恢复连续性更重要。",
            "blocked": "卡住是计划要调整的信号，先只拆一个阻碍。",
            "unclear": "我先记录下来，后面可以直接说完成、部分、没完成或卡住。",
        }.get(status, "继续记录即可。")
        text = f"收到，已记录到「{goal.title}」。{advice} 当前进度约 {progress.progress_percent}%。"
        if intervention_summary:
            text += f" {intervention_summary}"
        return text


class GoalMemoryProjector:
    def __init__(self, memory_service: Any | None = None) -> None:
        self._memory = memory_service

    async def project_checkin(
        self,
        *,
        goal: Goal,
        checkin_id: str,
        progress: GoalProgressSnapshot,
        parsed_status: str,
        turn_id: str | None,
        trace_id: str | None,
        intervention: Any | None = None,
    ) -> Any | None:
        if self._memory is None:
            return None
        signal_kind = _signal_kind_for_status(parsed_status, intervention)
        summary = _goal_memory_summary(
            goal=goal,
            progress=progress,
            parsed_status=parsed_status,
            intervention=intervention,
        )
        source = {
            "type": "goal_event",
            "goal_id": goal.goal_id,
            "checkin_id": checkin_id,
            "turn_id": turn_id,
            "trace_id": trace_id,
            "intervention_id": getattr(intervention, "intervention_id", None),
        }
        return await self._memory.record_goal_signal(
            summary_text=summary,
            organization_id=goal.organization_id,
            member_id=goal.owner_member_id,
            source=source,
            payload={
                "goal_id": goal.goal_id,
                "checkin_id": checkin_id,
                "parsed_status": parsed_status,
                "progress_percent": progress.progress_percent,
                "blockers": list(progress.blockers or []),
                "next_focus": list(progress.next_focus or []),
            },
            signal_kind=signal_kind,
            trace_id=trace_id,
        )


class GoalRuntime:
    def __init__(self, goal_service: Any) -> None:
        self._goal_service = goal_service

    async def try_handle_turn(
        self,
        *,
        text: str,
        conversation_id: str,
        member_id: str,
        turn_id: str,
        trace_id: str | None,
    ) -> GoalChatOutcome | None:
        return await self._goal_service.try_handle_chat_turn(
            text=text,
            conversation_id=conversation_id,
            member_id=member_id,
            turn_id=turn_id,
            trace_id=trace_id,
        )

    async def handle_scheduled_checkin(
        self,
        *,
        scheduled_task: ScheduledTask,
        scheduled_run_id: str,
        trace_id: str | None,
    ) -> dict[str, Any]:
        return await self._goal_service.handle_scheduled_checkin(
            scheduled_task=scheduled_task,
            scheduled_run_id=scheduled_run_id,
            trace_id=trace_id,
        )


def _signal_kind_for_status(parsed_status: str, intervention: Any | None) -> str:
    if intervention is not None:
        return "goal_intervention"
    if parsed_status == "blocked":
        return "goal_blocker"
    if parsed_status == "done":
        return "goal_routine_effective"
    return "goal_progress"


def _goal_memory_summary(
    *,
    goal: Goal,
    progress: GoalProgressSnapshot,
    parsed_status: str,
    intervention: Any | None,
) -> str:
    blockers = ", ".join(str(item) for item in list(progress.blockers or [])[:2])
    next_focus = ", ".join(str(item) for item in list(progress.next_focus or [])[:2])
    parts = [
        f"Goal '{goal.title}' check-in status: {parsed_status}.",
        f"Progress is about {progress.progress_percent}%.",
    ]
    if blockers:
        parts.append(f"Blockers: {blockers}.")
    if next_focus:
        parts.append(f"Next focus: {next_focus}.")
    if intervention is not None:
        summary = getattr(intervention, "summary", None)
        if summary:
            parts.append(f"Intervention suggested: {summary}.")
    return " ".join(parts)


def extract_goal_intake_from_text(text: str) -> dict[str, Any]:
    clean = " ".join(str(text or "").strip().split())
    intake: dict[str, Any] = {"raw_answers": {}}
    if match := re.search(r"每天\s*(\d+(?:\.\d+)?)\s*(小时|h|分钟|分)", clean, re.IGNORECASE):
        value = float(match.group(1))
        minutes = int(value * 60) if match.group(2) in {"小时", "h"} else int(value)
        intake["available_time"] = {"type": "daily", "minutes": minutes}
        intake["raw_answers"]["available_time"] = match.group(0)
    if match := re.search(r"每周\s*(\d+(?:\.\d+)?)\s*(小时|h|分钟|分)", clean, re.IGNORECASE):
        value = float(match.group(1))
        minutes = int(value * 60) if match.group(2) in {"小时", "h"} else int(value)
        intake["available_time"] = {"type": "weekly", "minutes": minutes}
        intake["raw_answers"]["available_time"] = match.group(0)
    if match := re.search(r"(\d{1,2})\s*月", clean):
        intake["target_date"] = f"{int(match.group(1)):02d}月"
        intake["raw_answers"]["target_date"] = match.group(0)
    for marker in ("软考高项", "软考", "CPA", "PMP", "雅思", "托福"):
        if marker.lower() in clean.lower():
            intake["raw_answers"]["exam_name"] = marker
            break
    for marker in ("Python", "Java", "前端", "后端", "算法"):
        if marker.lower() in clean.lower():
            intake["raw_answers"]["language_or_track"] = marker
            break
    for marker in ("英语", "日语", "韩语", "法语"):
        if marker in clean:
            intake["raw_answers"]["target_language"] = marker
            break
    return intake


def merge_intake(base: dict[str, Any], patch: GoalIntakeUpdateRequest | dict[str, Any]) -> dict[str, Any]:
    patch_data = patch.model_dump(mode="json", exclude_none=True) if hasattr(patch, "model_dump") else dict(patch)
    merged = dict(base or {})
    for key in ("current_level", "target_level", "target_date"):
        if patch_data.get(key):
            merged[key] = patch_data[key]
    for key in ("available_time", "constraints", "motivation", "raw_answers"):
        merged[key] = {**dict(merged.get(key) or {}), **dict(patch_data.get(key) or {})}
    return merged
