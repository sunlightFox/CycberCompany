CREATE TABLE IF NOT EXISTS chat_events (
  event_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  trace_id TEXT,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_events_turn_sequence
  ON chat_events(turn_id, sequence);

CREATE INDEX IF NOT EXISTS idx_chat_events_turn_created
  ON chat_events(turn_id, created_at);

ALTER TABLE trace_spans ADD COLUMN latency_ms INTEGER;
ALTER TABLE trace_spans ADD COLUMN error_code TEXT;
