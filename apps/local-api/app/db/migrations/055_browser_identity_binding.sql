ALTER TABLE browser_profiles ADD COLUMN execution_backend TEXT NOT NULL DEFAULT 'playwright_ephemeral';
ALTER TABLE browser_profiles ADD COLUMN cdp_endpoint TEXT;
ALTER TABLE browser_profiles ADD COLUMN browser_family TEXT;
ALTER TABLE browser_profiles ADD COLUMN browser_profile_name TEXT;
ALTER TABLE browser_profiles ADD COLUMN identity_binding_status TEXT NOT NULL DEFAULT 'unbound';
ALTER TABLE browser_profiles ADD COLUMN login_capture_mode TEXT NOT NULL DEFAULT 'manual_handoff';

ALTER TABLE browser_sessions ADD COLUMN execution_backend TEXT NOT NULL DEFAULT 'playwright_ephemeral';
ALTER TABLE browser_sessions ADD COLUMN identity_source TEXT;
ALTER TABLE browser_sessions ADD COLUMN cdp_endpoint TEXT;
ALTER TABLE browser_sessions ADD COLUMN browser_family TEXT;
ALTER TABLE browser_sessions ADD COLUMN browser_profile_name TEXT;
ALTER TABLE browser_sessions ADD COLUMN identity_binding_status TEXT NOT NULL DEFAULT 'unbound';
ALTER TABLE browser_sessions ADD COLUMN login_capture_mode TEXT NOT NULL DEFAULT 'manual_handoff';
