from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from app.services.chat_visible_guard import preserve_visible_reply_contract
from app.services.goals import _looks_like_goal_plan_request
from app.services.goal_engine import GoalDomainRegistry, GoalProgressEvaluator


def test_goal_domain_registry_covers_common_goal_domains() -> None:
    registry = GoalDomainRegistry()

    assert registry.classify("\u6211\u8981\u5b66\u4e60\u897f\u73ed\u7259\u8bed") == "language_learning"
    assert registry.classify("\u6211\u8981\u51c6\u5907\u6cd5\u8003") == "exam_certification"
    assert registry.classify("\u6211\u8981\u51c6\u5907 Azure \u7ba1\u7406\u5458\u8ba4\u8bc1\uff0c\u4e09\u4e2a\u6708\u5185\u62ff\u8bc1") == "exam_certification"
    assert registry.classify("\u6211\u60f3\u5b66\u4e2d\u6587\u5e76\u901a\u8fc7 HSK4") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u963f\u62c9\u4f2f\u8bed") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u6cf0\u8bed\u548c\u8d8a\u5357\u8bed") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u5370\u5c3c\u8bed\u548c\u571f\u8033\u5176\u8bed") == "language_learning"
    assert registry.classify("\u6211\u60f3\u51c6\u5907 DELF B2 \u6cd5\u8bed\u8003\u8bd5") == "language_learning"
    assert registry.classify("\u6211\u8981\u5b66\u6e38\u6cf3\u5e76\u63d0\u5347\u8010\u529b") == "fitness"
    assert registry.classify("\u6211\u60f3\u7ec3\u7fbd\u6bdb\u7403\u6b65\u4f10\u548c\u666e\u62c9\u63d0") == "fitness"
    assert registry.classify("\u6211\u8981\u5b66SQL\u548cPython\u6570\u636e\u5206\u6790") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66\u4e60 Rust \u505a CLI \u5de5\u5177") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Unity \u548c C# \u505a 2D \u5c0f\u6e38\u620f") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66 Spark \u548c\u6570\u636e\u5de5\u7a0b") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66 Flutter \u548c Dart \u505a App") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66 R \u8bed\u8a00\u548c\u6570\u636e\u53ef\u89c6\u5316") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66 Swift \u548c iOS \u505a App") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66 Linux \u547d\u4ee4\u884c\u548c Shell \u811a\u672c") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66 Unreal Engine \u548c C++") == "programming_learning"
    assert registry.classify("\u6211\u8981\u5b66 PHP \u548c Laravel") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u642d\u4e00\u4e2a Notion \u4e2a\u4eba\u77e5\u8bc6\u5e93") == "general"
    assert registry.classify("\u6211\u8981\u5b66 Docker \u548c DevOps \u90e8\u7f72") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Excel VBA \u81ea\u52a8\u5316\u548c\u5b8f") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5efa\u7acb\u5065\u5eb7\u996e\u98df\u548c\u5907\u9910\u4e60\u60ef") == "fitness"
    assert registry.classify("\u6211\u60f3\u5b66\u6ce2\u65af\u8bed\u5230 A1") == "language_learning"
    assert registry.classify("\u6211\u8981\u5b66\u745e\u5178\u8bed\u65c5\u884c\u53e3\u8bed") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u8377\u5170\u8bed\u5230 B1 \u65e5\u5e38\u6c9f\u901a") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u6ce2\u5170\u8bed\u5230 A2 \u65e5\u5e38\u4ea4\u6d41") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u632a\u5a01\u8bed\u5e76\u7ec3\u65c5\u884c\u5bf9\u8bdd") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u4e4c\u514b\u5170\u8bed\u5230 A1 \u95ee\u5019") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u6377\u514b\u8bed\u5230 A2 \u65c5\u884c\u6c9f\u901a") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u82ac\u5170\u8bed\u5230 A1 \u95ee\u5019") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u7f57\u9a6c\u5c3c\u4e9a\u8bed\u5230 A1 \u70b9\u9910") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u5308\u7259\u5229\u8bed\u5230 A1 \u95ee\u5019") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u7acb\u9676\u5b9b\u8bed\u5230 A1 \u65c5\u884c\u95ee\u5019") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5efa\u7acb\u7761\u524d\u653e\u677e\u548c\u51cf\u5c11\u5c4f\u5e55\u65f6\u95f4\u4e60\u60ef") == "general"
    assert registry.classify("\u6211\u51c6\u5907 TOEIC \u542c\u529b\uff0c\u60f3\u4e24\u4e2a\u6708\u63d0\u5206") == "language_learning"
    assert registry.classify("\u6211\u8981\u51c6\u5907 SAT Math\uff0c\u60f3\u4e24\u4e2a\u6708\u964d\u4f4e\u9519\u9898") == "exam_certification"
    assert registry.classify("\u6211\u8981\u5b66 Solidity \u548c Web3 \u667a\u80fd\u5408\u7ea6") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 C \u8bed\u8a00\u6570\u636e\u7ed3\u6784\uff0c\u505a\u94fe\u8868\u548c\u6808") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u84dd\u961f\u5b89\u5168\u548c\u65e5\u5fd7\u5206\u6790") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Scala \u548c Akka \u505a\u5e76\u53d1\u670d\u52a1") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Power BI \u505a\u9500\u552e\u4eea\u8868\u76d8\u4f5c\u54c1\u96c6") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Terraform \u548c AWS IaC \u505a\u57fa\u7840\u8bbe\u65bd\u6a21\u677f") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 GraphQL \u548c Apollo \u505a\u67e5\u8be2 API") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Kafka \u548c\u6d41\u5904\u7406\u505a\u5b9e\u65f6\u8ba2\u5355\u7edf\u8ba1") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Redis \u548c\u7f13\u5b58\u8bbe\u8ba1\u505a\u63a5\u53e3\u7f13\u5b58\u4f18\u5316") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Nuxt \u548c Vue \u505a\u7535\u5546\u5c55\u793a\u9875") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Airflow \u548c\u6570\u636e\u7ba1\u9053\u505a ETL DAG") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Remix \u548c Supabase \u505a\u4e66\u7b7e\u5e94\u7528") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Elasticsearch \u548c\u5168\u6587\u68c0\u7d22\u505a\u6587\u6863\u641c\u7d22") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u51c6\u5907 JLPT N2 \u65e5\u8bed\u9605\u8bfb") == "language_learning"
    assert registry.classify("\u6211\u8981\u51c6\u5907 DELE B1 \u897f\u73ed\u7259\u8bed\u8003\u8bd5") == "language_learning"
    assert registry.classify("\u6211\u60f3\u51c6\u5907 IELTS \u82f1\u8bed\u53e3\u8bed 7 \u5206") == "language_learning"
    assert registry.classify("\u6211\u60f3\u51c6\u5907 TEF Canada \u6cd5\u8bed\u542c\u529b\u548c\u53e3\u8bed") == "language_learning"
    assert registry.classify("\u6211\u8981\u51c6\u5907 CILS B1 \u610f\u5927\u5229\u8bed\u8003\u8bd5") == "language_learning"
    assert registry.classify("\u6211\u8981\u51c6\u5907 Goethe-Zertifikat B2 \u5fb7\u8bed\u8003\u8bd5") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66 FastAPI \u548c PostgreSQL \u505a API \u9879\u76ee") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Kubernetes Prometheus Grafana \u53ef\u89c2\u6d4b\u6027") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 LangChain RAG Chroma \u505a\u77e5\u8bc6\u5e93\u95ee\u7b54") == "programming_learning"
    assert registry.classify("\u6211\u60f3\u5b66 Blazor \u548c .NET \u505a\u6307\u6807\u770b\u677f") == "programming_learning"
    assert registry.classify("\u6211\u51c6\u5907 GRE \u9605\u8bfb\u548c\u5199\u4f5c") == "language_learning"
    assert registry.classify("\u6211\u60f3\u5b66\u8df3\u821e\uff0c\u63d0\u5347\u8282\u594f\u548c\u8eab\u4f53\u534f\u8c03") == "fitness"
    assert registry.classify("\u6211\u60f3\u7ec3\u6500\u5ca9\uff0c\u63d0\u5347\u63e1\u529b\u548c\u8def\u7ebf\u9605\u8bfb") == "fitness"
    assert registry.classify("\u6211\u60f3\u5efa\u7acb\u80a9\u9888\u62c9\u4f38\u548c\u653e\u677e\u4e60\u60ef") == "fitness"
    assert registry.classify("\u6211\u60f3\u7ec3\u592a\u6781\u548c\u8eab\u4f53\u5e73\u8861") == "fitness"
    assert registry.classify("\u6211\u60f3\u5efa\u7acb\u6bcf\u5929\u559d\u6c34\u548c\u51cf\u5c11\u542b\u7cd6\u996e\u6599\u4e60\u60ef") == "fitness"


def test_goal_plan_request_recognizes_long_term_finance_habits() -> None:
    assert _looks_like_goal_plan_request(
        "\u6211\u60f3\u5efa\u7acb\u957f\u671f\u7406\u8d22\u548c\u6295\u8d44\u590d\u76d8\u4e60\u60ef\uff0c"
        "\u6bcf\u5468\u8bb0\u5f55\u8d44\u4ea7\u548c\u51b3\u7b56\uff0c\u8bf7\u76d1\u7763\u3002"
    )
    assert _looks_like_goal_plan_request(
        "\u6211\u51c6\u5907 TOEIC \u542c\u529b\uff0c\u60f3\u4e24\u4e2a\u6708\u628a\u542c\u529b\u5206\u6570"
        "\u63d0\u5230 400\uff0c\u8bf7\u6bcf\u5929\u966a\u7ec3\u3002"
    )
    assert _looks_like_goal_plan_request(
        "\u6211\u60f3\u7ec3\u666e\u62c9\u63d0\u6838\u5fc3\uff0c\u6539\u5584\u4f53\u6001\u548c"
        "\u547c\u5438\u63a7\u5236\uff0c\u8bf7\u6bcf\u5929\u63d0\u9192\u6211\u3002"
    )


def test_visible_guard_preserves_goal_plan_for_finance_habit() -> None:
    request = (
        "\u6211\u60f3\u5efa\u7acb\u957f\u671f\u7406\u8d22\u548c\u6295\u8d44\u590d\u76d8\u4e60\u60ef\uff0c"
        "\u6bcf\u5468\u8bb0\u5f55\u8d44\u4ea7\u548c\u51b3\u7b56\uff0c\u8bf7\u76d1\u7763\u3002"
    )
    visible = (
        "\u53ef\u4ee5\u3002\u6211\u5148\u628a\u300c\u5efa\u7acb\u957f\u671f\u7406\u8d22\u548c\u6295\u8d44\u590d\u76d8\u4e60\u60ef\u300d"
        "\u8bbe\u6210\u4e00\u4e2a\u76ee\u6807\uff08\u957f\u671f\uff09\uff0c\u5e76\u751f\u6210\u4e00\u7248\u53ef\u6267\u884c\u8ba1\u5212\u3002"
    )

    repaired = preserve_visible_reply_contract(visible, user_text=request)

    assert "\u76ee\u6807" in repaired
    assert "\u8ba1\u5212" in repaired
    assert "\u9a6c\u4e0a\u66b4\u6da8" not in repaired


def test_goal_progress_evaluator_parses_natural_feedback_variants() -> None:
    evaluator = GoalProgressEvaluator()

    assert evaluator.parse_status("\u4eca\u5929\u7ec3\u5b8c\u4e86\u53d1\u97f3\u548c\u5341\u4e2a\u95ee\u5019\u53e5") == "done"
    assert evaluator.parse_status("\u4eca\u5929\u5237\u5b8c\u4e86\u7b2c\u4e00\u7ae0\u9898\u5e93") == "done"
    assert evaluator.parse_status("\u4eca\u5929\u542c\u5199\u5b8c\u4e86\u4e8c\u5341\u4e2a\u8bcd") == "done"
    assert evaluator.parse_status("\u4eca\u5929\u6574\u7406\u5b8c\u4e86\u4e00\u4e2a\u62bd\u5c49") == "done"
    assert evaluator.parse_status("\u4eca\u5929\u642d\u5b8c\u4e86\u9605\u8bfb\u6570\u636e\u5e93") == "done"
    assert evaluator.parse_status("\u4eca\u5929\u62cd\u5b8c\u4e86\u5341\u5f20\u7ec3\u4e60\u7167\u7247") == "done"
    assert evaluator.parse_status("\u4eca\u5929\u5b8c\u6210\u4e86\u72ec\u7acb\u5199\u4f5c\u63d0\u7eb2\u548c\u4e00\u6bb5\u53e3\u8bed\u5f55\u97f3") == "done"
    assert evaluator.parse_status("\u4eca\u5929\u5361\u5728\u6240\u6709\u6743\u548c\u501f\u7528\u89c4\u5219") == "blocked"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u8dd1\uff0c\u819d\u76d6\u6709\u70b9\u4e0d\u8212\u670d") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u5199\u4e86\u5f00\u5934\u548c\u63d0\u7eb2\uff0c\u6b63\u6587\u8fd8\u6ca1\u5c55\u5f00") == "partial"
    assert evaluator.parse_status("\u4eca\u5929\u53ea\u7ec3\u4e86 15 \u5206\u949f\uff0c\u542c\u529b\u6750\u6599\u6ca1\u542c\u5b8c") == "partial"
    assert evaluator.parse_status("\u4eca\u5929\u4e70\u4e86\u83dc\uff0c\u4f46\u6ca1\u6765\u5f97\u53ca\u505a\u996d") == "partial"
    assert evaluator.parse_status("\u4eca\u5929\u4e70\u4e86\u83dc\uff0c\u4f46\u6ca1\u6765\u5f97\u53ca\u505a\u5b8c\u4e09\u5929\u5907\u9910") == "partial"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u7ec3\uff0c\u4f1a\u8bae\u592a\u591a\u4e86") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u590d\u4e60\uff0c\u4e34\u65f6\u6709\u4e8b\u803d\u6401\u4e86") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u770b\u8bfe\u7a0b\uff0c\u88ab\u4e34\u65f6\u4f1a\u8bae\u6253\u65ad\u4e86") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u8fd8\u662f\u6ca1\u770b\uff0c\u665a\u4e0a\u592a\u7d2f") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u5237\u9898\uff0c\u665a\u4e0a\u4e34\u65f6\u52a0\u73ed") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u53bb\u8bad\u7ec3\u9986\uff0c\u4e0b\u96e8\u5835\u8f66") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u8fd8\u662f\u6ca1\u80cc\uff0c\u592a\u56f0\u4e86") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u53ea\u5199\u4e86\u5f00\u5934\uff0c\u6ca1\u65f6\u95f4\u5b8c\u6210\u6574\u7bc7\u4f5c\u6587") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u8bad\u7ec3\uff0c\u819d\u76d6\u6709\u70b9\u9178") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u8bad\u7ec3\uff0c\u811a\u8e1d\u6709\u70b9\u4e0d\u8212\u670d") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u8fd8\u662f\u6ca1\u8df3\uff0c\u811a\u8e1d\u4e0d\u592a\u8212\u670d") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u62c9\u4f38\uff0c\u5c0f\u817f\u6709\u70b9\u7d27") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u53c8\u5237\u624b\u673a\u5230\u5f88\u665a\uff0c\u6ca1\u6709\u505a\u7761\u524d\u653e\u677e") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u53ea\u8bfb\u4e86 10 \u5206\u949f\uff0c\u540e\u9762\u53c8\u5237\u624b\u673a\u4e86") == "partial"
    assert evaluator.parse_status("\u4eca\u5929\u6ca1\u8bb0\u5f55\u996e\u6c34\uff0c\u4e0b\u5348\u8fd8\u662f\u4e70\u4e86\u5976\u8336") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u53c8\u5237\u77ed\u89c6\u9891\uff0c\u6ca1\u63a7\u5236\u4f4f") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u8fd8\u662f\u8d85\u65f6\u4e86\uff0c\u6709\u70b9\u6cae\u4e27") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u53c8\u5237\u77ed\u89c6\u9891\u5230\u4e00\u70b9\uff0c\u6ca1\u63a7\u5236\u4f4f") == "missed"
    assert evaluator.parse_status("\u4eca\u5929\u5199\u5b8c\u4e86\u4e00\u7bc7\u8bae\u8bba\u6587\u63d0\u7eb2\u548c\u7b2c\u4e00\u6bb5") == "done"


def test_goal_engine_exam_intake_replan_and_timeline(client: TestClient) -> None:
    created = client.post(
        "/api/goals",
        json={
            "conversation_id": _conversation_id(client),
            "owner_member_id": "mem_xiaoyao",
            "description": "我要考证，帮我安排一下。",
        },
    )
    assert created.status_code == 200, created.text
    body = created.json()
    goal = body["goal"]

    assert goal["domain_label"] == "exam_certification"
    assert body["intake"]["status"] == "collecting"
    assert "exam_name" in body["intake"]["missing_fields"]
    assert body["milestones"]
    assert body["routines"]

    model_calls = client.get(f"/api/goals/{goal['goal_id']}/model-calls").json()["items"]
    assert model_calls[0]["status"] in {"fallback", "succeeded"}
    if model_calls[0]["status"] == "fallback":
        assert model_calls[0]["fallback_reason"]

    updated = client.post(
        f"/api/goals/{goal['goal_id']}/intake",
        json={
            "target_date": "11月",
            "available_time": {"type": "daily", "minutes": 60},
            "raw_answers": {"exam_name": "软考高项"},
        },
    )
    assert updated.status_code == 200, updated.text
    detail = updated.json()
    assert detail["active_plan"]["version"] == 2
    assert detail["intake"]["missing_fields"] == []
    assert len(detail["milestones"]) >= 4

    timeline = client.get(f"/api/goals/{goal['goal_id']}/timeline").json()["items"]
    assert any(item["kind"] == "event" for item in timeline)


def test_goal_engine_checkin_intervention_and_chat_runtime(client: TestClient) -> None:
    conversation_id = _conversation_id(client)
    first = _chat_reply(
        client,
        conversation_id=conversation_id,
        session_id="goal-engine-create",
        text="我要学习编程，帮我制定一个计划。",
    )
    assert "目标（长期）" in first
    goal = client.get("/api/goals", params={"conversation_id": conversation_id}).json()["items"][0]
    detail = client.get(f"/api/goals/{goal['goal_id']}").json()
    assert detail["goal"]["domain_label"] == "programming_learning"

    confirmed = client.post(
        f"/api/goals/{goal['goal_id']}/plans/{detail['active_plan']['goal_plan_id']}/confirm",
        json={
            "start_supervision": True,
            "supervision": {"schedule": {"type": "daily", "time": "21:00"}},
        },
    )
    assert confirmed.status_code == 200, confirmed.text

    checkin = client.post(f"/api/goals/{goal['goal_id']}/checkins", json={}).json()
    for text in ("没时间", "没做"):
        progress = client.post(
            f"/api/goals/{goal['goal_id']}/checkins/{checkin['checkin_id']}/reply",
            json={"reply_text": text},
        )
        assert progress.status_code == 200, progress.text
        checkin = client.post(f"/api/goals/{goal['goal_id']}/checkins", json={}).json()

    after = client.get(f"/api/goals/{goal['goal_id']}").json()
    assert after["latest_intervention"]["trigger_type"] == "consecutive_missed"
    assert after["progress"]["missed_count"] >= 2

    candidates = client.get(
        "/api/memory/candidates",
        params={"member_id": "mem_xiaoyao", "limit": 20},
    ).json()["items"]
    goal_candidates = [
        item
        for item in candidates
        if item["source"].get("type") == "goal_event"
        and item["source"].get("goal_id") == goal["goal_id"]
    ]
    assert goal_candidates
    assert any(
        item["proposed_kind"] in {"goal_progress", "goal_blocker"}
        for item in goal_candidates
    )


def test_goal_engine_runtime_contracts(client: TestClient) -> None:
    contracts = client.get("/api/system/runtime-contracts").json()["items"]
    by_name = {item["name"]: item for item in contracts}

    assert by_name["GoalSupportService"]["details"]["domain_specific"] is True
    assert by_name["GoalEngineRuntime"]["status"] == "implemented"
    assert by_name["GoalDomainRegistry"]["status"] == "implemented"
    assert by_name["GoalModelPlanner"]["status"] == "model_first_with_template_fallback"
    assert by_name["GoalMemoryProjection"]["details"]["source_type"] == "goal_event"


def _conversation_id(client: TestClient) -> str:
    return client.get("/api/chat/conversations").json()["items"][0]["conversation_id"]


def _chat_reply(
    client: TestClient,
    *,
    conversation_id: str,
    text: str,
    session_id: str,
) -> str:
    created = client.post(
        "/api/chat/turn",
        json={
            "conversation_id": conversation_id,
            "member_id": "mem_xiaoyao",
            "session_id": session_id,
            "input": {"type": "text", "text": text},
        },
    )
    assert created.status_code == 200, created.text
    stream = client.get(created.json()["stream_url"])
    return _reply_from_sse(stream.text)


def _reply_from_sse(raw: str) -> str:
    chunks: list[str] = []
    fallback = ""
    for block in raw.strip().split("\n\n"):
        for line in block.splitlines():
            if not line.startswith("data: "):
                continue
            event: dict[str, Any] = json.loads(line[6:])
            if event.get("event") == "response.delta":
                chunks.append(str(event.get("payload", {}).get("text") or ""))
            if event.get("event") == "response.completed":
                response_plan = event.get("payload", {}).get("response_plan", {})
                fallback = str(
                    response_plan.get("plain_text") or response_plan.get("summary") or ""
                )
    return "".join(chunks).strip() or fallback.strip()
