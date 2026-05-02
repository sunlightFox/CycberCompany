CREATE TABLE IF NOT EXISTS channel_bind_sessions (
  bind_session_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  requested_by_member_id TEXT NOT NULL,
  display_name_hint TEXT,
  status TEXT NOT NULL,
  qr_format TEXT,
  qr_payload_ref TEXT,
  qr_artifact_id TEXT,
  expires_at TEXT NOT NULL,
  confirmed_at TEXT,
  bound_asset_id TEXT,
  bound_channel_id TEXT,
  provider_account_ref_redacted TEXT,
  provider_state_ref TEXT,
  risk_level TEXT NOT NULL,
  policy_snapshot_json TEXT NOT NULL DEFAULT '{}',
  provider_status_json TEXT NOT NULL DEFAULT '{}',
  failure_reason TEXT,
  trace_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(bound_asset_id) REFERENCES assets(asset_id),
  FOREIGN KEY(bound_channel_id) REFERENCES notification_channels(channel_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_bind_sessions_provider_status
ON channel_bind_sessions(provider, status);

CREATE INDEX IF NOT EXISTS idx_channel_bind_sessions_requested_by
ON channel_bind_sessions(requested_by_member_id, created_at);

CREATE TABLE IF NOT EXISTS channel_accounts (
  channel_account_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  asset_id TEXT NOT NULL,
  channel_id TEXT,
  bind_session_id TEXT,
  provider TEXT NOT NULL,
  account_ref_redacted TEXT NOT NULL,
  display_name TEXT NOT NULL,
  status TEXT NOT NULL,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  provider_state_ref TEXT NOT NULL,
  policy_json TEXT NOT NULL DEFAULT '{}',
  last_seen_at TEXT,
  last_verified_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(asset_id) REFERENCES assets(asset_id),
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id),
  FOREIGN KEY(bind_session_id) REFERENCES channel_bind_sessions(bind_session_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_accounts_asset
ON channel_accounts(asset_id);

CREATE INDEX IF NOT EXISTS idx_channel_accounts_channel
ON channel_accounts(channel_id);

CREATE TABLE IF NOT EXISTS channel_peers (
  channel_peer_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  channel_account_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  peer_ref_redacted TEXT NOT NULL,
  peer_type TEXT NOT NULL,
  display_name_redacted TEXT,
  pairing_status TEXT NOT NULL,
  allow_inbound INTEGER NOT NULL DEFAULT 0,
  allow_outbound INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_peers_account_ref
ON channel_peers(channel_account_id, peer_ref_redacted);

CREATE TABLE IF NOT EXISTS channel_events (
  channel_event_id TEXT PRIMARY KEY,
  organization_id TEXT NOT NULL,
  provider TEXT NOT NULL,
  channel_account_id TEXT,
  channel_id TEXT,
  event_type TEXT NOT NULL,
  provider_event_id_redacted TEXT,
  payload_redacted_json TEXT NOT NULL DEFAULT '{}',
  normalized_event_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL,
  trace_id TEXT,
  received_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY(channel_account_id) REFERENCES channel_accounts(channel_account_id),
  FOREIGN KEY(channel_id) REFERENCES notification_channels(channel_id)
);

CREATE INDEX IF NOT EXISTS idx_channel_events_provider_received
ON channel_events(provider, received_at);
