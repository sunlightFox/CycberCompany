from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin, urlparse

from core_types import ExternalPlatformActionPlan
from trace_service import redact

from app.core.time import new_id
from app.db.repositories.external_platform_repo import ExternalPlatformRepository
from app.schemas.external_platform_adapters import ExternalPlatformDiscoveryResult
from app.schemas.tasks import ToolExecuteRequest
from app.services.tools import ToolRuntime

PUBLISH_ENTRY_MARKERS = (
    "发布",
    "发文",
    "发动态",
    "发帖",
    "创作",
    "写文章",
    "new post",
    "create",
    "compose",
    "write",
    "publish",
    "post",
)
TITLE_FIELD_MARKERS = ("title", "subject", "标题", "题目")
BODY_FIELD_MARKERS = (
    "body",
    "content",
    "正文",
    "内容",
    "article",
    "post",
    "text",
    "editor",
    "message",
)
CHALLENGE_MARKERS = (
    "captcha",
    "验证码",
    "二次验证",
    "risk check",
    "人机验证",
    "安全验证",
    "verify you are human",
    "login required",
    "请登录",
    "sign in",
)


@dataclass(frozen=True)
class DiscoveryCandidate:
    result: ExternalPlatformDiscoveryResult
    manifest: dict[str, Any] | None = None
    allowed_domains: list[str] | None = None


class ExternalPlatformDiscoveryService:
    def __init__(
        self,
        *,
        platform_repo: ExternalPlatformRepository,
        tool_runtime: ToolRuntime,
    ) -> None:
        self._platform_repo = platform_repo
        self._tools = tool_runtime

    async def discover_browser_adapter(
        self,
        plan: ExternalPlatformActionPlan,
        *,
        trace_id: str | None = None,
    ) -> DiscoveryCandidate:
        discovery_id = new_id("epdisc")
        target = await self._target_for_plan(plan)
        start_url, direct_publish_url = _discovery_urls(plan, target)
        session_handle_id = _discovery_session_handle(plan, target)
        if not start_url:
            return self._candidate(
                discovery_id=discovery_id,
                plan=plan,
                status="failed",
                failure_reason="discovery_start_url_missing",
                message="我还不知道这个平台从哪个网址开始操作，需要先补充平台入口或浏览器会话配置。",
            )

        start_snapshot = await self._snapshot(
            plan,
            url=start_url,
            trace_id=trace_id,
            label="autonomous_discovery_start",
            session_handle_id=session_handle_id,
        )
        evidence_refs = [_evidence_ref(start_snapshot)]
        if _action_failed(start_snapshot):
            return self._candidate(
                discovery_id=discovery_id,
                plan=plan,
                status="failed",
                failure_reason="discovery_start_page_failed",
                message="我打开平台入口时没有拿到可用页面，暂时不能继续准备草稿。",
                evidence_refs=evidence_refs,
            )
        start_html = _snapshot_text(start_snapshot)
        challenge = _challenge_reason(start_html)
        if challenge:
            return self._candidate(
                discovery_id=discovery_id,
                plan=plan,
                status="challenge_detected",
                failure_reason=challenge,
                message="遇到验证码/二次验证，需要你本人处理后我继续。",
                evidence_refs=evidence_refs,
            )

        publish_url = direct_publish_url or _find_publish_url(start_html, start_snapshot, start_url)
        if not publish_url:
            form_candidate = _learn_publish_form(start_html)
            if form_candidate is None:
                return self._candidate(
                    discovery_id=discovery_id,
                    plan=plan,
                    status="failed",
                    failure_reason="publish_entry_not_found",
                    message=(
                        "我没有稳定识别到发布入口，已停止在提交前。"
                        "你可以补充发布页网址或配置 adapter 后继续。"
                    ),
                    evidence_refs=evidence_refs,
                )
            publish_url = str(start_snapshot.get("url") or start_url)
        else:
            publish_snapshot = await self._snapshot(
                plan,
                url=publish_url,
                trace_id=trace_id,
                label="autonomous_discovery_publish",
                session_handle_id=session_handle_id,
            )
            evidence_refs.append(_evidence_ref(publish_snapshot))
            if _action_failed(publish_snapshot):
                return self._candidate(
                    discovery_id=discovery_id,
                    plan=plan,
                    status="failed",
                    failure_reason="publish_page_failed",
                    message="我找到疑似发布入口，但打开发布页失败，暂时不能继续准备草稿。",
                    evidence_refs=evidence_refs,
                )
            publish_html = _snapshot_text(publish_snapshot)
            challenge = _challenge_reason(publish_html)
            if challenge:
                return self._candidate(
                    discovery_id=discovery_id,
                    plan=plan,
                    status="challenge_detected",
                    failure_reason=challenge,
                    message="遇到验证码/二次验证，需要你本人处理后我继续。",
                    evidence_refs=evidence_refs,
                )
            form_candidate = _learn_publish_form(publish_html)

        if form_candidate is None:
            return self._candidate(
                discovery_id=discovery_id,
                plan=plan,
                status="failed",
                failure_reason="publish_form_not_found",
                message="我打开了发布页，但没有稳定识别到标题/正文/提交控件，已停止在提交前。",
                evidence_refs=evidence_refs,
            )

        allowed_domains = sorted(
            {
                _domain(start_url),
                _domain(publish_url),
                *[
                    str(item)
                    for item in (target or {}).get("allowed_domains", [])
                    if str(item).strip()
                ],
            }
            - {""}
        )
        manifest = {
            "start_url": publish_url,
            "allowed_domains": allowed_domains,
            "browser_session_handle_id": session_handle_id,
            "publish_flow": {
                "start_url": publish_url,
                "browser_session_handle_id": session_handle_id,
                "default_title": str(plan.content_summary or "外部平台发布")[:60],
                "selectors": form_candidate["selectors"],
                "verify": {"expected_url": publish_url, "evidence": "post_submit_snapshot"},
            },
            "challenge_detection": {
                "any_text": [
                    "captcha",
                    "验证码",
                    "二次验证",
                    "risk check",
                    "人机验证",
                    "安全验证",
                    "verify you are human",
                ],
                "not_logged_in_text": ["login required", "请登录", "sign in"],
            },
            "autonomous_discovery": {
                "source": "autonomous_discovery",
                "discovery_id": discovery_id,
                "confidence": form_candidate["confidence"],
                "evidence_ref_count": len(evidence_refs),
            },
        }
        return self._candidate(
            discovery_id=discovery_id,
            plan=plan,
            status="draft_prepared",
            failure_reason=None,
            message="我先打开平台并准备草稿。已找到发布入口，接下来会填好草稿，真正发布前会等你确认。",
            manifest=manifest,
            allowed_domains=allowed_domains,
            evidence_refs=evidence_refs,
            confidence=float(form_candidate["confidence"]),
            learned_adapter_manifest=_manifest_summary(manifest),
        )

    async def _target_for_plan(self, plan: ExternalPlatformActionPlan) -> dict[str, Any] | None:
        if plan.target_id:
            target = await self._platform_repo.get_target(plan.target_id)
            if target is not None:
                return target
        if plan.platform_key:
            return await self._platform_repo.get_target_by_key(
                str(plan.platform_key),
                organization_id=plan.organization_id,
            )
        return None

    async def _snapshot(
        self,
        plan: ExternalPlatformActionPlan,
        *,
        url: str,
        trace_id: str | None,
        label: str,
        session_handle_id: str | None,
    ) -> dict[str, Any]:
        args = {"url": url, "display_name": label}
        if session_handle_id:
            args["session_handle_id"] = session_handle_id
        response = await self._tools.execute(
            ToolExecuteRequest(
                task_id=plan.task_id,
                member_id=plan.member_id,
                tool_name="browser.snapshot",
                args=args,
            ),
            trace_id=trace_id or plan.trace_id,
        )
        result = response.result
        return result if isinstance(result, dict) else {"value": result}

    def _candidate(
        self,
        *,
        discovery_id: str,
        plan: ExternalPlatformActionPlan,
        status: str,
        failure_reason: str | None,
        message: str,
        manifest: dict[str, Any] | None = None,
        allowed_domains: list[str] | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        confidence: float = 0,
        learned_adapter_manifest: dict[str, Any] | None = None,
    ) -> DiscoveryCandidate:
        result = ExternalPlatformDiscoveryResult(
            discovery_id=discovery_id,
            plan_id=plan.plan_id,
            platform_key=str(plan.platform_key or ""),
            action_type=plan.action_type,
            status=status,
            learned_adapter_manifest=learned_adapter_manifest or {},
            confidence=confidence,
            evidence_refs=evidence_refs or [],
            failure_reason=failure_reason,
            user_visible_message=message,
        )
        return DiscoveryCandidate(
            result=result,
            manifest=manifest,
            allowed_domains=allowed_domains,
        )


class _DiscoveryHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[dict[str, str]] = []
        self.forms: list[dict[str, Any]] = []
        self.controls: list[dict[str, str]] = []
        self._form_stack: list[dict[str, Any]] = []
        self._link_index: int | None = None

    @classmethod
    def from_text(cls, text: str) -> _DiscoveryHtml:
        parser = cls()
        parser.feed(text)
        parser.close()
        return parser

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "a" and values.get("href"):
            self.links.append({**values, "text": ""})
            self._link_index = len(self.links) - 1
        if tag == "form":
            self._form_stack.append(
                {
                    "id": values.get("id"),
                    "action": values.get("action"),
                    "method": (values.get("method") or "get").lower(),
                    "controls": [],
                }
            )
        if tag in {"input", "textarea", "button"} or values.get("contenteditable") == "true":
            control = {**values, "tag": tag}
            self.controls.append(control)
            if self._form_stack:
                self._form_stack[-1]["controls"].append(control)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a":
            self._link_index = None
        if tag == "form" and self._form_stack:
            self.forms.append(self._form_stack.pop())

    def handle_data(self, data: str) -> None:
        if self._link_index is not None:
            self.links[self._link_index]["text"] += data


def _discovery_urls(
    plan: ExternalPlatformActionPlan,
    target: dict[str, Any] | None,
) -> tuple[str | None, str | None]:
    configs = _discovery_configs(plan.metadata)
    if target:
        configs.extend(_discovery_configs(target.get("metadata") or {}))
    direct = _first_url_value(
        configs,
        ("publish_url", "compose_url", "editor_url", "create_url"),
    )
    start = _first_url_value(
        configs,
        ("start_url", "home_url", "homepage_url", "url", "base_url"),
    )
    return start or direct, direct


def _discovery_session_handle(
    plan: ExternalPlatformActionPlan,
    target: dict[str, Any] | None,
) -> str | None:
    configs = _discovery_configs(plan.metadata)
    if target:
        configs.extend(_discovery_configs(target.get("metadata") or {}))
    for config in configs:
        value = str(
            config.get("session_handle_id")
            or config.get("browser_session_handle_id")
            or ""
        ).strip()
        if value:
            return value
    return None


def _discovery_configs(metadata: dict[str, Any]) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    if isinstance(metadata, dict):
        configs.append(metadata)
        for key in (
            "autonomous_browser_discovery",
            "autonomous_discovery",
            "browser_discovery",
            "discovery",
            "browser",
        ):
            value = metadata.get(key)
            if isinstance(value, dict):
                configs.append(value)
    return configs


def _first_url_value(configs: list[dict[str, Any]], keys: tuple[str, ...]) -> str | None:
    for config in configs:
        for key in keys:
            value = str(config.get(key) or "").strip()
            if value.startswith(("http://", "https://")):
                return value
    return None


def _find_publish_url(html: str, snapshot: dict[str, Any], fallback_url: str) -> str | None:
    parser = _DiscoveryHtml.from_text(html)
    base = str(snapshot.get("url") or fallback_url)
    for link in parser.links:
        haystack = " ".join(
            str(link.get(key) or "")
            for key in ("text", "href", "id", "class", "aria-label", "title")
        ).lower()
        if any(marker.lower() in haystack for marker in PUBLISH_ENTRY_MARKERS):
            return urljoin(base, str(link.get("href") or ""))
    return None


def _learn_publish_form(html: str) -> dict[str, Any] | None:
    parser = _DiscoveryHtml.from_text(html)
    controls = parser.forms[0]["controls"] if parser.forms else parser.controls
    title = _selector_for_control(controls, TITLE_FIELD_MARKERS)
    body = _selector_for_control(controls, BODY_FIELD_MARKERS, prefer_textarea=True)
    if body is None:
        body = _first_text_control_selector(controls, skip=title)
    form_selector = _form_selector(parser.forms[0]) if parser.forms else None
    submit = form_selector or _submit_selector(controls)
    if body is None or submit is None:
        return None
    selectors = {"body": body, "submit": submit}
    if title:
        selectors["title"] = title
    if form_selector:
        selectors["form"] = form_selector
    confidence = 0.72 + (0.1 if title else 0) + (0.1 if form_selector else 0)
    return {"selectors": selectors, "confidence": min(round(confidence, 2), 0.95)}


def _selector_for_control(
    controls: list[dict[str, str]],
    markers: tuple[str, ...],
    *,
    prefer_textarea: bool = False,
) -> str | None:
    ordered = sorted(
        controls,
        key=lambda item: 0 if prefer_textarea and item.get("tag") == "textarea" else 1,
    )
    for control in ordered:
        haystack = _control_haystack(control)
        if any(marker.lower() in haystack for marker in markers):
            selector = _control_selector(control)
            if selector:
                return selector
    return None


def _first_text_control_selector(
    controls: list[dict[str, str]],
    *,
    skip: str | None,
) -> str | None:
    for control in controls:
        tag = control.get("tag")
        input_type = str(control.get("type") or "text").lower()
        if tag not in {"textarea", "input"} and control.get("contenteditable") != "true":
            continue
        if tag == "input" and input_type not in {"", "text", "search"}:
            continue
        selector = _control_selector(control)
        if selector and selector != skip:
            return selector
    return None


def _control_selector(control: dict[str, str]) -> str | None:
    if control.get("id"):
        return f"#{control['id']}"
    if control.get("name"):
        return f"[name='{control['name']}']"
    if control.get("contenteditable") == "true":
        return "[contenteditable='true']"
    return None


def _form_selector(form: dict[str, Any]) -> str | None:
    form_id = str(form.get("id") or "").strip()
    return f"#{form_id}" if form_id else "form"


def _submit_selector(controls: list[dict[str, str]]) -> str | None:
    for control in controls:
        tag = control.get("tag")
        text = _control_haystack(control)
        if tag == "button" or str(control.get("type") or "").lower() == "submit":
            if any(marker.lower() in text for marker in PUBLISH_ENTRY_MARKERS):
                return _control_selector(control) or "button[type='submit']"
    return None


def _control_haystack(control: dict[str, str]) -> str:
    return " ".join(
        str(control.get(key) or "")
        for key in ("id", "name", "placeholder", "aria-label", "title", "class", "type")
    ).lower()


def _snapshot_text(snapshot: dict[str, Any]) -> str:
    return str(snapshot.get("snapshot") or snapshot.get("content_preview") or "")


def _challenge_reason(html: str) -> str | None:
    lowered = html.lower()
    for marker in CHALLENGE_MARKERS:
        if marker.lower() in lowered:
            return "challenge_detected"
    return None


def _action_failed(snapshot: dict[str, Any]) -> bool:
    status = str(snapshot.get("action_status") or "").lower()
    return status in {"failed", "timeout", "http_error", "unsupported", "not_found"}


def _evidence_ref(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in {
            "tool_call_id": snapshot.get("tool_call_id"),
            "browser_evidence_id": snapshot.get("browser_evidence_id"),
            "url": snapshot.get("url"),
            "title": snapshot.get("title"),
            "http_status": snapshot.get("http_status"),
            "action_status": snapshot.get("action_status"),
        }.items()
        if value is not None
    }


def _manifest_summary(manifest: dict[str, Any]) -> dict[str, Any]:
    raw_flow = manifest.get("publish_flow")
    flow: dict[str, Any] = raw_flow if isinstance(raw_flow, dict) else {}
    raw_selectors = flow.get("selectors")
    selectors: dict[str, Any] = raw_selectors if isinstance(raw_selectors, dict) else {}
    raw_challenge_detection = manifest.get("challenge_detection")
    challenge_detection: dict[str, Any] = (
        raw_challenge_detection if isinstance(raw_challenge_detection, dict) else {}
    )
    return {
        "start_url": str(redact(manifest.get("start_url"))),
        "allowed_domains": manifest.get("allowed_domains") or [],
        "selector_keys": sorted(str(key) for key in selectors),
        "challenge_detection": {
            "any_text_count": len(challenge_detection.get("any_text") or []),
            "not_logged_in_text_count": len(
                challenge_detection.get("not_logged_in_text") or []
            ),
        },
        "source": "autonomous_discovery",
        "secret_material_visible": False,
    }


def _domain(url: str) -> str:
    parsed = urlparse(url)
    return (parsed.hostname or parsed.netloc or "").lower()
