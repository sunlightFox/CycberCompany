ALTER TABLE brains ADD COLUMN default_temperature REAL NOT NULL DEFAULT 0.3;
ALTER TABLE brains ADD COLUMN default_top_p REAL NOT NULL DEFAULT 0.9;
ALTER TABLE brains ADD COLUMN default_max_output_tokens INTEGER NOT NULL DEFAULT 1024;
ALTER TABLE brains ADD COLUMN timeout_seconds INTEGER NOT NULL DEFAULT 180;
ALTER TABLE brains ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 1;
ALTER TABLE brains ADD COLUMN allow_fallback INTEGER NOT NULL DEFAULT 1;
ALTER TABLE brains ADD COLUMN allow_cloud INTEGER NOT NULL DEFAULT 0;
ALTER TABLE brains ADD COLUMN streaming_supported INTEGER NOT NULL DEFAULT 1;
ALTER TABLE brains ADD COLUMN last_verified_at TEXT;
ALTER TABLE brains ADD COLUMN last_error_code TEXT;
ALTER TABLE brains ADD COLUMN last_error_message TEXT;
ALTER TABLE brains ADD COLUMN latency_ms INTEGER;

CREATE TABLE IF NOT EXISTS chat_turns (
  turn_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  user_message_id TEXT,
  assistant_message_id TEXT,
  trace_id TEXT NOT NULL,
  status TEXT NOT NULL,
  intent TEXT,
  mode TEXT,
  privacy_level TEXT,
  route_json TEXT NOT NULL,
  usage_json TEXT NOT NULL,
  events_json TEXT NOT NULL,
  error_code TEXT,
  error_message TEXT,
  retry_of_turn_id TEXT,
  cancel_requested INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  ended_at TEXT,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(user_message_id) REFERENCES messages(message_id),
  FOREIGN KEY(assistant_message_id) REFERENCES messages(message_id),
  FOREIGN KEY(retry_of_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_chat_turns_conversation
  ON chat_turns(conversation_id, created_at);

CREATE INDEX IF NOT EXISTS idx_chat_turns_trace
  ON chat_turns(trace_id);

CREATE TABLE IF NOT EXISTS conversation_summaries (
  summary_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  source_turn_id TEXT,
  token_estimate INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_summaries_conversation
  ON conversation_summaries(conversation_id);

CREATE TABLE IF NOT EXISTS secret_refs (
  secret_ref TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  storage_uri TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  rotated_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_secret_refs_kind
  ON secret_refs(kind);
