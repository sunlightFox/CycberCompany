UPDATE brains
SET
  display_name = 'EdgeFn Default Brain',
  provider = 'openai_compatible',
  endpoint = 'https://api.edgefn.net/v1',
  model_name = 'MiniMax-M2.5',
  api_key_ref = 'env://EDGEFN_API_KEY',
  is_local = 0,
  context_window = 1000000,
  supports_tools = 1,
  supports_vision = 1,
  supports_audio = 0,
  cost_policy_json = '{"mode":"cloud","profile":"edgefn_minimax_m25"}',
  privacy_policy_json = '{"allow_cloud":true,"provider_display_name":"EdgeFn","adapter_family":"openai_compatible","codex_profile":"edgefn_minimax_m25","codex_wire_api":"chat_completions","codex_provider":"edgefn","requires_openai_auth":false,"reasoning_effort":"high","text_verbosity":"medium","api_key_ref_scheme":"env"}',
  status = 'configured',
  default_temperature = 0.2,
  default_top_p = 1.0,
  default_max_output_tokens = 4096,
  timeout_seconds = 300,
  retry_count = 1,
  allow_fallback = 1,
  allow_cloud = 1,
  streaming_supported = 1,
  protocol_family = 'chat_completions',
  request_format = 'chat_completions',
  response_format = 'auto',
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
