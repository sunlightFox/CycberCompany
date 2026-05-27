UPDATE brains
SET
  display_name = 'Codex Default Brain',
  provider = 'openai_compatible',
  endpoint = 'http://127.0.0.1:8317/v1',
  model_name = 'gpt-5.5',
  api_key_ref = 'codex-auth://OPENAI_API_KEY',
  is_local = 0,
  context_window = 1000000,
  supports_tools = 1,
  supports_vision = 1,
  supports_audio = 0,
  cost_policy_json = '{"mode":"cloud","profile":"codex_current_default"}',
  privacy_policy_json = '{"allow_cloud":true,"provider_display_name":"OpenAI","adapter_family":"openai_compatible","codex_profile":"current_codex","codex_wire_api":"responses","codex_provider":"custom","requires_openai_auth":true,"reasoning_effort":"high","text_verbosity":"medium","api_key_ref_scheme":"codex-auth"}',
  status = 'configured',
  default_temperature = 0.2,
  default_top_p = 1.0,
  default_max_output_tokens = 4096,
  timeout_seconds = 300,
  retry_count = 1,
  allow_fallback = 1,
  allow_cloud = 1,
  streaming_supported = 1,
  protocol_family = 'responses',
  request_format = 'responses',
  response_format = 'openai_responses',
  supports_stream = 1,
  verify_capabilities_json = '{}',
  last_error_code = NULL,
  last_error_message = NULL,
  latency_ms = NULL,
  updated_at = CURRENT_TIMESTAMP
WHERE brain_id = 'brain_not_configured'
  AND (
    provider IN ('local_placeholder', 'openai', 'openai_compatible', 'custom_openai_compatible')
    OR display_name IN (
      'Codex Default Brain',
      'Codex 默认大脑',
      'EdgeFn Default Brain'
    )
  );
