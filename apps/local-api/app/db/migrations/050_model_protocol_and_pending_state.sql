ALTER TABLE brains ADD COLUMN protocol_family TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE brains ADD COLUMN request_format TEXT NOT NULL DEFAULT 'chat_completions';
ALTER TABLE brains ADD COLUMN response_format TEXT NOT NULL DEFAULT 'auto';
ALTER TABLE brains ADD COLUMN supports_stream INTEGER NOT NULL DEFAULT 1;
ALTER TABLE brains ADD COLUMN verify_capabilities_json TEXT NOT NULL DEFAULT '{}';

ALTER TABLE conversation_working_states ADD COLUMN session_id TEXT;
ALTER TABLE conversation_working_states ADD COLUMN source_message_fingerprint TEXT;
ALTER TABLE conversation_working_states ADD COLUMN pending_clarification_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE conversation_working_states ADD COLUMN pending_approval_action_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE conversation_working_states ADD COLUMN pending_execution_resume_json TEXT NOT NULL DEFAULT '{}';
