CREATE TABLE IF NOT EXISTS brain_decision_logs (
  brain_decision_id TEXT PRIMARY KEY,
  turn_id TEXT,
  conversation_id TEXT,
  member_id TEXT,
  input_summary TEXT NOT NULL,
  intent_json TEXT NOT NULL DEFAULT '{}',
  mode_json TEXT NOT NULL DEFAULT '{}',
  context_json TEXT NOT NULL DEFAULT '{}',
  clarification_json TEXT NOT NULL DEFAULT '{}',
  capability_snapshot_json TEXT NOT NULL DEFAULT '{}',
  confidence REAL NOT NULL DEFAULT 0.0,
  status TEXT NOT NULL,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_brain_decision_logs_turn
  ON brain_decision_logs(turn_id);

CREATE INDEX IF NOT EXISTS idx_brain_decision_logs_conversation_time
  ON brain_decision_logs(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_brain_decision_logs_status
  ON brain_decision_logs(status, confidence);

ALTER TABLE chat_turns ADD COLUMN brain_decision_id TEXT;
