from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from core_types import MemberStatus
from shell_runtime import ShellRuntime

from app.core.time import utc_now_iso
from app.db.repositories.shell_repo import ShellRepository
from app.db.session import Database

DEFAULT_ORGANIZATION_ID = "org_default"
DEFAULT_USER_ID = "user_local_owner"
DEFAULT_BRAIN_ID = "brain_not_configured"
DEFAULT_VOICE_PROFILE_ID = "vpr_default_edge"
DEFAULT_MEMBER_VOICE_IDS = {
    "xiaoyao": "zh-CN-XiaoxiaoNeural",
    "ningning": "zh-CN-XiaoyiNeural",
    "aheng": "zh-CN-YunxiNeural",
    "mobai": "zh-CN-YunjianNeural",
    "xiaoqi": "zh-CN-XiaohanNeural",
    "xiaowu": "zh-CN-YunyangNeural",
}
DEFAULT_MEMBER_ID = "mem_xiaoyao"
XIAOWU_MEMBER_ID = "mem_xiaowu"
DEFAULT_CONVERSATION_ID = "conv_default_xiaoyao"
WELCOME_MESSAGE_ID = "msg_welcome_xiaoyao"

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
        exists = await self._db.fetch_one(
            "SELECT brain_id FROM brains WHERE brain_id = ?",
            (DEFAULT_BRAIN_ID,),
        )
        if exists:
            return
        now = utc_now_iso()
        await self._db.execute(
            """
            INSERT INTO brains (
              brain_id, display_name, provider, endpoint, model_name, api_key_ref, is_local,
              context_window, supports_tools, supports_vision, supports_audio, cost_policy_json,
              privacy_policy_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, NULL, ?, NULL, 1, NULL, 0, 0, 0, ?, ?, ?, ?, ?)
            """,
            (
                DEFAULT_BRAIN_ID,
                "未配置大脑",
                "local_placeholder",
                "not_configured",
                json.dumps({"mode": "placeholder"}, ensure_ascii=False),
                json.dumps({"allow_cloud": False}, ensure_ascii=False),
                "not_configured",
                now,
                now,
            ),
        )

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
                json.dumps({"default": True, "reply_mode": "explicit_request_only"}, ensure_ascii=False),
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
        await self._ensure_member_voice_binding(
            member_id=XIAOWU_MEMBER_ID,
            member_name="小吴",
            member_key="xiaowu",
            provider_voice_id=DEFAULT_MEMBER_VOICE_IDS["xiaowu"],
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
                XIAOWU_MEMBER_ID,
                DEFAULT_ORGANIZATION_ID,
                department_ids.get("ceo_office"),
                role_ids.get("chief_of_staff"),
                "小吴",
                MemberStatus.NEEDS_CONFIGURATION.value,
                DEFAULT_BRAIN_ID,
                f"persona_{XIAOWU_MEMBER_ID}",
                json.dumps(
                    {
                        "tone": "playful_witty",
                        "preferences": [
                            "先给结论",
                            "少空话",
                            "别太慢",
                            "语气自然",
                            "可以轻松一点",
                        ],
                    },
                    ensure_ascii=False,
                ),
                json.dumps({"write_requires_source": True}, ensure_ascii=False),
                self._default_shell_id,
                json.dumps(
                    {
                        "source": "user_requested_member_seed",
                        "default_skills": [
                            "task_planning",
                            "review_summary",
                            "coordination",
                        ],
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
            (XIAOWU_MEMBER_ID, DEFAULT_ORGANIZATION_ID, now),
        )
        if self._persona_heart is not None:
            await self._persona_heart.ensure_default_profile(
                member_id=XIAOWU_MEMBER_ID,
                profile_id=f"persona_{XIAOWU_MEMBER_ID}",
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

        await self._upsert_skill_policy(
            subject_type="member",
            subject_id=XIAOWU_MEMBER_ID,
            allowed_skills=["task_planning", "review_summary", "coordination"],
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
