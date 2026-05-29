from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Any

from core_types import MemberStatus
from shell_runtime import ShellRuntime

from app.core.time import utc_now_iso
from app.db.repositories.shell_repo import ShellRepository
from app.db.session import Database

DEFAULT_ORGANIZATION_ID = "org_default"
DEFAULT_USER_ID = "user_local_owner"
DEFAULT_BRAIN_ID = "brain_not_configured"
DEFAULT_CODEX_API_KEY_ENV = "OPENAI_API_KEY"
DEFAULT_CODEX_API_KEY_REF = "codex-auth://OPENAI_API_KEY"
DEFAULT_CODEX_AUTH_API_KEY_REF = DEFAULT_CODEX_API_KEY_REF
DEFAULT_CODEX_DISPLAY_NAME = "Codex Default Brain"
DEFAULT_CODEX_ENDPOINT = "http://127.0.0.1:8317/v1"
DEFAULT_CODEX_MODEL = "gpt-5.4-mini"
DEFAULT_CODEX_PROXY_MODEL = DEFAULT_CODEX_MODEL
DEFAULT_CODEX_CONTEXT_WINDOW = 1000000
DEFAULT_CODEX_REASONING_EFFORT = "medium"
DEFAULT_CODEX_TEXT_VERBOSITY = "medium"
LEGACY_EDGEFN_API_KEY_REF = "env://EDGEFN_API_KEY"
DEFAULT_VOICE_PROFILE_ID = "vpr_default_edge"
DEFAULT_MEMBER_VOICE_IDS = {
    "xiaoyao": "zh-CN-XiaoxiaoNeural",
    "ningning": "zh-CN-XiaoyiNeural",
    "aheng": "zh-CN-YunxiNeural",
    "mobai": "zh-CN-YunjianNeural",
    "xiaoqi": "zh-CN-XiaohanNeural",
    "xiaowu": "zh-CN-YunyangNeural",
    "chenxi": "zh-CN-XiaomengNeural",
    "jihan": "zh-CN-YunfengNeural",
    "suyin": "zh-CN-XiaochenNeural",
    "qiaoqiao": "zh-CN-XiaoruiNeural",
    "anan": "zh-CN-XiaoshuangNeural",
}
DEFAULT_MEMBER_ID = "mem_xiaoyao"
XIAOWU_MEMBER_ID = "mem_xiaowu"
DEFAULT_CONVERSATION_ID = "conv_default_xiaoyao"
WELCOME_MESSAGE_ID = "msg_welcome_xiaoyao"
DIRECT_MEMBER_SEEDS = [
    {
        "member_id": "mem_xiaowu",
        "member_key": "xiaowu",
        "display_name": "小吴",
        "department_key": "ceo_office",
        "role_key": "chief_of_staff",
        "persona_profile_id": "persona_mem_xiaowu",
        "heart_profile": {
            "tone": "playful_witty",
            "preferences": [
                "先给结论",
                "少空话",
                "别太慢",
                "语气自然",
                "可以轻松一点",
            ],
        },
        "default_skills": [
            "task_planning",
            "review_summary",
            "coordination",
        ],
    },
    {
        "member_id": "mem_chenxi",
        "member_key": "chenxi",
        "display_name": "晨曦",
        "department_key": "ceo_office",
        "role_key": "chief_of_staff",
        "persona_profile_id": "persona_mem_chenxi",
        "heart_profile": {
            "tone": "reliable_warm",
            "preferences": ["先收束问题", "把优先级讲清楚", "给出清楚下一步"],
        },
        "default_skills": ["task_planning", "review_summary", "coordination"],
    },
    {
        "member_id": "mem_jihan",
        "member_key": "jihan",
        "display_name": "季寒",
        "department_key": "engineering",
        "role_key": "architect",
        "persona_profile_id": "persona_mem_jihan",
        "heart_profile": {
            "tone": "direct_professional",
            "preferences": ["先讲架构判断", "把风险摊开", "少绕弯"],
        },
        "default_skills": ["architecture_design", "code_generation", "deployment_debug"],
    },
    {
        "member_id": "mem_suyin",
        "member_key": "suyin",
        "display_name": "素音",
        "department_key": "product",
        "role_key": "product_manager",
        "persona_profile_id": "persona_mem_suyin",
        "heart_profile": {
            "tone": "structured_ux_sensitive",
            "preferences": ["先对齐目标", "拆清场景", "说明验收口径"],
        },
        "default_skills": ["requirement_analysis", "product_design", "roadmap_planning"],
    },
    {
        "member_id": "mem_qiaoqiao",
        "member_key": "qiaoqiao",
        "display_name": "乔乔",
        "department_key": "operations",
        "role_key": "content_operator",
        "persona_profile_id": "persona_mem_qiaoqiao",
        "heart_profile": {
            "tone": "creative_growth",
            "preferences": ["先给传播角度", "文案要能发", "别空泛"],
        },
        "default_skills": ["short_video_script", "social_copywriting", "content_calendar"],
    },
    {
        "member_id": "mem_anan",
        "member_key": "anan",
        "display_name": "安安",
        "department_key": "life_service",
        "role_key": "personal_butler",
        "persona_profile_id": "persona_mem_anan",
        "heart_profile": {
            "tone": "gentle_careful",
            "preferences": ["先稳住节奏", "一步一步来", "提醒要清楚"],
        },
        "default_skills": ["daily_planning", "emotional_support", "home_control"],
    },
]

if TYPE_CHECKING:
    from app.services.design_alignment import PersonaHeartService


class BootstrapService:
    def __init__(
        self,
        db: Database,
        shell_runtime: ShellRuntime,
        default_shell_id: str,
        persona_heart_service: PersonaHeartService | None = None,
    ) -> None:
        self._db = db
        self._shell_runtime = shell_runtime
        self._default_shell_id = default_shell_id
        self._persona_heart = persona_heart_service
        self._shells = ShellRepository(db)

    async def ensure_defaults(self) -> None:
        async with self._db.transaction():
            shell = self._shell_runtime.load(self._default_shell_id)
            bootstrap_config = self._bootstrap_config()
            await self._shells.upsert_shell(
                shell.shell_id,
                shell.display_name,
                shell.version,
                shell.model_dump(mode="json"),
            )
            organization_display_name = str(
                bootstrap_config.get("organization_display_name")
                or shell.display_name
                or "Default Organization"
            )
            await self._ensure_organization(shell.default_owner_title, organization_display_name)
            department_ids = await self._ensure_departments()
            role_ids = await self._ensure_roles(department_ids)
            await self._ensure_default_brain()
            await self._ensure_default_voice_profile()
            await self._ensure_members(department_ids, role_ids)
            await self._ensure_user_requested_members(department_ids, role_ids)
            await self._ensure_member_voice_bindings()
            await self._ensure_default_skill_policies()
            await self._ensure_default_conversation(bootstrap_config)
            await self._ensure_welcome_message(bootstrap_config)

    async def _ensure_organization(self, owner_title: str, display_name: str) -> None:
        exists = await self._db.fetch_one(
            "SELECT organization_id FROM organizations WHERE organization_id = ?",
            (DEFAULT_ORGANIZATION_ID,),
        )
        if exists:
            return
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO organizations (
              organization_id, shell_id, display_name, owner_user_id, owner_title,
              settings_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_ORGANIZATION_ID,
                self._default_shell_id,
                display_name,
                DEFAULT_USER_ID,
                owner_title,
                json.dumps({"local_first": True}, ensure_ascii=False),
                now,
                now,
            ),
        )

    async def _ensure_departments(self) -> dict[str, str]:
        data = self._shell_runtime.read_shell_file(self._default_shell_id, "departments.yaml")
        ids: dict[str, str] = {}
        now = utc_now_iso()
        for item in data.get("departments", []):
            department_id = f"dept_{item['key']}"
            ids[item["key"]] = department_id
            await self._db.execute(
                """
                INSERT INTO departments (
                  department_id, organization_id, parent_department_id, key, display_name,
                  description, sort_order, metadata_json, created_at, updated_at
                ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(department_id) DO NOTHING
                """,
                (
                    department_id,
                    DEFAULT_ORGANIZATION_ID,
                    item["key"],
                    item["display_name"],
                    item.get("description"),
                    int(item.get("sort_order", 0)),
                    json.dumps({"source": "shell_template"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return ids

    async def _ensure_roles(self, department_ids: dict[str, str]) -> dict[str, str]:
        data = self._shell_runtime.read_shell_file(self._default_shell_id, "roles.yaml")
        ids: dict[str, str] = {}
        now = utc_now_iso()
        for item in data.get("roles", []):
            role_id = f"role_{item['key']}"
            ids[item["key"]] = role_id
            default_department_id = department_ids.get(item.get("default_department_key", ""))
            await self._db.execute(
                """
                INSERT INTO roles (
                  role_id, organization_id, key, display_name, description,
                  default_department_id, default_skills_json, authority_level,
                  metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(role_id) DO NOTHING
                """,
                (
                    role_id,
                    DEFAULT_ORGANIZATION_ID,
                    item["key"],
                    item["display_name"],
                    item.get("description"),
                    default_department_id,
                    json.dumps(item.get("default_skills", []), ensure_ascii=False),
                    int(item.get("authority_level", 0)),
                    json.dumps({"source": "shell_template"}, ensure_ascii=False),
                    now,
                    now,
                ),
            )
        return ids

    async def _ensure_default_brain(self) -> None:
        existing = await self._db.fetch_one(
            "SELECT * FROM brains WHERE brain_id = ?",
            (DEFAULT_BRAIN_ID,),
        )
        seed = self._default_codex_brain_seed()
        if existing:
            await self._repair_default_brain_if_managed(dict(existing), seed)
            return
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO brains (
              brain_id, display_name, provider, endpoint, model_name, api_key_ref, is_local,
              context_window, supports_tools, supports_vision, supports_audio, cost_policy_json,
              privacy_policy_json, status, default_temperature, default_top_p,
              default_max_output_tokens, timeout_seconds, retry_count, allow_fallback,
              allow_cloud, streaming_supported, protocol_family, request_format,
              response_format, supports_stream, verify_capabilities_json, created_at, updated_at
            ) VALUES (
              ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            (
                DEFAULT_BRAIN_ID,
                seed["display_name"],
                seed["provider"],
                seed["endpoint"],
                seed["model_name"],
                seed["api_key_ref"],
                1 if seed["is_local"] else 0,
                seed["context_window"],
                1 if seed["supports_tools"] else 0,
                1 if seed["supports_vision"] else 0,
                1 if seed["supports_audio"] else 0,
                json.dumps(seed["cost_policy"], ensure_ascii=False),
                json.dumps(seed["privacy_policy"], ensure_ascii=False),
                seed["status"],
                seed["default_temperature"],
                seed["default_top_p"],
                seed["default_max_output_tokens"],
                seed["timeout_seconds"],
                seed["retry_count"],
                1 if seed["allow_fallback"] else 0,
                1 if seed["allow_cloud"] else 0,
                1 if seed["streaming_supported"] else 0,
                seed["protocol_family"],
                seed["request_format"],
                seed["response_format"],
                1 if seed["supports_stream"] else 0,
                json.dumps(seed["verify_capabilities"], ensure_ascii=False),
                now,
                now,
            ),
        )

    async def _repair_default_brain_if_managed(
        self,
        existing: dict[str, Any],
        seed: dict[str, Any],
    ) -> None:
        is_legacy_placeholder = (
            existing.get("provider") == "local_placeholder"
            and existing.get("model_name") == "not_configured"
            and existing.get("status") == "not_configured"
        )
        is_default_codex_brain_record = (
            existing.get("brain_id") == DEFAULT_BRAIN_ID
            and existing.get("provider")
            in {seed["provider"], "openai", "openai_compatible", "custom_openai_compatible"}
            and str(existing.get("model_name") or "")
            in {
                str(seed["model_name"]),
                DEFAULT_CODEX_MODEL,
                "gpt-5.4-mini",
                "GPT-5.3-Codex",
                "MiniMax-M2.5",
                "not_configured",
            }
        )
        is_managed_codex_default = (
            existing.get("provider") == seed["provider"]
            and existing.get("display_name")
            in {
                seed["display_name"],
                "Codex Default Brain",
                "Codex 默认大脑",
                "EdgeFn Default Brain",
            }
        ) or is_default_codex_brain_record
        if not (is_legacy_placeholder or is_managed_codex_default):
            return

        fields = {
            "display_name": seed["display_name"],
            "provider": seed["provider"],
            "endpoint": seed["endpoint"],
            "model_name": seed["model_name"],
            "is_local": 1 if seed["is_local"] else 0,
            "context_window": seed["context_window"],
            "supports_tools": 1 if seed["supports_tools"] else 0,
            "supports_vision": 1 if seed["supports_vision"] else 0,
            "supports_audio": 1 if seed["supports_audio"] else 0,
            "cost_policy_json": json.dumps(seed["cost_policy"], ensure_ascii=False),
            "privacy_policy_json": json.dumps(seed["privacy_policy"], ensure_ascii=False),
            "status": seed["status"],
            "default_temperature": seed["default_temperature"],
            "default_top_p": seed["default_top_p"],
            "default_max_output_tokens": seed["default_max_output_tokens"],
            "timeout_seconds": seed["timeout_seconds"],
            "retry_count": seed["retry_count"],
            "allow_fallback": 1 if seed["allow_fallback"] else 0,
            "allow_cloud": 1 if seed["allow_cloud"] else 0,
            "streaming_supported": 1 if seed["streaming_supported"] else 0,
            "protocol_family": seed["protocol_family"],
            "request_format": seed["request_format"],
            "response_format": seed["response_format"],
            "supports_stream": 1 if seed["supports_stream"] else 0,
            "verify_capabilities_json": json.dumps(seed["verify_capabilities"], ensure_ascii=False),
            "last_error_code": None,
            "last_error_message": None,
            "latency_ms": None,
            "updated_at": utc_now_iso(),
        }
        existing_ref = existing.get("api_key_ref")
        seed_ref = seed["api_key_ref"]
        env_ref = f"env://{DEFAULT_CODEX_API_KEY_ENV}"
        if is_legacy_placeholder or existing_ref in {
            None,
            "",
            env_ref,
            DEFAULT_CODEX_API_KEY_REF,
            LEGACY_EDGEFN_API_KEY_REF,
        } or (
            is_managed_codex_default
            and seed_ref in {env_ref, DEFAULT_CODEX_API_KEY_REF}
        ):
            fields["api_key_ref"] = seed_ref
        else:
            fields["status"] = (
                "configured"
                if existing.get("status") == "needs_configuration"
                else str(existing.get("status") or "configured")
            )

        assignments = ", ".join(f"{column} = ?" for column in fields)
        await self._db.execute(
            f"UPDATE brains SET {assignments} WHERE brain_id = ?",
            (*fields.values(), DEFAULT_BRAIN_ID),
        )

    def _default_codex_brain_seed(self) -> dict[str, Any]:
        runtime = _read_codex_runtime_config()
        api_key_ref = runtime["api_key_ref"]
        return {
            "display_name": DEFAULT_CODEX_DISPLAY_NAME,
            "provider": runtime["provider"],
            "endpoint": runtime["endpoint"],
            "model_name": runtime["model"],
            "api_key_ref": api_key_ref,
            "is_local": False,
            "context_window": runtime["context_window"],
            "supports_tools": True,
            "supports_vision": True,
            "supports_audio": False,
            "cost_policy": {
                "mode": "cloud",
                "profile": "codex_current_default",
            },
            "privacy_policy": {
                "allow_cloud": True,
                "provider_display_name": "OpenAI",
                "adapter_family": "openai_compatible",
                "codex_profile": "current_codex",
                "codex_wire_api": runtime["wire_api"],
                "codex_provider": runtime["codex_provider"],
                "requires_openai_auth": runtime["requires_openai_auth"],
                "reasoning_effort": runtime["reasoning_effort"],
                "text_verbosity": DEFAULT_CODEX_TEXT_VERBOSITY,
                "disable_response_storage": True,
                "approvals_reviewer": "user",
                "api_key_ref_scheme": str(api_key_ref or "").split("://", 1)[0] or None,
            },
            "status": "configured" if api_key_ref else "needs_configuration",
            "default_temperature": 0.2,
            "default_top_p": 1.0,
            "default_max_output_tokens": 4096,
            "timeout_seconds": 300,
            "retry_count": 1,
            "allow_fallback": True,
            "allow_cloud": True,
            "streaming_supported": True,
            "protocol_family": runtime["wire_api"],
            "request_format": runtime["wire_api"],
            "response_format": (
                "openai_responses" if runtime["wire_api"] == "responses" else "auto"
            ),
            "supports_stream": True,
            "verify_capabilities": {},
        }

    async def _ensure_default_voice_profile(self) -> None:
        exists = await self._db.fetch_one(
            "SELECT voice_profile_id FROM voice_profiles WHERE voice_profile_id = ?",
            (DEFAULT_VOICE_PROFILE_ID,),
        )
        if exists:
            return
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO voice_profiles (
              voice_profile_id, organization_id, display_name, provider, provider_voice_id,
              output_format, sample_text, sample_audio_uri, config_json, secret_ref, status,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, NULL, 'active', ?, ?)
            """,
            (
                DEFAULT_VOICE_PROFILE_ID,
                DEFAULT_ORGANIZATION_ID,
                "默认 Edge 中文声音",
                "edge",
                "zh-CN-XiaoxiaoNeural",
                "wav",
                "你好，我是本地智能体成员。",
                json.dumps(
                    {"default": True, "reply_mode": "explicit_request_only"},
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )

    async def _ensure_member_voice_bindings(self) -> None:
        now = utc_now_iso()
        for template in self._member_templates():
            key = str(template["key"])
            await self._ensure_member_voice_binding(
                member_id=f"mem_{key}",
                member_name=str(template["name"]),
                member_key=key,
                provider_voice_id=DEFAULT_MEMBER_VOICE_IDS.get(
                    key,
                    "zh-CN-XiaoxiaoNeural",
                ),
                source="shell_template",
                now=now,
            )
        for seed in DIRECT_MEMBER_SEEDS:
            await self._ensure_member_voice_binding(
                member_id=str(seed["member_id"]),
                member_name=str(seed["display_name"]),
                member_key=str(seed["member_key"]),
                provider_voice_id=DEFAULT_MEMBER_VOICE_IDS[str(seed["member_key"])],
                source="user_requested_member_seed",
                now=now,
            )

    async def _ensure_member_voice_binding(
        self,
        *,
        member_id: str,
        member_name: str,
        member_key: str,
        provider_voice_id: str,
        source: str,
        now: str,
    ) -> None:
        profile_id = f"vpr_member_{member_key}_edge"
        await self._db.execute(
            """
            INSERT INTO voice_profiles (
              voice_profile_id, organization_id, display_name, provider, provider_voice_id,
              output_format, sample_text, sample_audio_uri, config_json, secret_ref, status,
              created_at, updated_at
            ) VALUES (?, ?, ?, 'edge', ?, 'wav', ?, NULL, ?, NULL, 'active', ?, ?)
            ON CONFLICT(voice_profile_id) DO NOTHING
            """,
            (
                profile_id,
                DEFAULT_ORGANIZATION_ID,
                f"{member_name}专属声音",
                provider_voice_id,
                f"你好，我是{member_name}。",
                json.dumps(
                    {
                        "default": True,
                        "member_id": member_id,
                        "source": source,
                        "reply_mode": "explicit_request_only",
                    },
                    ensure_ascii=False,
                ),
                now,
                now,
            ),
        )
        existing = await self._db.fetch_one(
            """
            SELECT binding_id
            FROM member_voice_bindings
            WHERE member_id = ?
              AND binding_scope = 'default'
              AND status = 'active'
            LIMIT 1
            """,
            (member_id,),
        )
        if existing:
            return
        await self._db.execute(
            """
            INSERT INTO member_voice_bindings (
              binding_id, organization_id, member_id, voice_profile_id, binding_scope,
              reply_mode, priority, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'default', 'explicit_request_only', 100, 'active', ?, ?)
            """,
            (
                f"vbind_{member_key}_default",
                DEFAULT_ORGANIZATION_ID,
                member_id,
                profile_id,
                now,
                now,
            ),
        )

    async def _ensure_members(
        self,
        department_ids: dict[str, str],
        role_ids: dict[str, str],
    ) -> None:
        now = utc_now_iso()
        for template in self._member_templates():
            member_id = f"mem_{template['key']}"
            await self._db.execute(
                """
                INSERT INTO members (
                  member_id, organization_id, department_id, role_id, display_name, avatar_uri,
                  status, default_brain_id, persona_profile_id, heart_profile_json,
                  memory_policy_json, created_from_shell_id, created_from_template_id,
                  metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(member_id) DO NOTHING
                """,
                (
                    member_id,
                    DEFAULT_ORGANIZATION_ID,
                    department_ids.get(template["department"]),
                    role_ids.get(template["role"]),
                    template["name"],
                    MemberStatus.NEEDS_CONFIGURATION.value,
                    DEFAULT_BRAIN_ID,
                    template["persona"],
                    json.dumps({"tone": "professional_warm"}, ensure_ascii=False),
                    json.dumps({"write_requires_source": True}, ensure_ascii=False),
                    self._default_shell_id,
                    template["key"],
                    json.dumps(
                        {"default_skills": template.get("default_skills", [])},
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            await self._db.execute(
                """
                INSERT INTO member_availability (
                  member_id, organization_id, status, capacity, current_load,
                  unavailable_reason, schedule_json, source, updated_at
                ) VALUES (?, ?, 'available', 1, 0, NULL, '{}', 'shell_template', ?)
                ON CONFLICT(member_id) DO NOTHING
                """,
                (member_id, DEFAULT_ORGANIZATION_ID, now),
            )

    async def _ensure_user_requested_members(
        self,
        department_ids: dict[str, str],
        role_ids: dict[str, str],
    ) -> None:
        now = utc_now_iso()
        for seed in DIRECT_MEMBER_SEEDS:
            member_id = str(seed["member_id"])
            profile_id = str(seed["persona_profile_id"])
            default_skills = list(seed.get("default_skills", []))
            await self._db.execute(
                """
                INSERT INTO members (
                  member_id, organization_id, department_id, role_id, display_name, avatar_uri,
                  status, default_brain_id, persona_profile_id, heart_profile_json,
                  memory_policy_json, created_from_shell_id, created_from_template_id,
                  metadata_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?)
                ON CONFLICT(member_id) DO NOTHING
                """,
                (
                    member_id,
                    DEFAULT_ORGANIZATION_ID,
                    department_ids.get(str(seed["department_key"])),
                    role_ids.get(str(seed["role_key"])),
                    str(seed["display_name"]),
                    MemberStatus.NEEDS_CONFIGURATION.value,
                    DEFAULT_BRAIN_ID,
                    profile_id,
                    json.dumps(seed["heart_profile"], ensure_ascii=False),
                    json.dumps({"write_requires_source": True}, ensure_ascii=False),
                    self._default_shell_id,
                    json.dumps(
                        {
                            "source": "user_requested_member_seed",
                            "seed_key": seed["member_key"],
                            "default_skills": default_skills,
                        },
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                ),
            )
            await self._db.execute(
                """
                INSERT INTO member_availability (
                  member_id, organization_id, status, capacity, current_load,
                  unavailable_reason, schedule_json, source, updated_at
                ) VALUES (?, ?, 'available', 1, 0, NULL, '{}', 'user_requested_member_seed', ?)
                ON CONFLICT(member_id) DO NOTHING
                """,
                (member_id, DEFAULT_ORGANIZATION_ID, now),
            )
            if self._persona_heart is not None:
                await self._persona_heart.ensure_default_profile(
                    member_id=member_id,
                    profile_id=profile_id,
                )

    async def _ensure_default_skill_policies(self) -> None:
        now = utc_now_iso()
        for template in self._member_templates():
            member_id = f"mem_{template['key']}"
            await self._upsert_skill_policy(
                subject_type="member",
                subject_id=member_id,
                allowed_skills=template.get("default_skills", []),
                now=now,
            )

        roles_data = self._shell_runtime.read_shell_file(self._default_shell_id, "roles.yaml")
        for role in roles_data.get("roles", []):
            await self._upsert_skill_policy(
                subject_type="role",
                subject_id=f"role_{role['key']}",
                allowed_skills=role.get("default_skills", []),
                now=now,
            )

        departments_data = self._shell_runtime.read_shell_file(
            self._default_shell_id,
            "departments.yaml",
        )
        for department in departments_data.get("departments", []):
            await self._upsert_skill_policy(
                subject_type="department",
                subject_id=f"dept_{department['key']}",
                allowed_skills=department.get("default_skills", []),
                now=now,
            )

        for seed in DIRECT_MEMBER_SEEDS:
            await self._upsert_skill_policy(
                subject_type="member",
                subject_id=str(seed["member_id"]),
                allowed_skills=list(seed.get("default_skills", [])),
                now=now,
            )

    async def _upsert_skill_policy(
        self,
        *,
        subject_type: str,
        subject_id: str,
        allowed_skills: list[str],
        now: str,
    ) -> None:
        policy_id = f"msp_{subject_type}_{subject_id}"
        await self._db.execute(
            """
            INSERT INTO member_skill_policies (
              policy_id, organization_id, subject_type, subject_id, allowed_skills_json,
              denied_skills_json, allowed_mcp_tools_json, denied_mcp_tools_json,
              risk_policy_json, source, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, '[]', '[]', '[]', '{}', 'shell_template', ?, ?)
            ON CONFLICT(organization_id, subject_type, subject_id) DO NOTHING
            """,
            (
                policy_id,
                DEFAULT_ORGANIZATION_ID,
                subject_type,
                subject_id,
                json.dumps(allowed_skills, ensure_ascii=False),
                now,
                now,
            ),
        )

    async def _ensure_default_conversation(self, bootstrap_config: dict[str, Any]) -> None:
        exists = await self._db.fetch_one(
            "SELECT conversation_id FROM conversations WHERE conversation_id = ?",
            (DEFAULT_CONVERSATION_ID,),
        )
        if exists:
            return
        now = utc_now_iso()
        default_member_name = str(self._default_member_template().get("name") or DEFAULT_MEMBER_ID)
        title_template = str(
            bootstrap_config.get("conversation_title_template") or "Conversation with {member_name}"
        )
        conversation_title = title_template.replace("{member_name}", default_member_name)
        await self._db.execute(
            """
            INSERT INTO conversations (
              conversation_id, organization_id, title, conversation_type, primary_member_id,
              participant_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_CONVERSATION_ID,
                DEFAULT_ORGANIZATION_ID,
                conversation_title,
                "single",
                DEFAULT_MEMBER_ID,
                json.dumps([{"type": "member", "id": DEFAULT_MEMBER_ID}], ensure_ascii=False),
                "active",
                now,
                now,
            ),
        )

    async def _ensure_welcome_message(self, bootstrap_config: dict[str, Any]) -> None:
        exists = await self._db.fetch_one(
            "SELECT message_id FROM messages WHERE message_id = ?",
            (WELCOME_MESSAGE_ID,),
        )
        if exists:
            return
        now = utc_now_iso()
        welcome_text = str(bootstrap_config.get("welcome_message") or "Ready when you are.")
        await self._db.execute(
            """
            INSERT INTO messages (
              message_id, conversation_id, turn_id, author_type, author_id, content_type,
              content_text, content_json, trace_id, created_at
            ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                WELCOME_MESSAGE_ID,
                DEFAULT_CONVERSATION_ID,
                "assistant",
                DEFAULT_MEMBER_ID,
                "text",
                welcome_text,
                json.dumps({"type": "text", "text": welcome_text}, ensure_ascii=False),
                now,
            ),
        )
        await self._db.execute(
            "INSERT INTO messages_fts (content_text, message_id) VALUES (?, ?)",
            (welcome_text, WELCOME_MESSAGE_ID),
        )

    def _bootstrap_config(self) -> dict[str, Any]:
        data = self._shell_runtime.read_shell_file(self._default_shell_id, "shell.yaml")
        config = data.get("bootstrap", {})
        return dict(config) if isinstance(config, dict) else {}

    def _default_member_template(self) -> dict[str, Any]:
        for member in self._member_templates():
            if member.get("default") is True:
                return member
        members = self._member_templates()
        if not members:
            raise RuntimeError("Shell has no member templates")
        return members[0]

    def _member_templates(self) -> list[dict[str, Any]]:
        data = self._shell_runtime.read_shell_file(self._default_shell_id, "member_templates.yaml")
        members = data.get("members", [])
        return [dict(member) for member in members]


def _read_codex_runtime_config() -> dict[str, Any]:
    endpoint_override = os.environ.get("CYCBER_REAL_MODEL_ENDPOINT")
    model_override = os.environ.get("CYCBER_REAL_MODEL_MODEL") or os.environ.get(
        "CYCBER_REAL_MODEL_NAME"
    )
    wire_api_override = os.environ.get("CYCBER_REAL_MODEL_WIRE_API")
    reasoning_override = os.environ.get("CYCBER_REAL_MODEL_REASONING_EFFORT")
    api_key_ref = os.environ.get("CYCBER_REAL_MODEL_API_KEY_REF", DEFAULT_CODEX_AUTH_API_KEY_REF)
    codex_config = _read_codex_config()
    provider_key = str(codex_config.get("model_provider") or "custom")
    provider_config = _codex_provider_config(codex_config, provider_key)
    wire_api = str(wire_api_override or provider_config.get("wire_api") or "responses").lower()
    if wire_api not in {"responses", "chat_completions"}:
        wire_api = "responses"
    reasoning_effort = str(reasoning_override or DEFAULT_CODEX_REASONING_EFFORT)
    return {
        "codex_provider": provider_key,
        "provider": "openai_compatible",
        "endpoint": endpoint_override
        or str(provider_config.get("base_url") or DEFAULT_CODEX_ENDPOINT),
        "model": model_override or DEFAULT_CODEX_MODEL,
        "wire_api": wire_api,
        "reasoning_effort": reasoning_effort,
        "requires_openai_auth": bool(provider_config.get("requires_openai_auth", True)),
        "api_key_ref": api_key_ref,
        "context_window": DEFAULT_CODEX_CONTEXT_WINDOW,
    }


def _read_codex_config() -> dict[str, Any]:
    config_path = Path.home() / ".codex" / "config.toml"
    if not config_path.exists():
        return {}
    try:
        data = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _codex_provider_config(config: dict[str, Any], provider_key: str) -> dict[str, Any]:
    providers = config.get("model_providers")
    if not isinstance(providers, dict):
        return {}
    provider = providers.get(provider_key)
    return provider if isinstance(provider, dict) else {}
