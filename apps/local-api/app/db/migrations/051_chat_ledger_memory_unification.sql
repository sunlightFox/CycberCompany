CREATE TABLE IF NOT EXISTS chat_turn_ledgers (
  turn_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  session_id TEXT,
  member_id TEXT NOT NULL,
  trace_id TEXT,
  status TEXT NOT NULL,
  route_type TEXT,
  mode TEXT,
  started_at TEXT,
  ended_at TEXT,
  retry_of_turn_id TEXT,
  recovered_from_turn_id TEXT,
  channel TEXT,
  source_message_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_turn_ledgers_conversation
  ON chat_turn_ledgers(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_turn_ledgers_trace
  ON chat_turn_ledgers(trace_id);

CREATE INDEX IF NOT EXISTS idx_chat_turn_ledgers_status
  ON chat_turn_ledgers(status, updated_at);

CREATE TABLE IF NOT EXISTS chat_run_ledgers (
  run_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  trace_id TEXT,
  stage TEXT NOT NULL,
  event_type TEXT NOT NULL,
  status TEXT NOT NULL,
  ref_id TEXT,
  ref_type TEXT,
  summary TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  trace_span_id TEXT,
  created_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turn_ledgers(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_run_ledgers_turn
  ON chat_run_ledgers(turn_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_run_ledgers_trace
  ON chat_run_ledgers(trace_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_run_ledgers_ref
  ON chat_run_ledgers(ref_type, ref_id, created_at);
