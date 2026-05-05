CREATE TABLE IF NOT EXISTS chat_message_envelopes (
  envelope_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  turn_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  user_message_id TEXT,
  dedupe_key TEXT NOT NULL,
  raw_payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  content_parts_json TEXT NOT NULL DEFAULT '[]',
  context_refs_json TEXT NOT NULL DEFAULT '[]',
  model_safe_text TEXT NOT NULL,
  normalized_summary_json TEXT NOT NULL DEFAULT '{}',
  ingress_metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'normalized',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(user_message_id) REFERENCES messages(message_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_message_envelopes_turn
  ON chat_message_envelopes(turn_id);

CREATE INDEX IF NOT EXISTS idx_chat_message_envelopes_dedupe
  ON chat_message_envelopes(dedupe_key, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_message_envelopes_session
  ON chat_message_envelopes(session_id, created_at);

CREATE TABLE IF NOT EXISTS chat_turn_queue (
  queue_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  turn_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  status TEXT NOT NULL,
  queue_policy TEXT NOT NULL DEFAULT 'immediate',
  position INTEGER NOT NULL DEFAULT 0,
  locked_by TEXT,
  locked_until TEXT,
  dedupe_key TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_turn_queue_turn
  ON chat_turn_queue(turn_id);

CREATE INDEX IF NOT EXISTS idx_chat_turn_queue_session_status
  ON chat_turn_queue(session_id, status, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_turn_queue_dedupe
  ON chat_turn_queue(dedupe_key, created_at);

CREATE TABLE IF NOT EXISTS chat_context_compactions (
  compaction_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  turn_id TEXT NOT NULL,
  conversation_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL,
  token_estimate_before INTEGER NOT NULL DEFAULT 0,
  token_estimate_after INTEGER NOT NULL DEFAULT 0,
  summary_redacted TEXT,
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_context_compactions_turn
  ON chat_context_compactions(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_context_compactions_conversation
  ON chat_context_compactions(conversation_id, created_at);

ALTER TABLE chat_turn_recovery_attempts ADD COLUMN recovery_stage TEXT NOT NULL DEFAULT 'task';
ALTER TABLE chat_turn_recovery_attempts ADD COLUMN error_signature TEXT;
ALTER TABLE chat_turn_recovery_attempts ADD COLUMN action_result_json TEXT NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS idx_chat_turn_recovery_attempts_stage
  ON chat_turn_recovery_attempts(turn_id, recovery_stage, attempt_index);

CREATE INDEX IF NOT EXISTS idx_chat_turn_recovery_attempts_signature
  ON chat_turn_recovery_attempts(error_signature, started_at);
