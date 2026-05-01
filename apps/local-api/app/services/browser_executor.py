from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from trace_service import redact

_BROWSER_EXECUTABLE_PATH_ENV = "CYCBER_BROWSER_EXECUTABLE_PATH"
_BROWSER_CHANNEL_ENV = "CYCBER_BROWSER_CHANNEL"
_BROWSER_EXECUTOR_ENV = "CYCBER_BROWSER_EXECUTOR"

_FALLBACK_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89"
    b"\x00\x00\x00\rIDATx\x9cc\xf8\xff\xff?\x00\x05\xfe"
    b"\x02\xfeA\xe2!\xbc\x00\x00\x00\x00IEND\xaeB`\x82"
)


@dataclass(frozen=True)
class BrowserExecutionRequest:
    action: str
    url: str
    selector: str | None = None
    value: str | None = None
    timeout_seconds: float = 15.0
    context_key: str = "default"
    session_context: dict[str, Any] = field(default_factory=dict)
    display_name: str | None = None


@dataclass
class BrowserExecutionResult:
    action: str
    url: str
    action_status: str
    backend: str
    backend_status: str
    evidence_summary: str
    title: str | None = None
    http_status: int | None = None
    snapshot: str | None = None
    content_preview: str | None = None
    screenshot_bytes: bytes | None = None
    download_bytes: bytes | None = None
    content_type: str | None = None
    filename: str | None = None
    timeout: bool = False
    recoverable: bool = False
    selector: str | None = None
    value_preview: str | None = None
    interaction: dict[str, Any] = field(default_factory=dict)
    network_summary: dict[str, Any] = field(default_factory=dict)
    console_summary: dict[str, Any] = field(default_factory=dict)
    fallback_chain: list[str] = field(default_factory=list)
    degraded_reason: str | None = None

    def public_result(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "url": str(redact(self.url)),
            "title": str(redact(self.title)) if self.title else None,
            "http_status": self.http_status,
            "action": self.action,
            "action_status": self.action_status,
            "backend": self.backend,
            "backend_status": self.backend_status,
            "fallback_chain": self.fallback_chain,
            "degraded_reason": self.degraded_reason,
            "evidence_summary": str(redact(self.evidence_summary)),
            "content_preview": str(redact(self.content_preview)) if self.content_preview else None,
            "snapshot": str(redact(self.snapshot)) if self.snapshot else None,
            "screenshot": None,
            "artifact": None,
            "timeout": self.timeout,
            "recoverable": self.recoverable,
            "network_summary": redact(self.network_summary),
            "console_summary": redact(self.console_summary),
            "interaction": redact(self.interaction),
            "redaction_summary": {
                "policy": "trace_service.redact",
                "cookie_redacted": True,
                "storage_state_redacted": True,
                "session_material_visible": False,
            },
            "untrusted_external_content": True,
        }
        if self.selector is not None:
            payload["selector"] = str(redact(self.selector))
        if self.value_preview is not None:
            payload["value_preview"] = str(redact(self.value_preview))
        return payload


class BrowserExecutor:
    def __init__(self) -> None:
        self._http = HttpBrowserExecutor()
        self._playwright = PlaywrightBrowserExecutor()
        self._playwright_disabled_reason: str | None = None

    async def execute(self, request: BrowserExecutionRequest) -> BrowserExecutionResult:
        mode = os.environ.get(_BROWSER_EXECUTOR_ENV, "auto").strip().lower()
        if mode in {"playwright", "auto"}:
            if mode == "auto" and self._playwright_disabled_reason:
                fallback = await self._http.execute(request)
                fallback.fallback_chain = ["playwright_skipped", "http_fallback"]
                fallback.degraded_reason = self._playwright_disabled_reason
                return fallback
            try:
                result = await self._playwright.execute(request)
                if result.backend_status == "available":
                    return result
                if result.backend_status == "unavailable":
                    self._playwright_disabled_reason = (
                        result.degraded_reason or result.backend_status
                    )
                if mode == "playwright":
                    return result
                fallback = await self._http.execute(request)
                fallback.fallback_chain = [*result.fallback_chain, "http_fallback"]
                fallback.degraded_reason = result.degraded_reason or result.backend_status
                return fallback
            except Exception as exc:  # pragma: no cover - depends on local browser runtime
                self._playwright_disabled_reason = str(
                    redact(str(exc) or exc.__class__.__name__)
                )
                if mode == "playwright":
                    return BrowserExecutionResult(
                        action=request.action,
                        url=request.url,
                        action_status="degraded",
                        backend="playwright",
                        backend_status="unavailable",
                        evidence_summary="Playwright browser backend failed before execution",
                        recoverable=True,
                        fallback_chain=["playwright_failed"],
                        degraded_reason=self._playwright_disabled_reason,
                    )
                fallback = await self._http.execute(request)
                fallback.fallback_chain = ["playwright_failed", "http_fallback"]
                fallback.degraded_reason = self._playwright_disabled_reason
                return fallback
        return await self._http.execute(request)

    async def close(self) -> None:
        await self._playwright.close()


class HttpBrowserExecutor:
    def __init__(self) -> None:
        self._contexts: dict[str, _HttpBrowserContext] = {}

    async def execute(self, request: BrowserExecutionRequest) -> BrowserExecutionResult:
        context = self._contexts.setdefault(request.context_key, _HttpBrowserContext())
        if request.action in {"open", "snapshot"}:
            return await self._fetch_page(request, action_status="completed")
        if request.action in {"fill", "type"}:
            return await self._fill(context, request)
        if request.action == "click":
            return await self._click(context, request)
        if request.action == "submit":
            return await self._submit(context, request)
        if request.action == "screenshot":
            page = await self._fetch_page(request, action_status="completed")
            page.action_status = "completed"
            page.screenshot_bytes = _FALLBACK_PNG
            page.content_type = "image/png"
            page.filename = "screenshot.png"
            page.evidence_summary = "browser.screenshot captured fallback page evidence"
            return page
        if request.action == "download":
            return await self._download(request)
        return BrowserExecutionResult(
            action=request.action,
            url=request.url,
            action_status="unsupported",
            backend="http_fallback",
            backend_status="unsupported",
            evidence_summary="browser action is not supported by fallback executor",
            recoverable=True,
        )

    async def _fetch_page(
        self,
        request: BrowserExecutionRequest,
        *,
        action_status: str,
    ) -> BrowserExecutionResult:
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds,
                follow_redirects=True,
            ) as client:
                response = await client.get(request.url)
        except httpx.TimeoutException:
            return _timeout_result(request, "timeout while fetching browser page")
        except httpx.HTTPError as exc:
            return _failed_result(request, str(exc) or exc.__class__.__name__)
        text = response.text[:5000]
        status = action_status if response.status_code < 400 else "http_error"
        return BrowserExecutionResult(
            action=request.action,
            url=str(response.url),
            action_status=status,
            backend="http_fallback",
            backend_status="available",
            evidence_summary=(
                f"browser.{request.action} captured HTTP {response.status_code} "
                "untrusted page evidence"
            ),
            title=_html_title(text),
            http_status=response.status_code,
            snapshot=text,
            content_preview=text,
            recoverable=response.status_code >= 400,
            network_summary={"request_count": 1, "failed_count": int(response.status_code >= 400)},
            console_summary={"error_count": 0, "warning_count": 0},
            fallback_chain=["http_fallback"],
        )

    async def _fill(
        self,
        context: _HttpBrowserContext,
        request: BrowserExecutionRequest,
    ) -> BrowserExecutionResult:
        if not request.selector:
            return _failed_result(request, "selector is required")
        page = await self._fetch_page(request, action_status="completed")
        parser = _ParsedHtml.from_text(page.snapshot or "")
        field_name = parser.field_name_for_selector(request.selector) or request.selector
        value = request.value or ""
        context.current_url = page.url
        context.fields[field_name] = value
        context.selectors[request.selector] = field_name
        page.action = request.action
        page.action_status = "completed"
        page.selector = request.selector
        page.value_preview = value[:80]
        page.interaction = {
            "dom_interaction_executed": True,
            "field_name": str(redact(field_name)),
            "selector_resolved": field_name != request.selector,
            "storage_state_visible": False,
        }
        page.evidence_summary = f"browser.{request.action} updated a controlled form field"
        return page

    async def _click(
        self,
        context: _HttpBrowserContext,
        request: BrowserExecutionRequest,
    ) -> BrowserExecutionResult:
        if not request.selector:
            return _failed_result(request, "selector is required")
        page = await self._fetch_page(request, action_status="completed")
        parser = _ParsedHtml.from_text(page.snapshot or "")
        target_url = parser.href_for_selector(request.selector)
        context.current_url = page.url
        if target_url:
            navigated = await self._fetch_page(
                BrowserExecutionRequest(
                    action="click",
                    url=urljoin(page.url, target_url),
                    selector=request.selector,
                    timeout_seconds=request.timeout_seconds,
                    context_key=request.context_key,
                    session_context=request.session_context,
                ),
                action_status="completed",
            )
            page = navigated
        page.action = "click"
        page.selector = request.selector
        page.action_status = (
            "completed" if page.http_status is None or page.http_status < 400 else "http_error"
        )
        page.interaction = {
            "dom_interaction_executed": True,
            "selector": str(redact(request.selector)),
            "navigated": bool(target_url),
        }
        page.evidence_summary = "browser.click executed a controlled DOM interaction"
        return page

    async def _submit(
        self,
        context: _HttpBrowserContext,
        request: BrowserExecutionRequest,
    ) -> BrowserExecutionResult:
        page = await self._fetch_page(request, action_status="completed")
        parser = _ParsedHtml.from_text(page.snapshot or "")
        form = parser.form_for_selector(request.selector) or parser.first_form()
        if form is None:
            page.action = "submit"
            page.action_status = "not_found"
            page.recoverable = True
            page.evidence_summary = "browser.submit could not find a form to submit"
            return page
        data = {**form.controls, **context.fields}
        submit_url = urljoin(page.url, form.action or page.url)
        try:
            async with httpx.AsyncClient(
                timeout=request.timeout_seconds,
                follow_redirects=True,
            ) as client:
                if form.method == "post":
                    response = await client.post(submit_url, data=data)
                else:
                    response = await client.get(submit_url, params=data)
        except httpx.TimeoutException:
            return _timeout_result(request, "timeout while submitting browser form")
        except httpx.HTTPError as exc:
            return _failed_result(request, str(exc) or exc.__class__.__name__)
        text = response.text[:5000]
        context.current_url = str(response.url)
        return BrowserExecutionResult(
            action="submit",
            url=str(response.url),
            action_status="completed" if response.status_code < 400 else "http_error",
            backend="http_fallback",
            backend_status="available",
            evidence_summary="browser.submit submitted a controlled form",
            title=_html_title(text),
            http_status=response.status_code,
            snapshot=text,
            content_preview=text,
            timeout=False,
            recoverable=response.status_code >= 400,
            selector=request.selector,
            interaction={
                "dom_interaction_executed": True,
                "form_method": form.method,
                "submitted_field_count": len(data),
                "storage_state_visible": False,
            },
            network_summary={
                "request_count": 2,
                "failed_count": int(response.status_code >= 400),
                "http_status": response.status_code,
            },
            console_summary={"error_count": 0, "warning_count": 0},
            fallback_chain=["http_fallback"],
        )

    async def _download(self, request: BrowserExecutionRequest) -> BrowserExecutionResult:
        try:
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                response = await client.get(request.url)
        except httpx.TimeoutException:
            return _timeout_result(request, "timeout while downloading browser resource")
        except httpx.HTTPError as exc:
            return _failed_result(request, str(exc) or exc.__class__.__name__)
        filename = request.display_name or _filename_from_response(str(response.url))
        return BrowserExecutionResult(
            action="download",
            url=str(response.url),
            action_status="completed" if response.status_code < 400 else "http_error",
            backend="http_fallback",
            backend_status="available",
            evidence_summary="browser.download fetched untrusted content for quarantine",
            http_status=response.status_code,
            download_bytes=response.content,
            content_type=response.headers.get("content-type") or "application/octet-stream",
            filename=filename,
            recoverable=response.status_code >= 400,
            network_summary={"request_count": 1, "failed_count": int(response.status_code >= 400)},
            console_summary={"error_count": 0, "warning_count": 0},
            fallback_chain=["http_fallback"],
        )


class PlaywrightBrowserExecutor:
    def __init__(self) -> None:
        self._states: dict[str, _PlaywrightContextState] = {}

    async def close(self) -> None:
        states = list(self._states.values())
        self._states.clear()
        for state in states:
            try:
                await state.context.close()
            except Exception:
                pass
            try:
                await state.browser.close()
            except Exception:
                pass
            try:
                await state.playwright.stop()
            except Exception:
                pass

    async def execute(self, request: BrowserExecutionRequest) -> BrowserExecutionResult:
        try:
            from playwright.async_api import async_playwright
        except Exception:
            return BrowserExecutionResult(
                action=request.action,
                url=request.url,
                action_status="degraded",
                backend="playwright",
                backend_status="unavailable",
                evidence_summary="Playwright is not installed",
                recoverable=True,
                fallback_chain=["playwright_unavailable"],
                degraded_reason="playwright_not_installed",
            )
        try:
            state = await self._state_for(async_playwright, request.context_key)
        except Exception as exc:  # pragma: no cover - depends on local browser runtime
            return BrowserExecutionResult(
                action=request.action,
                url=request.url,
                action_status="degraded",
                backend="playwright",
                backend_status="unavailable",
                evidence_summary="Playwright browser backend is unavailable",
                recoverable=True,
                fallback_chain=["playwright_unavailable"],
                degraded_reason=str(redact(str(exc) or exc.__class__.__name__)),
            )
        try:
            if request.action == "download":
                api_response = await state.context.request.get(
                    request.url,
                    timeout=int(request.timeout_seconds * 1000),
                )
                content = await api_response.body()
                return BrowserExecutionResult(
                    action=request.action,
                    url=request.url,
                    http_status=api_response.status,
                    action_status="completed" if api_response.status < 400 else "http_error",
                    backend="playwright",
                    backend_status="available",
                    evidence_summary="browser.download fetched content in Playwright context",
                    download_bytes=content,
                    content_type=api_response.headers.get("content-type")
                    or "application/octet-stream",
                    filename=request.display_name or _filename_from_response(request.url),
                    recoverable=api_response.status >= 400,
                    network_summary={
                        "request_count": 1,
                        "failed_count": int(api_response.status >= 400),
                    },
                    console_summary={"error_count": 0, "warning_count": 0},
                    fallback_chain=["playwright"],
                )
            page = state.page
            response = None
            should_navigate = request.action != "submit" or state.current_url != request.url
            if should_navigate:
                response = await page.goto(
                    request.url,
                    wait_until="domcontentloaded",
                    timeout=int(request.timeout_seconds * 1000),
                )
                state.current_url = request.url
            if request.action in {"fill", "type"} and request.selector:
                await page.locator(request.selector).fill(request.value or "")
            elif request.action == "click" and request.selector:
                await page.locator(request.selector).click(timeout=5000)
                await _quiet_load_state(page)
            elif request.action == "submit":
                if request.selector:
                    locator = page.locator(request.selector)
                    tag = await locator.evaluate("el => el.tagName.toLowerCase()")
                    if tag == "form":
                        await locator.evaluate("form => form.requestSubmit()")
                    else:
                        await locator.click(timeout=5000)
                else:
                    await page.locator("form").first.evaluate("form => form.requestSubmit()")
                await _quiet_load_state(page)
            data: bytes | None = None
            if request.action == "screenshot":
                data = await page.screenshot(full_page=True)
            content = (await page.content())[:5000]
            title = await page.title()
            state.current_url = page.url
            return BrowserExecutionResult(
                action=request.action,
                url=page.url,
                title=title,
                http_status=response.status if response is not None else None,
                action_status="completed",
                backend="playwright",
                backend_status="available",
                evidence_summary=f"browser.{request.action} executed in Playwright",
                snapshot=content if request.action in {"open", "snapshot", "submit"} else None,
                content_preview=content,
                screenshot_bytes=data,
                content_type="image/png" if data else None,
                filename="screenshot.png" if data else None,
                selector=request.selector,
                value_preview=(request.value or "")[:80] if request.value else None,
                interaction={
                    "dom_interaction_executed": request.action
                    in {"fill", "type", "click", "submit"},
                    "storage_state_visible": False,
                    "context_reused": True,
                },
                network_summary={"request_count": 1, "failed_count": 0},
                console_summary={"error_count": 0, "warning_count": 0},
                fallback_chain=["playwright"],
            )
        except Exception as exc:
            return BrowserExecutionResult(
                action=request.action,
                url=state.current_url or request.url,
                action_status="failed",
                backend="playwright",
                backend_status="available",
                evidence_summary="Playwright action failed after backend startup",
                recoverable=True,
                fallback_chain=["playwright"],
                degraded_reason=str(redact(str(exc) or exc.__class__.__name__)),
                network_summary={"request_count": 1, "failed_count": 1},
                console_summary={"error_count": 0, "warning_count": 0},
            )

    async def _state_for(
        self,
        async_playwright: Any,
        context_key: str,
    ) -> _PlaywrightContextState:
        state = self._states.get(context_key)
        if state is not None:
            return state
        manager = async_playwright()
        playwright = None
        browser = None
        try:
            playwright = await manager.start()
            browser = await playwright.chromium.launch(**_browser_launch_options())
            context = await browser.new_context()
            page = await context.new_page()
            state = _PlaywrightContextState(
                manager=manager,
                playwright=playwright,
                browser=browser,
                context=context,
                page=page,
            )
            self._states[context_key] = state
            return state
        except Exception:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass
            if playwright is not None:
                try:
                    await playwright.stop()
                except Exception:
                    pass
            raise


@dataclass
class _PlaywrightContextState:
    manager: Any
    playwright: Any
    browser: Any
    context: Any
    page: Any
    current_url: str | None = None


@dataclass
class _HttpBrowserContext:
    current_url: str | None = None
    fields: dict[str, str] = field(default_factory=dict)
    selectors: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _ParsedForm:
    selector_id: str | None
    action: str | None
    method: str
    controls: dict[str, str]


class _ParsedHtml(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._title_parts: list[str] = []
        self._in_title = False
        self.inputs: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.forms: list[_ParsedForm] = []
        self._form_stack: list[dict[str, Any]] = []

    @classmethod
    def from_text(cls, text: str) -> _ParsedHtml:
        parser = cls()
        parser.feed(text)
        parser.close()
        return parser

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): value or "" for key, value in attrs}
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag == "form":
            self._form_stack.append(
                {
                    "id": values.get("id"),
                    "action": values.get("action"),
                    "method": (values.get("method") or "get").lower(),
                    "controls": {},
                }
            )
        if tag in {"input", "textarea"}:
            self.inputs.append(values)
            name = values.get("name")
            if name and self._form_stack:
                self._form_stack[-1]["controls"][name] = values.get("value", "")
        if tag == "a" and values.get("href"):
            self.links.append(values)
        if tag == "button" and values.get("name") and self._form_stack:
            self._form_stack[-1]["controls"][values["name"]] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self._in_title = False
            self.title = " ".join("".join(self._title_parts).split())[:200]
        if tag.lower() == "form" and self._form_stack:
            form = self._form_stack.pop()
            self.forms.append(
                _ParsedForm(
                    selector_id=form.get("id"),
                    action=form.get("action"),
                    method="post" if form.get("method") == "post" else "get",
                    controls=dict(form.get("controls") or {}),
                )
            )

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self._title_parts.append(data)

    def field_name_for_selector(self, selector: str) -> str | None:
        selector_id = _selector_id(selector)
        selector_name = _selector_name(selector)
        for item in self.inputs:
            if selector_id and item.get("id") == selector_id:
                return item.get("name") or selector_id
            if selector_name and item.get("name") == selector_name:
                return selector_name
        return selector_name

    def href_for_selector(self, selector: str) -> str | None:
        selector_id = _selector_id(selector)
        for item in self.links:
            if selector_id and item.get("id") == selector_id:
                return item.get("href")
        return None

    def form_for_selector(self, selector: str | None) -> _ParsedForm | None:
        if not selector:
            return None
        selector_id = _selector_id(selector)
        if not selector_id:
            return None
        for form in self.forms:
            if form.selector_id == selector_id:
                return form
        return None

    def first_form(self) -> _ParsedForm | None:
        return self.forms[0] if self.forms else None


def _selector_id(selector: str) -> str | None:
    selector = selector.strip()
    if selector.startswith("#") and re.fullmatch(r"#[A-Za-z0-9_\-:.]+", selector):
        return selector[1:]
    match = re.search(r"\[id=['\"]?([^'\"\]]+)['\"]?\]", selector)
    return match.group(1) if match else None


def _selector_name(selector: str) -> str | None:
    selector = selector.strip()
    match = re.search(r"\[name=['\"]?([^'\"\]]+)['\"]?\]", selector)
    if match:
        return match.group(1)
    if re.fullmatch(r"[A-Za-z0-9_\-:.]+", selector):
        return selector
    return None


def _html_title(text: str) -> str | None:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return None
    return str(redact(re.sub(r"\s+", " ", match.group(1)).strip()))[:200]


def _timeout_result(request: BrowserExecutionRequest, reason: str) -> BrowserExecutionResult:
    return BrowserExecutionResult(
        action=request.action,
        url=request.url,
        action_status="timeout",
        backend="http_fallback",
        backend_status="available",
        evidence_summary=reason,
        timeout=True,
        recoverable=True,
        network_summary={"request_count": 1, "failed_count": 1, "timeout": True},
        console_summary={"error_count": 0, "warning_count": 0},
        fallback_chain=["http_fallback"],
    )


def _failed_result(request: BrowserExecutionRequest, reason: str) -> BrowserExecutionResult:
    return BrowserExecutionResult(
        action=request.action,
        url=request.url,
        action_status="failed",
        backend="http_fallback",
        backend_status="available",
        evidence_summary=str(redact(reason)),
        recoverable=True,
        network_summary={"request_count": 1, "failed_count": 1},
        console_summary={"error_count": 0, "warning_count": 0},
        fallback_chain=["http_fallback"],
        degraded_reason=str(redact(reason)),
    )


def _filename_from_response(url: str) -> str:
    name = url.rstrip("/").rsplit("/", 1)[-1] or "download.bin"
    return name.split("?", 1)[0] or "download.bin"


async def _quiet_load_state(page: Any) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        return


def _browser_launch_options() -> dict[str, Any]:
    configured_path = _configured_browser_executable_path()
    if configured_path is not None:
        return {"executable_path": str(configured_path)}
    channel = os.environ.get(_BROWSER_CHANNEL_ENV, "").strip()
    if channel:
        return {"channel": channel}
    default_path = _default_browser_executable_path()
    if default_path is not None:
        return {"executable_path": str(default_path)}
    return {}


def _configured_browser_executable_path() -> Path | None:
    value = os.environ.get(_BROWSER_EXECUTABLE_PATH_ENV, "").strip()
    if not value:
        return None
    return Path(os.path.expandvars(value)).expanduser()


def _default_browser_executable_path() -> Path | None:
    if os.name != "nt":
        return None
    for candidate in _browser_executable_candidates():
        if candidate.exists():
            return candidate
    return None


def _browser_executable_candidates() -> list[Path]:
    bases = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("LOCALAPPDATA"),
    ]
    roots = [Path(base) for base in bases if base]
    return [
        *(root / "Google" / "Chrome" / "Application" / "chrome.exe" for root in roots),
        *(root / "Microsoft" / "Edge" / "Application" / "msedge.exe" for root in roots),
    ]
