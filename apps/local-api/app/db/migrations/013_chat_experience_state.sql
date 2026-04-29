CREATE TABLE IF NOT EXISTS conversation_working_states (
  conversation_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL DEFAULT 'org_default',
  active_topic TEXT,
  user_goal TEXT,
  known_constraints_json TEXT NOT NULL DEFAULT '[]',
  decisions_made_json TEXT NOT NULL DEFAULT '[]',
  open_questions_json TEXT NOT NULL DEFAULT '[]',
  candidate_actions_json TEXT NOT NULL DEFAULT '[]',
  referenced_artifacts_json TEXT NOT NULL DEFAULT '[]',
  last_response_summary TEXT,
  pending_confirmation_json TEXT NOT NULL DEFAULT '{}',
  source_turn_id TEXT,
  confidence REAL NOT NULL DEFAULT 0.5,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_working_states_org_status
  ON conversation_working_states(organization_id, status, updated_at);

CREATE TABLE IF NOT EXISTS chat_clarification_decisions (
  clarification_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL UNIQUE,
  conversation_id TEXT NOT NULL,
  needs_clarification INTEGER NOT NULL,
  reason TEXT NOT NULL,
  blocking_level TEXT NOT NULL,
  questions_json TEXT NOT NULL DEFAULT '[]',
  can_answer_partially INTEGER NOT NULL DEFAULT 0,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_clarification_conversation
  ON chat_clarification_decisions(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_clarification_needs
  ON chat_clarification_decisions(needs_clarification, blocking_level);

ALTER TABLE chat_turns ADD COLUMN experience_json TEXT NOT NULL DEFAULT '{}';
