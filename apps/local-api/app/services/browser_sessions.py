from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qsl, urlsplit, urlunsplit

from core_types import (
    BrowserEvidence,
    BrowserPageState,
    BrowserProfile,
    BrowserProfileEvent,
    BrowserSession,
    ErrorCode,
    RiskLevel,
)
from trace_service import TraceService, redact

from app.core.errors import AppError
from app.core.time import new_id, utc_now_iso
from app.db.repositories.asset_repo import AssetRepository
from app.db.repositories.browser_repo import BrowserRepository
from app.schemas.browser import (
    BrowserProfileCreateRequest,
    BrowserProfileUpdateRequest,
    BrowserSessionHealthCheckRequest,
    BrowserSessionHealthCheckResponse,
    BrowserSessionHealthProbeResponse,
    BrowserSessionRestoreContextRequest,
    BrowserSessionRestoreContextResponse,
    BrowserSessionCreateRequest,
)
from app.services.audit import AuditEventService

SENSITIVE_QUERY_KEYS = {
    "api_key",
    "apikey",
    "token",
    "access_token",
    "secret",
    "cookie",
    "password",
    "passwd",
    "pwd",
    "private_key",
    "mnemonic",
}
METADATA_IPS = {"169.254.169.254", "100.100.100.200"}


@dataclass(frozen=True)
class BrowserSafetyDecision:
    allowed: bool
    url: str
    redacted_url: str
    scheme: str
    hostname: str | None
    risk_level: str
    reason_codes: list[str]
    blocked_reason: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "url": self.redacted_url,
            "scheme": self.scheme,
            "hostname": self.hostname,
            "risk_level": self.risk_level,
            "reason_codes": self.reason_codes,
            "blocked_reason": self.blocked_reason,
        }


class BrowserSafetyPolicy:
    def classify(
        self,
        url: str,
        *,
        allowed_domains: list[str] | None = None,
        blocked_domains: list[str] | None = None,
        allow_private: bool = False,
    ) -> BrowserSafetyDecision:
        parsed = urlsplit(url.strip())
        scheme = parsed.scheme.lower()
        host = parsed.hostname.lower() if parsed.hostname else None
        redacted_url, sensitive_query = _redact_url_query(url)
        reasons: list[str] = []
        risk = "R2"
        if scheme not in {"http", "https"}:
            return BrowserSafetyDecision(
                allowed=False,
                url=url,
                redacted_url=redacted_url,
                scheme=scheme,
                hostname=host,
                risk_level="R5",
                reason_codes=[f"browser_scheme_{scheme or 'missing'}_denied"],
                blocked_reason="unsupported_scheme",
            )
        if sensitive_query:
            reasons.append("sensitive_query_redacted")
            risk = "R3"
        if host is None:
            return BrowserSafetyDecision(
                allowed=False,
                url=url,
                redacted_url=redacted_url,
                scheme=scheme,
                hostname=host,
                risk_level="R5",
                reason_codes=["browser_url_missing_host"],
                blocked_reason="missing_host",
            )
        if host in {item.lower() for item in blocked_domains or []}:
            return BrowserSafetyDecision(
                allowed=False,
                url=url,
                redacted_url=redacted_url,
                scheme=scheme,
                hostname=host,
                risk_level="R4",
                reason_codes=["browser_domain_blocked_by_profile"],
                blocked_reason="profile_blocked_domain",
            )
        if allowed_domains and not _domain_allowed(host, allowed_domains):
            return BrowserSafetyDecision(
                allowed=False,
                url=url,
                redacted_url=redacted_url,
                scheme=scheme,
                hostname=host,
                risk_level="R4",
                reason_codes=["browser_domain_not_allowed_by_profile"],
                blocked_reason="profile_allowed_domain_mismatch",
            )
        ip = _parse_ip(host)
        if host in METADATA_IPS or (ip is not None and str(ip) in METADATA_IPS):
            return BrowserSafetyDecision(
                allowed=False,
                url=url,
                redacted_url=redacted_url,
                scheme=scheme,
                hostname=host,
                risk_level="R5",
                reason_codes=["browser_metadata_url_denied"],
                blocked_reason="metadata_url",
            )
        if ip is not None and ip.is_private and not ip.is_loopback and not allow_private:
            return BrowserSafetyDecision(
                allowed=False,
                url=url,
                redacted_url=redacted_url,
                scheme=scheme,
                hostname=host,
                risk_level="R4",
                reason_codes=["browser_private_network_denied"],
                blocked_reason="private_network",
            )
        if ip is not None and ip.is_loopback:
            reasons.append("browser_loopback_test_origin")
        return BrowserSafetyDecision(
            allowed=True,
            url=url,
            redacted_url=redacted_url,
            scheme=scheme,
            hostname=host,
            risk_level=risk,
            reason_codes=reasons or ["browser_url_allowed"],
        )


class BrowserSessionService:
    def __init__(
        self,
        *,
        repo: BrowserRepository,
        asset_repo: AssetRepository,
        trace_service: TraceService,
        audit_service: AuditEventService,
    ) -> None:
        self._repo = repo
        self._assets = asset_repo
        self._trace = trace_service
        self._audit = audit_service
        self._safety = BrowserSafetyPolicy()

    async def create_profile(
        self,
        request: BrowserProfileCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserProfile:
        profile_id = new_id("bprof")
        now = utc_now_iso()
        policy = _default_profile_policy(request.policy)
        data = {
            "browser_profile_id": profile_id,
            "organization_id": "org_default",
            "display_name": request.display_name,
            "profile_type": request.profile_type,
            "storage_backend": request.storage_backend,
            "status": "active",
            "sensitivity": request.sensitivity,
            "allowed_domains": request.allowed_domains,
            "blocked_domains": request.blocked_domains,
            "policy": policy,
            "metadata": request.metadata,
            "created_by_member_id": request.created_by_member_id,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
            "expires_at": request.expires_at,
            "health_status": "unknown",
            "reuse_policy": request.metadata.get("reuse_policy", {}),
        }
        await self._repo.insert_profile(data)
        await self._event(
            profile_id,
            "browser_profile.created",
            {"policy": policy},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="browser_profile.created",
            object_type="browser_profile",
            object_id=profile_id,
            summary="浏览器 profile 已创建",
            risk_level=RiskLevel.R2,
            payload={"browser_profile_id": profile_id, "profile_type": request.profile_type},
            trace_id=trace_id,
        )
        return await self.get_profile(profile_id)

    async def list_profiles(self, *, status: str | None = None) -> list[BrowserProfile]:
        return [BrowserProfile(**row) for row in await self._repo.list_profiles(status=status)]

    async def get_profile(self, browser_profile_id: str) -> BrowserProfile:
        row = await self._repo.get_profile(browser_profile_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "浏览器 profile 不存在", status_code=404)
        return BrowserProfile(**row)

    async def update_profile(
        self,
        browser_profile_id: str,
        request: BrowserProfileUpdateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserProfile:
        await self.get_profile(browser_profile_id)
        fields = {
            key: value
            for key, value in request.model_dump(exclude_unset=True).items()
            if value is not None
        }
        if "policy" in fields:
            fields["policy"] = _default_profile_policy(fields["policy"])
        fields["updated_at"] = utc_now_iso()
        await self._repo.update_profile(browser_profile_id, fields)
        await self._event(
            browser_profile_id,
            "browser_profile.updated",
            {"fields": sorted(key for key in fields if key != "updated_at")},
            trace_id=trace_id,
        )
        return await self.get_profile(browser_profile_id)

    async def activate_profile(
        self,
        browser_profile_id: str,
        *,
        trace_id: str | None = None,
    ) -> BrowserProfile:
        await self._repo.update_profile(
            browser_profile_id,
            {"status": "active", "updated_at": utc_now_iso()},
        )
        await self._event(browser_profile_id, "browser_profile.activated", {}, trace_id=trace_id)
        return await self.get_profile(browser_profile_id)

    async def pause_profile(
        self,
        browser_profile_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> BrowserProfile:
        await self._repo.update_profile(
            browser_profile_id,
            {"status": "paused", "updated_at": utc_now_iso()},
        )
        await self._event(
            browser_profile_id,
            "browser_profile.paused",
            {"reason": reason},
            trace_id=trace_id,
        )
        return await self.get_profile(browser_profile_id)

    async def revoke_profile(
        self,
        browser_profile_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> BrowserProfile:
        profile = await self.get_profile(browser_profile_id)
        now = utc_now_iso()
        await self._repo.update_profile(
            browser_profile_id,
            {"status": "revoked", "revoked_at": now, "updated_at": now},
        )
        revoked_assets: list[str] = []
        for session in await self._repo.list_sessions(browser_profile_id=browser_profile_id):
            await self._repo.update_session(
                session["browser_session_id"],
                {"status": "revoked", "revoked_at": now, "updated_at": now},
            )
            if session.get("asset_id"):
                revoked_assets.append(str(session["asset_id"]))
                await self._assets.revoke_handles_for_asset(
                    str(session["asset_id"]),
                    revoked_at=now,
                )
        await self._event(
            browser_profile_id,
            "browser_profile.revoked",
            {"reason": reason, "revoked_asset_count": len(set(revoked_assets))},
            trace_id=trace_id,
        )
        await self._audit.write_event(
            actor_type="system",
            action="browser_profile.revoked",
            object_type="browser_profile",
            object_id=browser_profile_id,
            summary="浏览器 profile 已撤销",
            risk_level=RiskLevel.R2,
            payload={"browser_profile_id": profile.browser_profile_id, "reason": reason},
            trace_id=trace_id,
        )
        return await self.get_profile(browser_profile_id)

    async def clear_profile(
        self,
        browser_profile_id: str,
        *,
        reason: str | None = None,
        trace_id: str | None = None,
    ) -> BrowserProfile:
        await self.get_profile(browser_profile_id)
        now = utc_now_iso()
        for session in await self._repo.list_sessions(browser_profile_id=browser_profile_id):
            await self._repo.update_session(
                session["browser_session_id"],
                {"status": "cleared", "updated_at": now},
            )
        await self._repo.update_profile(
            browser_profile_id,
            {"cleared_at": now, "updated_at": now},
        )
        await self._event(
            browser_profile_id,
            "browser_profile.cleared",
            {"reason": reason},
            trace_id=trace_id,
        )
        return await self.get_profile(browser_profile_id)

    async def create_session(
        self,
        browser_profile_id: str,
        request: BrowserSessionCreateRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserSession:
        profile = await self.get_profile(browser_profile_id)
        if profile.status != "active":
            raise AppError(ErrorCode.ASSET_DISABLED, "浏览器 profile 不可用", status_code=403)
        if request.asset_id:
            asset = await self._assets.get_asset(request.asset_id)
            if asset is None:
                raise AppError(
                    ErrorCode.ASSET_NOT_FOUND,
                    "浏览器 session 资产不存在",
                    status_code=404,
                )
        session_id = new_id("bsess")
        now = utc_now_iso()
        data = {
            "browser_session_id": session_id,
            "organization_id": "org_default",
            "browser_profile_id": browser_profile_id,
            "asset_id": request.asset_id,
            "login_domain": request.login_domain,
            "auth_type": request.auth_type,
            "status": "active",
            "sensitivity": request.sensitivity,
            "session_metadata": request.session_metadata,
            "secret_ref": request.secret_ref,
            "created_by_member_id": request.created_by_member_id,
            "trace_id": trace_id,
            "created_at": now,
            "updated_at": now,
            "expires_at": request.expires_at,
            "health_status": "unknown",
            "login_state": "unknown",
            "reuse_policy": request.reuse_policy,
        }
        await self._repo.insert_session(data)
        await self._event(
            browser_profile_id,
            "browser_session.created",
            {"browser_session_id": session_id, "login_domain": request.login_domain},
            browser_session_id=session_id,
            trace_id=trace_id,
        )
        if request.asset_id:
            asset = await self._assets.get_asset(request.asset_id)
            if asset is not None:
                config = dict(asset.get("config", {}))
                config.update(
                    {
                        "provider": "browser_session",
                        "profile_id": browser_profile_id,
                        "browser_profile_id": browser_profile_id,
                        "browser_session_id": session_id,
                        "login_domain": request.login_domain,
                        "auth_type": request.auth_type,
                    }
                )
                await self._assets.update_asset(
                    request.asset_id,
                    {
                        "provider": "browser_session",
                        "config": config,
                        "sensitivity": request.sensitivity,
                        "updated_at": now,
                    },
                )
        return await self.get_session(session_id)

    async def list_sessions(self, browser_profile_id: str) -> list[BrowserSession]:
        await self.get_profile(browser_profile_id)
        return [
            BrowserSession(**row)
            for row in await self._repo.list_sessions(browser_profile_id=browser_profile_id)
        ]

    async def get_session(self, browser_session_id: str) -> BrowserSession:
        row = await self._repo.get_session(browser_session_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "浏览器 session 不存在", status_code=404)
        return BrowserSession(**row)

    async def health_check_session(
        self,
        browser_session_id: str,
        request: BrowserSessionHealthCheckRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserSessionHealthCheckResponse:
        session = await self.get_session(browser_session_id)
        profile = await self.get_profile(session.browser_profile_id)
        decision = self._infer_health_decision(
            session=session,
            profile=profile,
            probe_type=request.probe_type,
            provider_status=request.provider_status,
            observed_status=request.observed_status,
            failure_reason=request.failure_reason,
            recovery_hint=request.recovery_hint,
            evidence=request.evidence,
        )
        now = utc_now_iso()
        probe_id = new_id("bsp")
        await self._repo.insert_health_probe(
            {
                "probe_id": probe_id,
                "organization_id": session.organization_id,
                "browser_profile_id": session.browser_profile_id,
                "browser_session_id": session.browser_session_id,
                "probe_type": request.probe_type,
                "health_status": decision["health_status"],
                "login_state": decision["login_state"],
                "provider_status": request.provider_status,
                "failure_reason": decision.get("failure_reason"),
                "recovery_hint": decision.get("recovery_hint"),
                "evidence_redacted": redact(request.evidence),
                "redaction_summary": {
                    "policy": "trace_service.redact",
                    "cookie_redacted": True,
                    "token_redacted": True,
                },
                "trace_id": trace_id,
                "probed_at": now,
            }
        )
        await self._repo.update_session(
            browser_session_id,
            {
                "health_status": decision["health_status"],
                "login_state": decision["login_state"],
                "last_probe_at": now,
                "invalidation_reason": decision.get("failure_reason"),
                "recovery_hint": decision.get("recovery_hint"),
                "status": decision["session_status"],
                "updated_at": now,
            },
        )
        await self._repo.update_profile(
            session.browser_profile_id,
            {
                "health_status": decision["health_status"],
                "last_probe_at": now,
                "recovery_hint": decision.get("recovery_hint"),
                "updated_at": now,
            },
        )
        await self._event(
            session.browser_profile_id,
            "browser_session.health_checked",
            {
                "browser_session_id": session.browser_session_id,
                "health_status": decision["health_status"],
                "login_state": decision["login_state"],
                "provider_status": request.provider_status,
            },
            browser_session_id=session.browser_session_id,
            trace_id=trace_id,
        )
        updated_session = await self.get_session(browser_session_id)
        updated_profile = await self.get_profile(session.browser_profile_id)
        probe = await self.get_health_probe(browser_session_id, probe_id)
        return BrowserSessionHealthCheckResponse(
            browser_session=updated_session,
            browser_profile=updated_profile,
            probe=probe,
        )

    async def restore_context(
        self,
        browser_session_id: str,
        request: BrowserSessionRestoreContextRequest,
        *,
        trace_id: str | None = None,
    ) -> BrowserSessionRestoreContextResponse:
        session = await self.get_session(browser_session_id)
        profile = await self.get_profile(session.browser_profile_id)
        decision = self._infer_health_decision(
            session=session,
            profile=profile,
            probe_type="restore_context",
            provider_status=None,
            observed_status=None,
            failure_reason=None,
            recovery_hint=None,
            evidence={},
        )
        context = {
            "browser_profile_id": session.browser_profile_id,
            "browser_session_id": session.browser_session_id,
            "login_domain": session.login_domain,
            "health_status": decision["health_status"],
            "login_state": decision["login_state"],
            "restore_context_ref": session.restore_context_ref,
            "recoverable": decision["health_status"] != "healthy",
            "page_key": request.page_key,
            "current_url": str(redact(request.current_url)) if request.current_url else None,
            "requested_action": request.requested_action,
            "redaction_summary": {
                "policy": "trace_service.redact",
                "cookie_redacted": True,
                "token_redacted": True,
                "profile_path_visible": False,
            },
        }
        await self._repo.update_session(
            browser_session_id,
            {
                "restore_context_ref": new_id("bctx"),
                "updated_at": utc_now_iso(),
            },
        )
        session = await self.get_session(browser_session_id)
        return BrowserSessionRestoreContextResponse(
            browser_session=session,
            browser_profile=profile,
            context=context,
        )

    async def validate_session_context(
        self,
        *,
        browser_profile_id: str | None,
        browser_session_id: str | None,
        member_id: str | None = None,
        task_id: str | None = None,
        url: str | None = None,
    ) -> dict[str, Any]:
        if not browser_profile_id and not browser_session_id:
            return {}
        session = await self.get_session(browser_session_id) if browser_session_id else None
        profile_id = browser_profile_id or (session.browser_profile_id if session else None)
        if profile_id is None:
            return {}
        profile = await self.get_profile(profile_id)
        self._ensure_session_usable(
            session=session,
            profile=profile,
            member_id=member_id,
            task_id=task_id,
            url=url,
        )
        return {
            "browser_profile_id": profile.browser_profile_id,
            "browser_session_id": session.browser_session_id if session else None,
            "profile_policy": profile.policy,
            "allowed_domains": profile.allowed_domains,
            "blocked_domains": profile.blocked_domains,
            "sensitivity": session.sensitivity if session else profile.sensitivity,
            "reuse_policy": session.reuse_policy if session else profile.reuse_policy,
            "login_domain": session.login_domain if session else None,
        }

    def classify_url(
        self,
        url: str,
        *,
        session_context: dict[str, Any] | None = None,
        allow_private: bool = False,
    ) -> BrowserSafetyDecision:
        context = session_context or {}
        raw_policy = context.get("profile_policy")
        policy = raw_policy if isinstance(raw_policy, dict) else {}
        return self._safety.classify(
            url,
            allowed_domains=list(context.get("allowed_domains") or []),
            blocked_domains=list(context.get("blocked_domains") or []),
            allow_private=allow_private or bool(policy.get("allow_private_network")),
        )

    async def record_evidence(
        self,
        *,
        task_id: str | None,
        tool_call_id: str | None,
        organization_id: str,
        action: str,
        action_status: str,
        url: str | None,
        title: str | None = None,
        http_status: int | None = None,
        evidence_summary: str,
        snapshot_preview: str | None = None,
        screenshot_artifact_id: str | None = None,
        download_artifact_id: str | None = None,
        artifact_ids: list[str] | None = None,
        network_summary: dict[str, Any] | None = None,
        console_summary: dict[str, Any] | None = None,
        redaction_summary: dict[str, Any] | None = None,
        safety_decision: dict[str, Any] | None = None,
        session_context: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> BrowserEvidence:
        context = session_context or {}
        evidence_id = new_id("bevd")
        created_at = utc_now_iso()
        data = {
            "browser_evidence_id": evidence_id,
            "organization_id": organization_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "browser_profile_id": context.get("browser_profile_id"),
            "browser_session_id": context.get("browser_session_id"),
            "action": action,
            "action_status": action_status,
            "url": str(redact(url)) if url else None,
            "title": str(redact(title)) if title else None,
            "http_status": http_status,
            "evidence_summary": str(redact(evidence_summary)),
            "snapshot_preview": str(redact(snapshot_preview))[:1000] if snapshot_preview else None,
            "screenshot_artifact_id": screenshot_artifact_id,
            "download_artifact_id": download_artifact_id,
            "artifact_ids": artifact_ids or [],
            "network_summary": redact(network_summary or {}),
            "console_summary": redact(console_summary or {}),
            "redaction_summary": {
                "policy": "trace_service.redact",
                "cookie_redacted": True,
                "token_redacted": True,
                **(redaction_summary or {}),
            },
            "safety_decision": redact(safety_decision or {}),
            "untrusted_external_content": True,
            "trace_id": trace_id,
            "created_at": created_at,
        }
        await self._repo.insert_evidence(data)
        if url:
            await self._repo.insert_network_event(
                {
                    "network_event_id": new_id("bnet"),
                    "browser_evidence_id": evidence_id,
                    "organization_id": organization_id,
                    "request_url": str(redact(url)),
                    "method": "GET",
                    "status_code": http_status,
                    "resource_type": "document",
                    "redaction_summary": data["redaction_summary"],
                    "created_at": created_at,
                }
            )
        if context.get("browser_session_id"):
            await self._touch_session_usage(
                str(context["browser_session_id"]),
                created_at=created_at,
                task_id=task_id,
                browser_evidence_id=evidence_id,
                page_key=None,
            )
        return BrowserEvidence(**data)

    async def get_evidence(self, browser_evidence_id: str) -> BrowserEvidence:
        row = await self._repo.get_evidence(browser_evidence_id)
        if row is None:
            raise AppError(ErrorCode.NOT_FOUND, "浏览器证据不存在", status_code=404)
        return BrowserEvidence(**row)

    async def list_task_evidence(self, task_id: str) -> list[BrowserEvidence]:
        return [BrowserEvidence(**row) for row in await self._repo.list_evidence_for_task(task_id)]

    async def get_health_probe(
        self,
        browser_session_id: str,
        probe_id: str,
    ) -> BrowserSessionHealthProbeResponse:
        rows = await self._repo.list_health_probes(browser_session_id)
        for row in rows:
            if row["probe_id"] == probe_id:
                return BrowserSessionHealthProbeResponse(**row)
        raise AppError(ErrorCode.NOT_FOUND, "浏览器健康探测不存在", status_code=404)

    async def list_page_states(
        self,
        *,
        browser_session_id: str | None = None,
        task_id: str | None = None,
        page_key: str | None = None,
    ) -> list[BrowserPageState]:
        return [
            BrowserPageState(**row)
            for row in await self._repo.list_page_states(
                browser_session_id=browser_session_id,
                task_id=task_id,
                page_key=page_key,
            )
        ]

    async def record_page_state(
        self,
        *,
        task_id: str | None,
        tool_call_id: str | None,
        organization_id: str,
        action: str,
        action_status: str,
        page_key: str,
        current_url: str | None,
        title: str | None,
        http_status: int | None,
        dom_summary: dict[str, Any],
        network_summary: dict[str, Any],
        console_summary: dict[str, Any],
        task_checkpoint: dict[str, Any],
        redaction_summary: dict[str, Any],
        session_context: dict[str, Any],
        trace_id: str | None = None,
        browser_evidence_id: str | None = None,
    ) -> BrowserPageState:
        page_state_id = new_id("bps")
        created_at = utc_now_iso()
        data = {
            "page_state_id": page_state_id,
            "organization_id": organization_id,
            "task_id": task_id,
            "tool_call_id": tool_call_id,
            "browser_profile_id": session_context.get("browser_profile_id"),
            "browser_session_id": session_context.get("browser_session_id"),
            "browser_evidence_id": browser_evidence_id,
            "page_key": page_key,
            "action": action,
            "action_status": action_status,
            "current_url": str(redact(current_url)) if current_url else None,
            "title": str(redact(title)) if title else None,
            "http_status": http_status,
            "dom_summary": redact(dom_summary),
            "network_summary": redact(network_summary),
            "console_summary": redact(console_summary),
            "task_checkpoint": redact(task_checkpoint),
            "redaction_summary": {
                "policy": "trace_service.redact",
                "cookie_redacted": True,
                "token_redacted": True,
                **redaction_summary,
            },
            "trace_id": trace_id,
            "created_at": created_at,
        }
        await self._repo.insert_page_state(data)
        if session_context.get("browser_session_id"):
            await self._touch_session_usage(
                str(session_context["browser_session_id"]),
                created_at=created_at,
                task_id=task_id,
                browser_evidence_id=browser_evidence_id,
                page_key=page_key,
            )
        return BrowserPageState(**data)

    async def list_profile_events(self, browser_profile_id: str) -> list[BrowserProfileEvent]:
        await self.get_profile(browser_profile_id)
        return [
            BrowserProfileEvent(**row)
            for row in await self._repo.list_profile_events(browser_profile_id)
        ]

    def _infer_health_decision(
        self,
        *,
        session: BrowserSession,
        profile: BrowserProfile,
        probe_type: str,
        provider_status: str | None,
        observed_status: str | None,
        failure_reason: str | None,
        recovery_hint: str | None,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        probed_at = utc_now_iso()
        if session.status in {"revoked", "cleared"} or profile.status == "revoked":
            return {
                "health_status": "session_expired",
                "login_state": "expired",
                "session_status": "expired",
                "failure_reason": failure_reason or "session_revoked",
                "recovery_hint": recovery_hint or "rebind_session_asset",
            }
        if _is_expired(session.expires_at):
            return {
                "health_status": "session_expired",
                "login_state": "expired",
                "session_status": "expired",
                "failure_reason": failure_reason or "session_expired",
                "recovery_hint": recovery_hint or "create_new_session_asset",
            }
        if provider_status in {"unreachable", "down", "offline"}:
            return {
                "health_status": "provider_unreachable",
                "login_state": "unknown",
                "session_status": "degraded",
                "failure_reason": failure_reason or "provider_unreachable",
                "recovery_hint": recovery_hint or "retry_when_provider_available",
            }
        if observed_status in {
            "healthy",
            "login_required",
            "session_expired",
            "provider_unreachable",
            "recovery_required",
            "degraded",
        }:
            health_status = observed_status
        elif evidence.get("login_required") or evidence.get("challenge_detected"):
            health_status = "login_required"
        elif evidence.get("session_expired"):
            health_status = "session_expired"
        elif evidence.get("recovery_required"):
            health_status = "recovery_required"
        elif evidence.get("degraded"):
            health_status = "degraded"
        else:
            health_status = "healthy"
        if health_status == "healthy":
            login_state = "authenticated"
            session_status = "active"
        elif health_status == "login_required":
            login_state = "login_required"
            session_status = "degraded"
        elif health_status == "session_expired":
            login_state = "expired"
            session_status = "expired"
        elif health_status == "provider_unreachable":
            login_state = "unknown"
            session_status = "degraded"
        elif health_status == "recovery_required":
            login_state = "recovery_required"
            session_status = "recovery_required"
        else:
            login_state = "degraded"
            session_status = "degraded"
        return {
            "health_status": health_status,
            "login_state": login_state,
            "session_status": session_status,
            "failure_reason": failure_reason or evidence.get("failure_reason"),
            "recovery_hint": recovery_hint or evidence.get("recovery_hint"),
            "probed_at": probed_at,
            "probe_type": probe_type,
        }

    def _ensure_session_usable(
        self,
        *,
        session: BrowserSession | None,
        profile: BrowserProfile,
        member_id: str | None,
        task_id: str | None,
        url: str | None,
    ) -> None:
        if profile.status != "active":
            raise AppError(
                "SESSION_PROFILE_INACTIVE",
                "浏览器 profile 已不可用",
                status_code=403,
                details={"profile_status": profile.status},
            )
        if session is None:
            return
        if session.status in {"revoked", "cleared"}:
            raise AppError(
                "SESSION_EXPIRED",
                "浏览器 session 已失效",
                status_code=409,
                details={
                    "session_status": session.status,
                    "health_status": session.health_status,
                    "login_state": session.login_state,
                    "recovery_hint": session.recovery_hint,
                },
            )
        if session.health_status == "login_required" or session.login_state == "login_required":
            raise AppError(
                "LOGIN_REQUIRED",
                "浏览器 session 登录态已失效，需要用户重新登录",
                status_code=409,
                details={
                    "session_status": session.status,
                    "health_status": session.health_status,
                    "login_state": session.login_state,
                    "recovery_hint": session.recovery_hint,
                },
            )
        if session.health_status == "session_expired" or session.status == "expired":
            raise AppError(
                "SESSION_EXPIRED",
                "浏览器 session 已过期，需要重新授权",
                status_code=409,
                details={
                    "session_status": session.status,
                    "health_status": session.health_status,
                    "login_state": session.login_state,
                    "recovery_hint": session.recovery_hint,
                },
            )
        if session.health_status in {
            "recovery_required",
            "provider_unreachable",
            "degraded",
        } or session.status in {"recovery_required", "degraded"}:
            raise AppError(
                "RECOVERY_REQUIRED",
                "浏览器 session 需要恢复后才能复用",
                status_code=409,
                details={
                    "session_status": session.status,
                    "health_status": session.health_status,
                    "login_state": session.login_state,
                    "recovery_hint": session.recovery_hint,
                },
            )
        if member_id and session.created_by_member_id and session.created_by_member_id != member_id:
            raise AppError(
                "SESSION_REUSE_DENIED",
                "浏览器 session 不允许跨成员复用",
                status_code=403,
                details={"reason": "member_mismatch"},
            )
        if task_id and session.reuse_policy.get("cross_task_reuse") is False:
            last_task_id = str(session.session_metadata.get("last_task_id") or "")
            if last_task_id and last_task_id != task_id:
                raise AppError(
                    "SESSION_REUSE_DENIED",
                    "浏览器 session 不允许跨任务复用",
                    status_code=403,
                    details={"reason": "task_mismatch"},
                )
        if url and profile.allowed_domains:
            parsed = urlsplit(url)
            host = parsed.hostname.lower() if parsed.hostname else None
            if host and host not in {item.lower() for item in profile.allowed_domains}:
                if not _domain_allowed(host, profile.allowed_domains):
                    raise AppError(
                        "SESSION_REUSE_DENIED",
                        "浏览器 session 不允许跨域名复用",
                        status_code=403,
                        details={"reason": "domain_mismatch"},
                    )

    async def _touch_session_usage(
        self,
        browser_session_id: str,
        *,
        created_at: str,
        task_id: str | None,
        browser_evidence_id: str | None,
        page_key: str | None,
    ) -> None:
        session = await self.get_session(browser_session_id)
        metadata = dict(session.session_metadata)
        if task_id:
            metadata["last_task_id"] = task_id
        if browser_evidence_id:
            metadata["last_browser_evidence_id"] = browser_evidence_id
        if page_key:
            metadata["last_page_key"] = page_key
        await self._repo.update_session(
            browser_session_id,
            {
                "session_metadata": metadata,
                "last_used_at": created_at,
                "updated_at": created_at,
            },
        )

    async def _event(
        self,
        browser_profile_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        browser_session_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        await self._repo.insert_profile_event(
            {
                "event_id": new_id("bpe"),
                "organization_id": "org_default",
                "browser_profile_id": browser_profile_id,
                "browser_session_id": browser_session_id,
                "event_type": event_type,
                "payload": payload,
                "payload_redacted": redact(payload),
                "trace_id": trace_id,
                "created_at": utc_now_iso(),
            }
        )


def _is_expired(value: Any) -> bool:
    if value is None:
        return False
    try:
        from datetime import UTC, datetime

        if isinstance(value, str):
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if isinstance(value, datetime):
            now = datetime.now(UTC) if value.tzinfo else datetime.now()
            return value <= now
    except Exception:
        return False
    return False


def _default_profile_policy(policy: dict[str, Any]) -> dict[str, Any]:
    return {
        "persistent_cookies": bool(policy.get("persistent_cookies", False)),
        "download_quarantine": bool(policy.get("download_quarantine", True)),
        "network_capture": str(policy.get("network_capture") or "metadata_only"),
        "console_capture": str(policy.get("console_capture") or "errors_only"),
        "allow_private_network": bool(policy.get("allow_private_network", False)),
    }


def _redact_url_query(url: str) -> tuple[str, bool]:
    parsed = urlsplit(url.strip())
    sensitive = False
    query_items = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key.lower().replace("-", "_") in SENSITIVE_QUERY_KEYS:
            sensitive = True
            query_items.append((key, "[REDACTED_QUERY_SECRET]"))
        else:
            query_items.append((key, value))
    query = "&".join(f"{key}={value}" for key, value in query_items)
    redacted_url = urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, parsed.fragment))
    return str(redact(redacted_url)), sensitive


def _domain_allowed(host: str, allowed_domains: list[str]) -> bool:
    normalized = [item.lower() for item in allowed_domains]
    return any(host == item or host.endswith(f".{item}") for item in normalized)


def _parse_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        return None
