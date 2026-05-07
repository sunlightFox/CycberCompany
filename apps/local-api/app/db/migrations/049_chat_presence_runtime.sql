CREATE TABLE IF NOT EXISTS conversation_user_profiles (
  profile_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  profile_type TEXT NOT NULL,
  profile_data_json TEXT NOT NULL DEFAULT '{}',
  source_turn_id TEXT,
  trace_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  expires_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(member_id) REFERENCES members(member_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_user_profiles_conversation
ON conversation_user_profiles(conversation_id, status, updated_at);

CREATE TABLE IF NOT EXISTS conversation_continuity_snapshots (
  snapshot_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  source_turn_id TEXT,
  summary_text TEXT NOT NULL,
  user_state_hint TEXT,
  assistant_commitments_json TEXT NOT NULL DEFAULT '[]',
  followup_candidates_json TEXT NOT NULL DEFAULT '[]',
  topic_anchor TEXT,
  expiry_policy_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_conversation_continuity_snapshots_conversation
ON conversation_continuity_snapshots(conversation_id, status, updated_at);

CREATE TABLE IF NOT EXISTS assistant_commitments (
  commitment_id TEXT PRIMARY KEY,
  conversation_id TEXT NOT NULL,
  source_turn_id TEXT,
  commitment_text TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id),
  FOREIGN KEY(source_turn_id) REFERENCES chat_turns(turn_id)
);

CREATE INDEX IF NOT EXISTS idx_assistant_commitments_conversation
ON assistant_commitments(conversation_id, status, updated_at);

CREATE TABLE IF NOT EXISTS turn_presence_states (
  presence_state_id TEXT PRIMARY KEY,
  turn_id TEXT NOT NULL UNIQUE,
  conversation_id TEXT NOT NULL,
  understanding_json TEXT NOT NULL DEFAULT '{}',
  presence_state_json TEXT NOT NULL DEFAULT '{}',
  session_context_json TEXT NOT NULL DEFAULT '{}',
  response_policy_json TEXT NOT NULL DEFAULT '{}',
  action_dialogue_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE INDEX IF NOT EXISTS idx_turn_presence_states_conversation
ON turn_presence_states(conversation_id, updated_at);
