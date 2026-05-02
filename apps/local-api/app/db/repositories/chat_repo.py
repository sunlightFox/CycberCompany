from __future__ import annotations

import json
from typing import Any

from app.db.session import Database

TURN_UPDATE_COLUMNS = {
    "assistant_message_id",
    "status",
    "intent",
    "mode",
    "privacy_level",
    "route_json",
    "usage_json",
    "events_json",
    "experience_json",
    "error_code",
    "error_message",
    "cancel_requested",
    "brain_decision_id",
    "updated_at",
    "ended_at",
}


class ChatRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def list_conversations(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT conversation_id, organization_id, title, conversation_type, primary_member_id,
                   participant_json, status, created_at, updated_at
            FROM conversations
            ORDER BY updated_at DESC
            """
        )
        return [self._conversation_from_row(dict(row)) for row in rows]

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT conversation_id, organization_id, title, conversation_type, primary_member_id,
                   participant_json, status, created_at, updated_at
            FROM conversations
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        )
        if row is None:
            return None
        conversation = self._conversation_from_row(dict(row))
        conversation["messages"] = await self.list_messages(conversation_id)
        return conversation

    async def create_conversation(
        self,
        *,
        conversation_id: str,
        organization_id: str,
        title: str,
        primary_member_id: str,
        participants: list[dict[str, Any]],
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO conversations (
              conversation_id, organization_id, title, conversation_type, primary_member_id,
              participant_json, status, created_at, updated_at
            ) VALUES (?, ?, ?, 'single', ?, ?, 'active', ?, ?)
            """,
            (
                conversation_id,
                organization_id,
                title,
                primary_member_id,
                json.dumps(participants, ensure_ascii=False),
                created_at,
                created_at,
            ),
        )

    async def list_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT message_id, conversation_id, turn_id, author_type, author_id, content_type,
                   content_text, content_json, trace_id, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at ASC
            """,
            (conversation_id,),
        )
        return [self._message_from_row(dict(row)) for row in rows]

    async def list_recent_messages(
        self,
        conversation_id: str,
        limit: int = 12,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT message_id, conversation_id, turn_id, author_type, author_id, content_type,
                   content_text, content_json, trace_id, created_at
            FROM messages
            WHERE conversation_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (conversation_id, limit),
        )
        return [
            self._message_from_row(dict(row))
            for row in reversed(rows)
        ]

    async def get_message(self, message_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT message_id, conversation_id, turn_id, author_type, author_id, content_type,
                   content_text, content_json, trace_id, created_at
            FROM messages
            WHERE message_id = ?
            """,
            (message_id,),
        )
        return self._message_from_row(dict(row)) if row else None

    async def insert_message(
        self,
        *,
        message_id: str,
        conversation_id: str,
        turn_id: str | None,
        author_type: str,
        author_id: str | None,
        content_type: str,
        content_text: str | None,
        content: dict[str, Any],
        trace_id: str | None,
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO messages (
              message_id, conversation_id, turn_id, author_type, author_id, content_type,
              content_text, content_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message_id,
                conversation_id,
                turn_id,
                author_type,
                author_id,
                content_type,
                content_text,
                json.dumps(content, ensure_ascii=False),
                trace_id,
                created_at,
            ),
        )
        if content_text:
            await self._db.execute(
                "INSERT INTO messages_fts (content_text, message_id) VALUES (?, ?)",
                (content_text, message_id),
            )

    async def insert_turn(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        member_id: str,
        user_message_id: str,
        trace_id: str,
        status: str,
        retry_of_turn_id: str | None,
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO chat_turns (
              turn_id, conversation_id, member_id, user_message_id, assistant_message_id,
              trace_id, status, route_json, usage_json, events_json, retry_of_turn_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, NULL, ?, ?, '{}', '{}', '[]', ?, ?, ?)
            """,
            (
                turn_id,
                conversation_id,
                member_id,
                user_message_id,
                trace_id,
                status,
                retry_of_turn_id,
                created_at,
                created_at,
            ),
        )

    async def get_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one("SELECT * FROM chat_turns WHERE turn_id = ?", (turn_id,))
        if row is None:
            return None
        return self._turn_from_row(dict(row))

    async def update_turn(self, turn_id: str, **fields: Any) -> None:
        if not fields:
            return
        json_fields = {
            "route": "route_json",
            "usage": "usage_json",
            "events": "events_json",
            "experience": "experience_json",
        }
        values: dict[str, Any] = {}
        for key, value in fields.items():
            column = json_fields.get(key, key)
            if column not in TURN_UPDATE_COLUMNS:
                raise ValueError(f"Unsupported chat_turns update column: {column}")
            if key in json_fields:
                values[column] = json.dumps(value, ensure_ascii=False)
            elif column == "cancel_requested":
                values[column] = 1 if bool(value) else 0
            else:
                values[column] = value
        assignments = ", ".join(f"{column} = ?" for column in values)
        await self._db.execute(
            f"UPDATE chat_turns SET {assignments} WHERE turn_id = ?",
            (*values.values(), turn_id),
        )

    async def try_mark_turn_running(self, turn_id: str, updated_at: str) -> bool:
        rowcount = await self._db.execute(
            """
            UPDATE chat_turns
            SET status = 'running', updated_at = ?
            WHERE turn_id = ?
              AND status = 'created'
              AND cancel_requested = 0
            """,
            (updated_at, turn_id),
        )
        return rowcount == 1

    async def cancel_created_turn(
        self,
        turn_id: str,
        *,
        error_code: str,
        error_message: str,
        events: list[dict[str, Any]],
        updated_at: str,
    ) -> bool:
        rowcount = await self._db.execute(
            """
            UPDATE chat_turns
            SET status = 'cancelled',
                error_code = ?,
                error_message = ?,
                events_json = ?,
                updated_at = ?,
                ended_at = ?
            WHERE turn_id = ?
              AND status = 'created'
            """,
            (
                error_code,
                error_message,
                json.dumps(events, ensure_ascii=False),
                updated_at,
                updated_at,
                turn_id,
            ),
        )
        return rowcount == 1

    async def list_running_turns(self) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM chat_turns
            WHERE status = 'running'
            ORDER BY created_at ASC
            """
        )
        return [self._turn_from_row(dict(row)) for row in rows]

    async def mark_running_turns_failed(self, updated_at: str) -> int:
        rows = await self.list_running_turns()
        await self._db.execute(
            """
            UPDATE chat_turns
            SET status = 'failed',
                error_code = 'CHAT_RUNTIME_FAILED',
                error_message = '服务重启后运行中的 turn 已被关闭',
                updated_at = ?,
                ended_at = ?
            WHERE status = 'running'
            """,
            (updated_at, updated_at),
        )
        return len(rows)

    async def next_event_sequence(self, turn_id: str) -> int:
        row = await self._db.fetch_one(
            "SELECT COALESCE(MAX(sequence), 0) AS max_sequence FROM chat_events WHERE turn_id = ?",
            (turn_id,),
        )
        return int(row["max_sequence"]) + 1 if row else 1

    async def insert_event(
        self,
        *,
        event_id: str,
        turn_id: str,
        sequence: int,
        event_type: str,
        trace_id: str | None,
        payload: dict[str, Any],
        created_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO chat_events (
              event_id, turn_id, sequence, event_type, trace_id, payload_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id,
                turn_id,
                sequence,
                event_type,
                trace_id,
                json.dumps(payload, ensure_ascii=False),
                created_at,
            ),
        )

    async def list_events(
        self,
        turn_id: str,
        *,
        after_sequence: int = 0,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT event_id, turn_id, sequence, event_type, trace_id, payload_json, created_at
            FROM chat_events
            WHERE turn_id = ? AND sequence > ?
            ORDER BY sequence ASC
            """,
            (turn_id, after_sequence),
        )
        return [self._event_from_row(dict(row)) for row in rows]

    async def insert_recovery_attempt(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO chat_turn_recovery_attempts (
              recovery_attempt_id, organization_id, turn_id, task_id, attempt_index,
              failure_type, root_cause, recovery_action, status, diagnostic_payload_json,
              trace_id, started_at, completed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["recovery_attempt_id"],
                data.get("organization_id") or "org_default",
                data["turn_id"],
                data.get("task_id"),
                int(data["attempt_index"]),
                data["failure_type"],
                data["root_cause"],
                data["recovery_action"],
                data["status"],
                json.dumps(data.get("diagnostic_payload", {}), ensure_ascii=False),
                data.get("trace_id"),
                data["started_at"],
                data.get("completed_at"),
            ),
        )

    async def update_recovery_attempt(
        self,
        recovery_attempt_id: str,
        *,
        status: str,
        diagnostic_payload: dict[str, Any],
        completed_at: str,
    ) -> None:
        await self._db.execute(
            """
            UPDATE chat_turn_recovery_attempts
            SET status = ?,
                diagnostic_payload_json = ?,
                completed_at = ?
            WHERE recovery_attempt_id = ?
            """,
            (
                status,
                json.dumps(diagnostic_payload, ensure_ascii=False),
                completed_at,
                recovery_attempt_id,
            ),
        )

    async def list_recovery_attempts(self, turn_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM chat_turn_recovery_attempts
            WHERE turn_id = ?
            ORDER BY attempt_index ASC, started_at ASC
            """,
            (turn_id,),
        )
        return [self._recovery_attempt_from_row(dict(row)) for row in rows]

    async def upsert_conversation_summary(
        self,
        *,
        summary_id: str,
        conversation_id: str,
        summary_text: str,
        source_turn_id: str,
        token_estimate: int,
        updated_at: str,
    ) -> None:
        await self._db.execute(
            """
            INSERT INTO conversation_summaries (
              summary_id, conversation_id, summary_text, source_turn_id, token_estimate,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
              summary_text = excluded.summary_text,
              source_turn_id = excluded.source_turn_id,
              token_estimate = excluded.token_estimate,
              updated_at = excluded.updated_at
            """,
            (
                summary_id,
                conversation_id,
                summary_text,
                source_turn_id,
                token_estimate,
                updated_at,
                updated_at,
            ),
        )

    async def request_cancel(self, turn_id: str, updated_at: str) -> None:
        await self._db.execute(
            """
            UPDATE chat_turns
            SET cancel_requested = 1, updated_at = ?
            WHERE turn_id = ?
              AND status IN ('created', 'running')
            """,
            (updated_at, turn_id),
        )

    async def get_latest_summary(self, conversation_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT summary_id, conversation_id, summary_text, source_turn_id, token_estimate,
                   created_at, updated_at
            FROM conversation_summaries
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        )
        return dict(row) if row else None

    async def get_working_state(self, conversation_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM conversation_working_states
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        )
        return self._working_state_from_row(dict(row)) if row else None

    async def get_dialogue_state(self, conversation_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM dialogue_states
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        )
        return self._dialogue_state_from_row(dict(row)) if row else None

    async def upsert_dialogue_state(self, state: dict[str, Any]) -> None:
        values = dict(state)
        json_columns = {
            "goal_history": "goal_history_json",
            "known_constraints": "known_constraints_json",
            "soft_preferences": "soft_preferences_json",
            "hard_constraints": "hard_constraints_json",
            "decisions_made": "decisions_made_json",
            "open_questions": "open_questions_json",
            "pending_confirmation": "pending_confirmation_json",
            "candidate_next_actions": "candidate_next_actions_json",
            "referenced_memories": "referenced_memories_json",
            "referenced_artifacts": "referenced_artifacts_json",
        }
        for key, column in json_columns.items():
            default: list[Any] | dict[str, Any]
            default = {} if key == "pending_confirmation" else []
            values[column] = json.dumps(values.pop(key, default), ensure_ascii=False)
        await self._db.execute(
            """
            INSERT INTO dialogue_states (
              dialogue_state_id, conversation_id, member_id, active_topic, user_goal,
              goal_status, goal_history_json, known_constraints_json,
              soft_preferences_json, hard_constraints_json, decisions_made_json,
              open_questions_json, pending_confirmation_json, topic_shift,
              last_user_action, candidate_next_actions_json, referenced_memories_json,
              referenced_artifacts_json, confidence, source_turn_id, trace_id,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
              member_id = excluded.member_id,
              active_topic = excluded.active_topic,
              user_goal = excluded.user_goal,
              goal_status = excluded.goal_status,
              goal_history_json = excluded.goal_history_json,
              known_constraints_json = excluded.known_constraints_json,
              soft_preferences_json = excluded.soft_preferences_json,
              hard_constraints_json = excluded.hard_constraints_json,
              decisions_made_json = excluded.decisions_made_json,
              open_questions_json = excluded.open_questions_json,
              pending_confirmation_json = excluded.pending_confirmation_json,
              topic_shift = excluded.topic_shift,
              last_user_action = excluded.last_user_action,
              candidate_next_actions_json = excluded.candidate_next_actions_json,
              referenced_memories_json = excluded.referenced_memories_json,
              referenced_artifacts_json = excluded.referenced_artifacts_json,
              confidence = excluded.confidence,
              source_turn_id = excluded.source_turn_id,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                values["dialogue_state_id"],
                values["conversation_id"],
                values["member_id"],
                values.get("active_topic"),
                values.get("user_goal"),
                values.get("goal_status") or "active",
                values["goal_history_json"],
                values["known_constraints_json"],
                values["soft_preferences_json"],
                values["hard_constraints_json"],
                values["decisions_made_json"],
                values["open_questions_json"],
                values["pending_confirmation_json"],
                1 if values.get("topic_shift") else 0,
                values.get("last_user_action"),
                values["candidate_next_actions_json"],
                values["referenced_memories_json"],
                values["referenced_artifacts_json"],
                float(values.get("confidence") or 0.0),
                values.get("source_turn_id"),
                values.get("trace_id"),
                values["created_at"],
                values["updated_at"],
            ),
        )

    async def insert_semantic_intent_candidate(self, candidate: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO semantic_intent_candidates (
              semantic_candidate_id, brain_decision_id, turn_id, conversation_id, member_id,
              primary_intent, secondary_intents_json, actionable_intents_json,
              non_actionable_intents_json, risk_intents_json, memory_intents_json,
              tool_intents_json, skill_intents_json, mcp_intents_json,
              conversation_intents_json, conflicts_json, confidence, reason_codes_json,
              model_hint_json, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate["semantic_candidate_id"],
                candidate.get("brain_decision_id"),
                candidate.get("turn_id"),
                candidate.get("conversation_id"),
                candidate["member_id"],
                candidate["primary_intent"],
                json.dumps(candidate.get("secondary_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("actionable_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("non_actionable_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("risk_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("memory_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("tool_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("skill_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("mcp_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("conversation_intents") or [], ensure_ascii=False),
                json.dumps(candidate.get("conflicts") or [], ensure_ascii=False),
                float(candidate.get("confidence") or 0.0),
                json.dumps(candidate.get("reason_codes") or [], ensure_ascii=False),
                json.dumps(candidate.get("model_hint") or {}, ensure_ascii=False),
                candidate.get("trace_id"),
                candidate["created_at"],
            ),
        )

    async def list_semantic_intents_by_turn(self, turn_id: str) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM semantic_intent_candidates
            WHERE turn_id = ?
            ORDER BY created_at ASC
            """,
            (turn_id,),
        )
        return [self._semantic_intent_from_row(dict(row)) for row in rows]

    async def list_semantic_intents_by_decision(
        self,
        brain_decision_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM semantic_intent_candidates
            WHERE brain_decision_id = ?
            ORDER BY created_at ASC
            """,
            (brain_decision_id,),
        )
        return [self._semantic_intent_from_row(dict(row)) for row in rows]

    async def insert_low_confidence_review(self, review: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO low_confidence_decision_reviews (
              review_id, brain_decision_id, turn_id, conversation_id, member_id,
              trigger_reasons_json, rule_decision_json, verifier_suggestion_json,
              clarification_candidates_json, fallback_used, model_assist_enabled,
              semantic_review_id, model_assist_attempted, schema_valid, fallback_reason,
              risk_guard_applied, confidence, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                review["review_id"],
                review.get("brain_decision_id"),
                review.get("turn_id"),
                review.get("conversation_id"),
                review["member_id"],
                json.dumps(review.get("trigger_reasons") or [], ensure_ascii=False),
                json.dumps(review.get("rule_decision") or {}, ensure_ascii=False),
                json.dumps(review.get("verifier_suggestion") or {}, ensure_ascii=False),
                json.dumps(review.get("clarification_candidates") or [], ensure_ascii=False),
                1 if review.get("fallback_used", True) else 0,
                1 if review.get("model_assist_enabled", False) else 0,
                review.get("semantic_review_id"),
                1 if review.get("model_assist_attempted", False) else 0,
                (
                    None
                    if review.get("schema_valid") is None
                    else 1 if review.get("schema_valid") else 0
                ),
                review.get("fallback_reason"),
                1 if review.get("risk_guard_applied", False) else 0,
                float(review.get("confidence") or 0.0),
                review["status"],
                review.get("trace_id"),
                review["created_at"],
            ),
        )

    async def insert_semantic_review_request(self, request: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO semantic_review_requests (
              semantic_review_id, brain_decision_id, turn_id, conversation_id,
              member_id, privacy_level, privacy_policy, trigger_reasons_json,
              redacted_request_json, capability_boundary_summary_json,
              risk_signal_summary_json, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                request["semantic_review_id"],
                request.get("brain_decision_id"),
                request.get("turn_id"),
                request.get("conversation_id"),
                request["member_id"],
                request.get("privacy_level", "medium"),
                request.get("privacy_policy", "local_only"),
                json.dumps(request.get("trigger_reasons") or [], ensure_ascii=False),
                json.dumps(request, ensure_ascii=False),
                json.dumps(
                    request.get("capability_boundary_summary") or {},
                    ensure_ascii=False,
                ),
                json.dumps(request.get("risk_signal_summary") or {}, ensure_ascii=False),
                request.get("status", "completed"),
                request.get("trace_id"),
                request["created_at"],
            ),
        )

    async def insert_semantic_review_suggestion(self, suggestion: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO semantic_review_suggestions (
              suggestion_id, semantic_review_id, source, suggestion_json, confidence,
              schema_valid, rejected_reasons_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                suggestion["suggestion_id"],
                suggestion["semantic_review_id"],
                suggestion["source"],
                json.dumps(suggestion.get("suggestion") or {}, ensure_ascii=False),
                float(suggestion.get("confidence") or 0.0),
                1 if suggestion.get("schema_valid", False) else 0,
                json.dumps(suggestion.get("rejected_reasons") or [], ensure_ascii=False),
                suggestion["created_at"],
            ),
        )

    async def insert_semantic_review_model_call(self, model_call: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO semantic_review_model_calls (
              model_call_id, semantic_review_id, brain_id, provider, model_name,
              adapter_name, status, fallback_used, fallback_reason, latency_ms,
              usage_json, schema_valid, error_code, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                model_call["model_call_id"],
                model_call["semantic_review_id"],
                model_call.get("brain_id"),
                model_call.get("provider"),
                model_call.get("model_name"),
                model_call["adapter_name"],
                model_call["status"],
                1 if model_call.get("fallback_used", True) else 0,
                model_call.get("fallback_reason"),
                int(model_call.get("latency_ms") or 0),
                json.dumps(model_call.get("usage") or {}, ensure_ascii=False),
                1 if model_call.get("schema_valid", False) else 0,
                model_call.get("error_code"),
                model_call.get("trace_id"),
                model_call["created_at"],
            ),
        )

    async def insert_semantic_review_merge_result(self, merge: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO semantic_review_merge_results (
              merge_id, semantic_review_id, brain_decision_id, merged_intent_json,
              merged_mode_json, merged_context_json, merged_clarification_json,
              reason_codes_json, risk_monotonic_guard_applied,
              unsafe_downgrade_count, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                merge["merge_id"],
                merge["semantic_review_id"],
                merge.get("brain_decision_id"),
                json.dumps(merge.get("merged_intent") or {}, ensure_ascii=False),
                json.dumps(merge.get("merged_mode") or {}, ensure_ascii=False),
                json.dumps(merge.get("merged_context") or {}, ensure_ascii=False),
                json.dumps(merge.get("merged_clarification") or {}, ensure_ascii=False),
                json.dumps(merge.get("reason_codes") or [], ensure_ascii=False),
                1 if merge.get("risk_monotonic_guard_applied") else 0,
                int(merge.get("unsafe_downgrade_count") or 0),
                merge["status"],
                merge.get("trace_id"),
                merge["created_at"],
            ),
        )

    async def get_low_confidence_review_by_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM low_confidence_decision_reviews
            WHERE turn_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (turn_id,),
        )
        return self._low_confidence_review_from_row(dict(row)) if row else None

    async def get_low_confidence_review_by_decision(
        self,
        brain_decision_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM low_confidence_decision_reviews
            WHERE brain_decision_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (brain_decision_id,),
        )
        return self._low_confidence_review_from_row(dict(row)) if row else None

    async def get_semantic_review_by_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM semantic_review_requests
            WHERE turn_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (turn_id,),
        )
        return await self._semantic_review_from_request_row(dict(row)) if row else None

    async def get_semantic_review_by_decision(
        self,
        brain_decision_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM semantic_review_requests
            WHERE brain_decision_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (brain_decision_id,),
        )
        return await self._semantic_review_from_request_row(dict(row)) if row else None

    async def list_semantic_review_events_by_turn(self, turn_id: str) -> list[dict[str, Any]]:
        review = await self.get_semantic_review_by_turn(turn_id)
        if review is None:
            return []
        review_id = str(review["semantic_review_id"])
        events: list[dict[str, Any]] = [
            {
                "event_type": "semantic_review.request",
                "semantic_review_id": review_id,
                "payload": review["request"],
                "created_at": review.get("created_at"),
            }
        ]
        for item in await self._semantic_review_suggestions(review_id):
            events.append(
                {
                    "event_type": "semantic_review.suggestion",
                    "semantic_review_id": review_id,
                    "payload": item,
                    "created_at": item.get("created_at"),
                }
            )
        for item in await self._semantic_review_model_calls(review_id):
            events.append(
                {
                    "event_type": "semantic_review.model_call",
                    "semantic_review_id": review_id,
                    "payload": item,
                    "created_at": item.get("created_at"),
                }
            )
        if review.get("merge_result"):
            events.append(
                {
                    "event_type": "semantic_review.merge",
                    "semantic_review_id": review_id,
                    "payload": review["merge_result"],
                    "created_at": review["merge_result"].get("created_at"),
                }
            )
        return sorted(events, key=lambda item: str(item.get("created_at") or ""))

    async def upsert_working_state(self, state: dict[str, Any]) -> None:
        json_columns = {
            "known_constraints": "known_constraints_json",
            "decisions_made": "decisions_made_json",
            "open_questions": "open_questions_json",
            "candidate_actions": "candidate_actions_json",
            "referenced_artifacts": "referenced_artifacts_json",
            "pending_confirmation": "pending_confirmation_json",
        }
        values = dict(state)
        for key, column in json_columns.items():
            default: list[Any] | dict[str, Any]
            default = {} if key == "pending_confirmation" else []
            values[column] = json.dumps(
                values.pop(key, default),
                ensure_ascii=False,
            )
        await self._db.execute(
            """
            INSERT INTO conversation_working_states (
              conversation_id, organization_id, active_topic, user_goal,
              known_constraints_json, decisions_made_json, open_questions_json,
              candidate_actions_json, referenced_artifacts_json, last_response_summary,
              pending_confirmation_json, source_turn_id, confidence, status,
              created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(conversation_id) DO UPDATE SET
              organization_id = excluded.organization_id,
              active_topic = excluded.active_topic,
              user_goal = excluded.user_goal,
              known_constraints_json = excluded.known_constraints_json,
              decisions_made_json = excluded.decisions_made_json,
              open_questions_json = excluded.open_questions_json,
              candidate_actions_json = excluded.candidate_actions_json,
              referenced_artifacts_json = excluded.referenced_artifacts_json,
              last_response_summary = excluded.last_response_summary,
              pending_confirmation_json = excluded.pending_confirmation_json,
              source_turn_id = excluded.source_turn_id,
              confidence = excluded.confidence,
              status = excluded.status,
              updated_at = excluded.updated_at
            """,
            (
                values["conversation_id"],
                values.get("organization_id") or "org_default",
                values.get("active_topic"),
                values.get("user_goal"),
                values["known_constraints_json"],
                values["decisions_made_json"],
                values["open_questions_json"],
                values["candidate_actions_json"],
                values["referenced_artifacts_json"],
                values.get("last_response_summary"),
                values["pending_confirmation_json"],
                values.get("source_turn_id"),
                float(values.get("confidence") or 0.5),
                values.get("status") or "active",
                values["created_at"],
                values["updated_at"],
            ),
        )

    async def insert_clarification_decision(self, decision: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO chat_clarification_decisions (
              clarification_id, turn_id, conversation_id, needs_clarification,
              reason, blocking_level, questions_json, can_answer_partially,
              trace_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(turn_id) DO UPDATE SET
              needs_clarification = excluded.needs_clarification,
              reason = excluded.reason,
              blocking_level = excluded.blocking_level,
              questions_json = excluded.questions_json,
              can_answer_partially = excluded.can_answer_partially,
              trace_id = excluded.trace_id,
              updated_at = excluded.updated_at
            """,
            (
                decision["clarification_id"],
                decision["turn_id"],
                decision["conversation_id"],
                1 if decision.get("needs_clarification") else 0,
                decision["reason"],
                decision["blocking_level"],
                json.dumps(decision.get("questions") or [], ensure_ascii=False),
                1 if decision.get("can_answer_partially") else 0,
                decision.get("trace_id"),
                decision["created_at"],
                decision["updated_at"],
            ),
        )

    async def get_clarification_by_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM chat_clarification_decisions
            WHERE turn_id = ?
            """,
            (turn_id,),
        )
        return self._clarification_from_row(dict(row)) if row else None

    async def insert_brain_decision(self, data: dict[str, Any]) -> None:
        await self._db.execute(
            """
            INSERT INTO brain_decision_logs (
              brain_decision_id, turn_id, conversation_id, member_id, input_summary,
              intent_json, mode_json, context_json, clarification_json,
              capability_snapshot_json, confidence, status, trace_id, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                data["brain_decision_id"],
                data.get("turn_id"),
                data.get("conversation_id"),
                data.get("member_id"),
                data["input_summary"],
                json.dumps(data.get("intent", {}), ensure_ascii=False),
                json.dumps(data.get("mode", {}), ensure_ascii=False),
                json.dumps(data.get("context", {}), ensure_ascii=False),
                json.dumps(data.get("clarification", {}), ensure_ascii=False),
                json.dumps(data.get("capability_snapshot", {}), ensure_ascii=False),
                float(data.get("confidence") or 0.0),
                data["status"],
                data.get("trace_id"),
                data["created_at"],
            ),
        )

    async def get_brain_decision(self, decision_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            "SELECT * FROM brain_decision_logs WHERE brain_decision_id = ?",
            (decision_id,),
        )
        return self._brain_decision_from_row(dict(row)) if row else None

    async def get_brain_decision_by_turn(self, turn_id: str) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM brain_decision_logs
            WHERE turn_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (turn_id,),
        )
        return self._brain_decision_from_row(dict(row)) if row else None

    async def touch_conversation(self, conversation_id: str, updated_at: str) -> None:
        await self._db.execute(
            "UPDATE conversations SET updated_at = ? WHERE conversation_id = ?",
            (updated_at, conversation_id),
        )

    def _conversation_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["participants"] = json.loads(row.pop("participant_json"))
        return row

    def _message_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["content"] = json.loads(row.pop("content_json"))
        return row

    def _turn_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["route"] = json.loads(row.pop("route_json") or "{}")
        row["usage"] = json.loads(row.pop("usage_json") or "{}")
        row["events"] = json.loads(row.pop("events_json") or "[]")
        row["experience"] = json.loads(row.pop("experience_json", "{}") or "{}")
        row["cancel_requested"] = bool(row["cancel_requested"])
        return row

    def _event_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["payload"] = json.loads(row.pop("payload_json") or "{}")
        return row

    def _recovery_attempt_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["diagnostic_payload"] = json.loads(row.pop("diagnostic_payload_json") or "{}")
        return row

    def _working_state_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["known_constraints"] = json.loads(row.pop("known_constraints_json") or "[]")
        row["decisions_made"] = json.loads(row.pop("decisions_made_json") or "[]")
        row["open_questions"] = json.loads(row.pop("open_questions_json") or "[]")
        row["candidate_actions"] = json.loads(row.pop("candidate_actions_json") or "[]")
        row["referenced_artifacts"] = json.loads(row.pop("referenced_artifacts_json") or "[]")
        row["pending_confirmation"] = json.loads(row.pop("pending_confirmation_json") or "{}")
        return row

    def _dialogue_state_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for key in [
            "goal_history",
            "known_constraints",
            "soft_preferences",
            "hard_constraints",
            "decisions_made",
            "open_questions",
            "candidate_next_actions",
            "referenced_memories",
            "referenced_artifacts",
        ]:
            row[key] = json.loads(row.pop(f"{key}_json") or "[]")
        row["pending_confirmation"] = json.loads(row.pop("pending_confirmation_json") or "{}")
        row["topic_shift"] = bool(row["topic_shift"])
        return row

    def _semantic_intent_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        for key in [
            "secondary_intents",
            "actionable_intents",
            "non_actionable_intents",
            "risk_intents",
            "memory_intents",
            "tool_intents",
            "skill_intents",
            "mcp_intents",
            "conversation_intents",
            "conflicts",
            "reason_codes",
        ]:
            row[key] = json.loads(row.pop(f"{key}_json") or "[]")
        row["model_hint"] = json.loads(row.pop("model_hint_json") or "{}")
        return row

    def _low_confidence_review_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["trigger_reasons"] = json.loads(row.pop("trigger_reasons_json") or "[]")
        row["rule_decision"] = json.loads(row.pop("rule_decision_json") or "{}")
        row["verifier_suggestion"] = json.loads(row.pop("verifier_suggestion_json") or "{}")
        row["clarification_candidates"] = json.loads(
            row.pop("clarification_candidates_json") or "[]"
        )
        row["fallback_used"] = bool(row["fallback_used"])
        row["model_assist_enabled"] = bool(row["model_assist_enabled"])
        row["model_assist_attempted"] = bool(row.get("model_assist_attempted", 0))
        if row.get("schema_valid") is not None:
            row["schema_valid"] = bool(row["schema_valid"])
        row["risk_guard_applied"] = bool(row.get("risk_guard_applied", 0))
        return row

    async def _semantic_review_from_request_row(self, row: dict[str, Any]) -> dict[str, Any]:
        request = self._semantic_review_request_from_row(row)
        review_id = str(request["semantic_review_id"])
        suggestions = await self._semantic_review_suggestions(review_id)
        model_calls = await self._semantic_review_model_calls(review_id)
        merge = await self._semantic_review_merge_result(review_id)
        latest_suggestion = suggestions[-1] if suggestions else None
        latest_model_call = model_calls[-1] if model_calls else {}
        return {
            "semantic_review_id": review_id,
            "brain_decision_id": request.get("brain_decision_id"),
            "turn_id": request.get("turn_id"),
            "conversation_id": request.get("conversation_id"),
            "member_id": request["member_id"],
            "request": request,
            "suggestion": latest_suggestion.get("suggestion") if latest_suggestion else None,
            "model_call": latest_model_call,
            "merge_result": merge,
            "fallback_used": bool(latest_model_call.get("fallback_used", True)),
            "fallback_reason": latest_model_call.get("fallback_reason"),
            "model_assist_attempted": latest_model_call.get("status") not in {None, "skipped"},
            "schema_valid": latest_model_call.get("schema_valid"),
            "risk_guard_applied": bool(
                (merge or {}).get("risk_monotonic_guard_applied", False)
            ),
            "unsafe_downgrade_count": int((merge or {}).get("unsafe_downgrade_count") or 0),
            "status": request.get("status", "completed"),
            "trace_id": request.get("trace_id"),
            "created_at": request.get("created_at"),
        }

    def _semantic_review_request_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        redacted_request = json.loads(row.pop("redacted_request_json") or "{}")
        redacted_request["semantic_review_id"] = row["semantic_review_id"]
        redacted_request["brain_decision_id"] = row.get("brain_decision_id")
        redacted_request["turn_id"] = row.get("turn_id")
        redacted_request["conversation_id"] = row.get("conversation_id")
        redacted_request["member_id"] = row["member_id"]
        redacted_request["privacy_level"] = row["privacy_level"]
        redacted_request["privacy_policy"] = row["privacy_policy"]
        redacted_request["trigger_reasons"] = json.loads(
            row.pop("trigger_reasons_json") or "[]"
        )
        redacted_request["capability_boundary_summary"] = json.loads(
            row.pop("capability_boundary_summary_json") or "{}"
        )
        redacted_request["risk_signal_summary"] = json.loads(
            row.pop("risk_signal_summary_json") or "{}"
        )
        redacted_request["status"] = row["status"]
        redacted_request["trace_id"] = row.get("trace_id")
        redacted_request["created_at"] = row["created_at"]
        return redacted_request

    async def _semantic_review_suggestions(
        self,
        semantic_review_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM semantic_review_suggestions
            WHERE semantic_review_id = ?
            ORDER BY created_at ASC
            """,
            (semantic_review_id,),
        )
        return [self._semantic_review_suggestion_from_row(dict(row)) for row in rows]

    async def _semantic_review_model_calls(
        self,
        semantic_review_id: str,
    ) -> list[dict[str, Any]]:
        rows = await self._db.fetch_all(
            """
            SELECT *
            FROM semantic_review_model_calls
            WHERE semantic_review_id = ?
            ORDER BY created_at ASC
            """,
            (semantic_review_id,),
        )
        return [self._semantic_review_model_call_from_row(dict(row)) for row in rows]

    async def _semantic_review_merge_result(
        self,
        semantic_review_id: str,
    ) -> dict[str, Any] | None:
        row = await self._db.fetch_one(
            """
            SELECT *
            FROM semantic_review_merge_results
            WHERE semantic_review_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (semantic_review_id,),
        )
        return self._semantic_review_merge_from_row(dict(row)) if row else None

    def _semantic_review_suggestion_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["suggestion"] = json.loads(row.pop("suggestion_json") or "{}")
        row["schema_valid"] = bool(row["schema_valid"])
        row["rejected_reasons"] = json.loads(row.pop("rejected_reasons_json") or "[]")
        return row

    def _semantic_review_model_call_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["fallback_used"] = bool(row["fallback_used"])
        row["schema_valid"] = bool(row["schema_valid"])
        row["usage"] = json.loads(row.pop("usage_json") or "{}")
        return row

    def _semantic_review_merge_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["merged_intent"] = json.loads(row.pop("merged_intent_json") or "{}")
        row["merged_mode"] = json.loads(row.pop("merged_mode_json") or "{}")
        row["merged_context"] = json.loads(row.pop("merged_context_json") or "{}")
        row["merged_clarification"] = json.loads(
            row.pop("merged_clarification_json") or "{}"
        )
        row["reason_codes"] = json.loads(row.pop("reason_codes_json") or "[]")
        row["risk_monotonic_guard_applied"] = bool(
            row["risk_monotonic_guard_applied"]
        )
        return row

    def _clarification_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["needs_clarification"] = bool(row["needs_clarification"])
        row["questions"] = json.loads(row.pop("questions_json") or "[]")
        row["can_answer_partially"] = bool(row["can_answer_partially"])
        return row

    def _brain_decision_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        row["intent"] = json.loads(row.pop("intent_json") or "{}")
        row["mode"] = json.loads(row.pop("mode_json") or "{}")
        row["context"] = json.loads(row.pop("context_json") or "{}")
        row["clarification"] = json.loads(row.pop("clarification_json") or "{}")
        row["capability_snapshot"] = json.loads(
            row.pop("capability_snapshot_json") or "{}"
        )
        return row
