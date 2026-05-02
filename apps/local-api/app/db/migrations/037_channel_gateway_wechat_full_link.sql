CREATE TABLE IF NOT EXISTS channel_peer_sessions (
  channel_peer_session_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_account_id TEXT NOT NULL,
  channel_peer_id TEXT,
  channel_id TEXT,
  provider TEXT NOT NULL,
  peer_ref_redacted TEXT NOT NULL,
  peer_type TEXT NOT NULL,
  conversation_id TEXT,
  session_id TEXT NOT NULL,
  member_id TEXT NOT NULL,
  peer_state_ref TEXT,
  pairing_status TEXT NOT NULL,
  allow_inbound INTEGER NOT NULL DEFAULT 0,
  allow_outbound INTEGER NOT NULL DEFAULT 0,
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  last_inbound_at TEXT,
  last_outbound_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_peer_id) REFERENCES channel_peers(channel_peer_id),
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id),
  FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_peer_sessions_account_peer
ON channel_peer_sessions(channel_account_id, peer_ref_redacted);

CREATE INDEX IF NOT EXISTS idx_channel_peer_sessions_conversation
ON channel_peer_sessions(conversation_id);

CREATE TABLE IF NOT EXISTS channel_pairing_requests (
  pairing_request_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_account_id TEXT NOT NULL,
  channel_peer_id TEXT,
  provider TEXT NOT NULL,
  peer_ref_redacted TEXT NOT NULL,
  peer_type TEXT NOT NULL,
  display_name_redacted TEXT,
  peer_state_ref TEXT,
  status TEXT NOT NULL,
  requested_member_id TEXT NOT NULL,
  decision_by_member_id TEXT,
  decision_reason TEXT,
  expires_at TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  decided_at TEXT,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_peer_id) REFERENCES channel_peers(channel_peer_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_pairing_requests_status
ON channel_pairing_requests(provider, status, created_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_pairing_requests_pending_peer
ON channel_pairing_requests(channel_account_id, peer_ref_redacted, status)
WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS channel_attachments (
  channel_attachment_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_event_id TEXT,
  channel_account_id TEXT NOT NULL,
  channel_peer_session_id TEXT,
  provider TEXT NOT NULL,
  provider_attachment_ref_redacted TEXT,
  attachment_type TEXT NOT NULL,
  display_name_redacted TEXT,
  content_type TEXT,
  size_bytes INTEGER,
  artifact_id TEXT,
  blob_ref TEXT,
  media_id TEXT,
  status TEXT NOT NULL,
  failure_reason TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_event_id) REFERENCES channel_events(channel_event_id),
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_peer_session_id) REFERENCES channel_peer_sessions(channel_peer_session_id),
  FOREIGN KEY(artifact_id) REFERENCES task_artifacts(artifact_id),
  FOREIGN KEY(media_id) REFERENCES media_assets(media_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_attachments_event
ON channel_attachments(channel_event_id);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_attachments_event_provider_ref
ON channel_attachments(channel_event_id, provider_attachment_ref_redacted)
WHERE provider_attachment_ref_redacted IS NOT NULL;

CREATE TABLE IF NOT EXISTS channel_delivery_bindings (
  channel_delivery_binding_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_account_id TEXT NOT NULL,
  channel_peer_session_id TEXT,
  channel_event_id TEXT,
  turn_id TEXT,
  message_id TEXT,
  notification_id TEXT,
  provider TEXT NOT NULL,
  provider_message_id_redacted TEXT,
  status TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  failure_reason TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  sent_at TEXT,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_peer_session_id) REFERENCES channel_peer_sessions(channel_peer_session_id),
  FOREIGN KEY(channel_event_id) REFERENCES channel_events(channel_event_id),
  FOREIGN KEY(turn_id) REFERENCES chat_turns(turn_id),
  FOREIGN KEY(message_id) REFERENCES messages(message_id),
  FOREIGN KEY(notification_id) REFERENCES notification_messages(notification_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_delivery_turn_once
ON channel_delivery_bindings(turn_id, channel_peer_session_id)
WHERE turn_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_channel_delivery_pending
ON channel_delivery_bindings(provider, status, created_at);

CREATE TABLE IF NOT EXISTS channel_event_offsets (
  offset_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_account_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  provider_event_id_redacted TEXT NOT NULL,
  channel_event_id TEXT,
  status TEXT NOT NULL,
  received_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_event_id) REFERENCES channel_events(channel_event_id)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_channel_event_offsets_unique
ON channel_event_offsets(channel_account_id, provider_event_id_redacted);
