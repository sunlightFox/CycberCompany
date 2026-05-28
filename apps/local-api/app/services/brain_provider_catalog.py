from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.schemas.brain import BrainProviderModelPreset, BrainProviderPreset


@dataclass(frozen=True)
class ProviderPreset:
    provider: str
    display_name: str
    category: str
    adapter_family: str = "openai_compatible"
    implementation_status: str = "compatible"
    endpoint: str | None = None
    api_key_env_vars: tuple[str, ...] = ()
    default_model: str | None = None
    models: tuple[dict[str, Any], ...] = ()
    is_local_default: bool = False
    allow_cloud_default: bool = True
    protocol_family: str = "auto"
    request_format: str = "chat_completions"
    response_format: str = "auto"
    supports_stream: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    supports_audio: bool = False
    icon_uri: str | None = None
    docs_ref: str | None = None
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_schema(self) -> BrainProviderPreset:
        return BrainProviderPreset(
            provider=self.provider,
            display_name=self.display_name,
            category=self.category,
            adapter_family=self.adapter_family,
            implementation_status=self.implementation_status,
            endpoint=self.endpoint,
            api_key_env_vars=list(self.api_key_env_vars),
            default_model=self.default_model,
            model_presets=[
                BrainProviderModelPreset(
                    model_name=str(model["model_name"]),
                    display_name=str(model.get("display_name") or model["model_name"]),
                    context_window=model.get("context_window"),
                    supports_tools=bool(model.get("supports_tools", self.supports_tools)),
                    supports_vision=bool(model.get("supports_vision", self.supports_vision)),
                    supports_audio=bool(model.get("supports_audio", self.supports_audio)),
                    reasoning=bool(model.get("reasoning", False)),
                )
                for model in self.models
            ],
            is_local_default=self.is_local_default,
            allow_cloud_default=self.allow_cloud_default,
            protocol_family=self.protocol_family,
            request_format=self.request_format,
            response_format=self.response_format,
            supports_stream=self.supports_stream,
            supports_tools=self.supports_tools,
            supports_vision=self.supports_vision,
            supports_audio=self.supports_audio,
            icon_uri=self.icon_uri,
            docs_ref=self.docs_ref,
            notes=list(self.notes),
        )

    def apply_defaults(
        self,
        data: dict[str, Any],
        *,
        explicit_fields: set[str] | None = None,
    ) -> dict[str, Any]:
        merged = dict(data)
        explicit = explicit_fields or set()
        if not merged.get("endpoint") and self.endpoint:
            merged["endpoint"] = self.endpoint
        if not merged.get("model_name") and self.default_model:
            merged["model_name"] = self.default_model
        for key in (
            "protocol_family",
            "request_format",
            "response_format",
        ):
            if not merged.get(key) or merged.get(key) == "auto":
                merged[key] = getattr(self, key)
        if "supports_stream" not in merged:
            merged["supports_stream"] = self.supports_stream
        if "streaming_supported" not in merged:
            merged["streaming_supported"] = self.supports_stream
        if "supports_tools" not in explicit and not merged.get("supports_tools"):
            merged["supports_tools"] = self.supports_tools
        if "supports_vision" not in explicit and not merged.get("supports_vision"):
            merged["supports_vision"] = self.supports_vision
        if "supports_audio" not in explicit and not merged.get("supports_audio"):
            merged["supports_audio"] = self.supports_audio
        if "allow_cloud" not in explicit:
            merged["allow_cloud"] = self.allow_cloud_default
        if "is_local" not in explicit:
            merged["is_local"] = self.is_local_default
        if "context_window" not in explicit:
            context_window = _default_context_window(self)
            if context_window is not None:
                merged["context_window"] = context_window
        privacy_policy = dict(merged.get("privacy_policy") or {})
        privacy_policy.setdefault("provider_display_name", self.display_name)
        privacy_policy.setdefault("adapter_family", self.adapter_family)
        privacy_policy.setdefault("implementation_status", self.implementation_status)
        if self.icon_uri:
            privacy_policy.setdefault("provider_icon_uri", self.icon_uri)
        if self.docs_ref:
            privacy_policy.setdefault("provider_docs_ref", self.docs_ref)
        merged["privacy_policy"] = privacy_policy
        return merged


def list_provider_presets() -> list[BrainProviderPreset]:
    return [preset.to_schema() for preset in _PRESETS]


def get_provider_preset(provider: str | None) -> ProviderPreset | None:
    if not provider:
        return None
    return _PRESET_BY_ID.get(_normalize_provider(provider))


def apply_provider_defaults(
    data: dict[str, Any],
    *,
    explicit_fields: set[str] | None = None,
) -> dict[str, Any]:
    preset = get_provider_preset(str(data.get("provider") or ""))
    return preset.apply_defaults(data, explicit_fields=explicit_fields) if preset else data


def _model(
    model_name: str,
    display_name: str | None = None,
    *,
    context_window: int | None = None,
    supports_vision: bool = False,
    supports_audio: bool = False,
    supports_tools: bool = True,
    reasoning: bool = False,
) -> dict[str, Any]:
    return {
        "model_name": model_name,
        "display_name": display_name or model_name,
        "context_window": context_window,
        "supports_vision": supports_vision,
        "supports_audio": supports_audio,
        "supports_tools": supports_tools,
        "reasoning": reasoning,
    }


def _default_context_window(preset: ProviderPreset) -> int | None:
    if not preset.default_model:
        return None
    for model in preset.models:
        if model.get("model_name") == preset.default_model:
            value = model.get("context_window")
            return int(value) if value else None
    return None


def _normalize_provider(provider: str) -> str:
    return provider.strip().lower().replace("-", "_")


_OPENAI_ICON = "config/model-provider-icons/openai.svg"
_NVIDIA_ICON = "config/model-provider-icons/nvidia.svg"
_VERCEL_ICON = "config/model-provider-icons/vercel.svg"
_GITHUB_ICON = "config/model-provider-icons/github.svg"


_PRESETS: tuple[ProviderPreset, ...] = (
    ProviderPreset(
        provider="openai",
        display_name="OpenAI",
        category="cloud",
        endpoint="https://api.openai.com/v1",
        api_key_env_vars=("OPENAI_API_KEY", "OPENAI_API_KEYS"),
        default_model="gpt-5.4-mini",
        models=(
            _model("gpt-5.5", context_window=400000, supports_vision=True, reasoning=True),
            _model("gpt-5.4-mini", context_window=400000, supports_vision=True, reasoning=True),
            _model("chat-latest", "Chat Latest", context_window=128000, supports_vision=True),
        ),
        protocol_family="responses",
        request_format="responses",
        response_format="openai_responses",
        supports_vision=True,
        icon_uri=_OPENAI_ICON,
        docs_ref=".tmp_compare/openclaw/docs/providers/openai.md",
    ),
    ProviderPreset(
        provider="openai_codex",
        display_name="OpenAI Codex OAuth",
        category="subscription",
        adapter_family="provider_plugin",
        implementation_status="catalog_only",
        default_model="gpt-5.4-mini",
        models=(
            _model("gpt-5.4-mini", context_window=400000, supports_vision=True, reasoning=True),
        ),
        protocol_family="responses",
        request_format="responses",
        response_format="openai_responses",
        supports_vision=True,
        icon_uri=_OPENAI_ICON,
        docs_ref=".tmp_compare/openclaw/docs/providers/openai.md",
        notes=("Requires an OAuth/provider plugin flow before direct model calls.",),
    ),
    ProviderPreset(
        provider="anthropic",
        display_name="Anthropic",
        category="cloud",
        adapter_family="anthropic_messages",
        implementation_status="catalog_only",
        endpoint="https://api.anthropic.com",
        api_key_env_vars=("ANTHROPIC_API_KEY", "ANTHROPIC_API_KEYS"),
        default_model="claude-opus-4-6",
        models=(
            _model("claude-opus-4-6", context_window=200000, supports_vision=True, reasoning=True),
            _model(
                "claude-sonnet-4-6",
                context_window=200000,
                supports_vision=True,
                reasoning=True,
            ),
        ),
        request_format="anthropic_messages",
        response_format="anthropic_messages",
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/anthropic.md",
        notes=(
            "Native Anthropic transport is listed for selection; "
            "runtime adapter is not implemented yet.",
        ),
    ),
    ProviderPreset(
        provider="google",
        display_name="Google Gemini",
        category="cloud",
        adapter_family="google_generative_language",
        implementation_status="catalog_only",
        endpoint="https://generativelanguage.googleapis.com",
        api_key_env_vars=("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_API_KEYS"),
        default_model="gemini-3.1-pro-preview",
        models=(
            _model(
                "gemini-3.1-pro-preview",
                context_window=1048576,
                supports_vision=True,
                reasoning=True,
            ),
            _model(
                "gemini-3-flash-preview",
                context_window=1048576,
                supports_vision=True,
                reasoning=True,
            ),
        ),
        request_format="google_generate_content",
        response_format="google_generate_content",
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/google.md",
    ),
    ProviderPreset(
        provider="deepseek",
        display_name="DeepSeek",
        category="cloud",
        endpoint="https://api.deepseek.com/v1",
        api_key_env_vars=("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEYS"),
        default_model="deepseek-chat",
        models=(
            _model("deepseek-chat", context_window=131072),
            _model("deepseek-reasoner", context_window=131072, reasoning=True),
        ),
        docs_ref=".tmp_compare/openclaw/docs/providers/deepseek.md",
    ),
    ProviderPreset(
        provider="moonshot",
        display_name="Moonshot AI",
        category="cloud",
        endpoint="https://api.moonshot.ai/v1",
        api_key_env_vars=("MOONSHOT_API_KEY",),
        default_model="kimi-k2.6",
        models=(_model("kimi-k2.6", context_window=262144, supports_vision=True),),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/moonshot.md",
    ),
    ProviderPreset(
        provider="kimi",
        display_name="Kimi Coding",
        category="subscription",
        endpoint="https://api.moonshot.ai/v1",
        api_key_env_vars=("KIMI_API_KEY", "MOONSHOT_API_KEY"),
        default_model="kimi-for-coding",
        models=(_model("kimi-for-coding", context_window=262144, reasoning=True),),
        docs_ref=".tmp_compare/openclaw/docs/providers/moonshot.md",
    ),
    ProviderPreset(
        provider="qwen",
        display_name="Qwen Cloud",
        category="cloud",
        endpoint="https://dashscope.aliyuncs.com/compatible-mode/v1",
        api_key_env_vars=("DASHSCOPE_API_KEY", "QWEN_API_KEY", "MODELSTUDIO_API_KEY"),
        default_model="qwen-plus",
        models=(
            _model("qwen-plus", context_window=131072, supports_vision=True),
            _model("qwen-max", context_window=32768, supports_vision=True),
        ),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/qwen.md",
    ),
    ProviderPreset(
        provider="volcengine",
        display_name="Volcengine Doubao",
        category="cloud",
        endpoint="https://ark.cn-beijing.volces.com/api/v3",
        api_key_env_vars=("ARK_API_KEY", "VOLCENGINE_API_KEY"),
        default_model="doubao-seed-1-6",
        models=(_model("doubao-seed-1-6", context_window=256000, supports_vision=True),),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/volcengine.md",
    ),
    ProviderPreset(
        provider="byteplus",
        display_name="BytePlus",
        category="cloud",
        endpoint="https://ark.ap-southeast.bytepluses.com/api/v3",
        api_key_env_vars=("BYTEPLUS_API_KEY",),
        default_model="seed-1-6",
        models=(_model("seed-1-6", context_window=256000, supports_vision=True),),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/concepts/model-providers.md",
    ),
    ProviderPreset(
        provider="minimax",
        display_name="MiniMax",
        category="cloud",
        endpoint="https://api.minimax.io/v1",
        api_key_env_vars=("MINIMAX_API_KEY",),
        default_model="minimax-m2.7",
        models=(_model("minimax-m2.7", context_window=200000, reasoning=True),),
        docs_ref=".tmp_compare/openclaw/docs/providers/minimax.md",
    ),
    ProviderPreset(
        provider="zai",
        display_name="Z.AI",
        category="cloud",
        endpoint="https://api.z.ai/api/paas/v4",
        api_key_env_vars=("ZAI_API_KEY", "GLM_API_KEY"),
        default_model="glm-4.7",
        models=(_model("glm-4.7", context_window=128000, reasoning=True),),
        docs_ref=".tmp_compare/openclaw/docs/providers/zai.md",
    ),
    ProviderPreset(
        provider="glm",
        display_name="GLM",
        category="cloud",
        endpoint="https://open.bigmodel.cn/api/paas/v4",
        api_key_env_vars=("GLM_API_KEY", "ZAI_API_KEY"),
        default_model="glm-4.7",
        models=(_model("glm-4.7", context_window=128000, reasoning=True),),
        docs_ref=".tmp_compare/openclaw/docs/providers/glm.md",
    ),
    ProviderPreset(
        provider="openrouter",
        display_name="OpenRouter",
        category="gateway",
        endpoint="https://openrouter.ai/api/v1",
        api_key_env_vars=("OPENROUTER_API_KEY",),
        default_model="auto",
        models=(_model("auto", "Auto"),),
        docs_ref=".tmp_compare/openclaw/docs/providers/openrouter.md",
    ),
    ProviderPreset(
        provider="vercel_ai_gateway",
        display_name="Vercel AI Gateway",
        category="gateway",
        endpoint="https://ai-gateway.vercel.sh/v1",
        api_key_env_vars=("VERCEL_AI_GATEWAY_API_KEY", "AI_GATEWAY_API_KEY"),
        default_model="openai/gpt-5.5",
        models=(_model("openai/gpt-5.5", context_window=400000, supports_vision=True),),
        supports_vision=True,
        icon_uri=_VERCEL_ICON,
        docs_ref=".tmp_compare/openclaw/docs/providers/vercel-ai-gateway.md",
    ),
    ProviderPreset(
        provider="groq",
        display_name="Groq",
        category="cloud",
        endpoint="https://api.groq.com/openai/v1",
        api_key_env_vars=("GROQ_API_KEY",),
        default_model="openai/gpt-oss-120b",
        models=(_model("openai/gpt-oss-120b", context_window=131072),),
        docs_ref=".tmp_compare/openclaw/docs/providers/groq.md",
    ),
    ProviderPreset(
        provider="mistral",
        display_name="Mistral",
        category="cloud",
        endpoint="https://api.mistral.ai/v1",
        api_key_env_vars=("MISTRAL_API_KEY",),
        default_model="mistral-large-latest",
        models=(_model("mistral-large-latest", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/mistral.md",
    ),
    ProviderPreset(
        provider="xai",
        display_name="xAI",
        category="cloud",
        endpoint="https://api.x.ai/v1",
        api_key_env_vars=("XAI_API_KEY",),
        default_model="grok-4.3",
        models=(_model("grok-4.3", context_window=256000, supports_vision=True, reasoning=True),),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/xai.md",
    ),
    ProviderPreset(
        provider="together",
        display_name="Together AI",
        category="cloud",
        endpoint="https://api.together.xyz/v1",
        api_key_env_vars=("TOGETHER_API_KEY",),
        default_model="meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo",
        models=(_model("meta-llama/Meta-Llama-3.1-405B-Instruct-Turbo", context_window=131072),),
        docs_ref=".tmp_compare/openclaw/docs/providers/together.md",
    ),
    ProviderPreset(
        provider="fireworks",
        display_name="Fireworks",
        category="cloud",
        endpoint="https://api.fireworks.ai/inference/v1",
        api_key_env_vars=("FIREWORKS_API_KEY",),
        default_model="accounts/fireworks/models/llama-v3p1-405b-instruct",
        models=(
            _model(
                "accounts/fireworks/models/llama-v3p1-405b-instruct",
                context_window=131072,
            ),
        ),
        docs_ref=".tmp_compare/openclaw/docs/providers/fireworks.md",
    ),
    ProviderPreset(
        provider="cerebras",
        display_name="Cerebras",
        category="cloud",
        endpoint="https://api.cerebras.ai/v1",
        api_key_env_vars=("CEREBRAS_API_KEY",),
        default_model="qwen-3-coder-480b",
        models=(_model("qwen-3-coder-480b", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/cerebras.md",
    ),
    ProviderPreset(
        provider="chutes",
        display_name="Chutes",
        category="cloud",
        endpoint="https://llm.chutes.ai/v1",
        api_key_env_vars=("CHUTES_API_KEY",),
        default_model="zai-org/GLM-4.7-TEE",
        models=(_model("zai-org/GLM-4.7-TEE", context_window=128000, reasoning=True),),
        docs_ref=".tmp_compare/openclaw/docs/providers/chutes.md",
    ),
    ProviderPreset(
        provider="venice",
        display_name="Venice AI",
        category="cloud",
        endpoint="https://api.venice.ai/api/v1",
        api_key_env_vars=("VENICE_API_KEY",),
        default_model="llama-3.3-70b",
        models=(_model("llama-3.3-70b", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/venice.md",
    ),
    ProviderPreset(
        provider="nvidia",
        display_name="NVIDIA",
        category="cloud",
        endpoint="https://integrate.api.nvidia.com/v1",
        api_key_env_vars=("NVIDIA_API_KEY",),
        default_model="meta/llama-3.1-405b-instruct",
        models=(_model("meta/llama-3.1-405b-instruct", context_window=131072),),
        icon_uri=_NVIDIA_ICON,
        docs_ref=".tmp_compare/openclaw/docs/providers/nvidia.md",
    ),
    ProviderPreset(
        provider="huggingface",
        display_name="Hugging Face",
        category="cloud",
        endpoint="https://router.huggingface.co/v1",
        api_key_env_vars=("HF_TOKEN", "HUGGINGFACE_API_KEY"),
        default_model="openai/gpt-oss-120b",
        models=(_model("openai/gpt-oss-120b", context_window=131072),),
        docs_ref=".tmp_compare/openclaw/docs/providers/huggingface.md",
    ),
    ProviderPreset(
        provider="qianfan",
        display_name="Qianfan",
        category="cloud",
        endpoint="https://qianfan.baidubce.com/v2",
        api_key_env_vars=("QIANFAN_API_KEY",),
        default_model="ernie-4.5-turbo-128k",
        models=(_model("ernie-4.5-turbo-128k", context_window=131072, supports_vision=True),),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/qianfan.md",
    ),
    ProviderPreset(
        provider="tencent",
        display_name="Tencent Cloud TokenHub",
        category="cloud",
        endpoint="https://api.lkeap.cloud.tencent.com/v1",
        api_key_env_vars=("TENCENT_TOKENHUB_API_KEY", "TENCENTCLOUD_API_KEY"),
        default_model="hunyuan-turbos-latest",
        models=(_model("hunyuan-turbos-latest", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/tencent.md",
    ),
    ProviderPreset(
        provider="stepfun",
        display_name="StepFun",
        category="cloud",
        endpoint="https://api.stepfun.com/v1",
        api_key_env_vars=("STEPFUN_API_KEY",),
        default_model="step-2-mini",
        models=(_model("step-2-mini", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/stepfun.md",
    ),
    ProviderPreset(
        provider="arcee",
        display_name="Arcee AI",
        category="cloud",
        endpoint="https://api.arcee.ai/api/v1",
        api_key_env_vars=("ARCEEAI_API_KEY",),
        default_model="arcee-ai/AFM-4.5B",
        models=(_model("arcee-ai/AFM-4.5B", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/arcee.md",
    ),
    ProviderPreset(
        provider="cloudflare_ai_gateway",
        display_name="Cloudflare AI Gateway",
        category="gateway",
        adapter_family="openai_compatible",
        implementation_status="requires_endpoint",
        api_key_env_vars=("CLOUDFLARE_API_TOKEN",),
        default_model="workers-ai/@cf/meta/llama-3.1-70b-instruct",
        models=(_model("workers-ai/@cf/meta/llama-3.1-70b-instruct", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/cloudflare-ai-gateway.md",
        notes=("Endpoint depends on account and gateway id.",),
    ),
    ProviderPreset(
        provider="bedrock",
        display_name="Amazon Bedrock",
        category="cloud",
        adapter_family="aws_bedrock",
        implementation_status="catalog_only",
        default_model="anthropic.claude-sonnet-4-6",
        models=(
            _model(
                "anthropic.claude-sonnet-4-6",
                context_window=200000,
                supports_vision=True,
            ),
        ),
        request_format="aws_bedrock",
        response_format="aws_bedrock",
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/bedrock.md",
    ),
    ProviderPreset(
        provider="bedrock_mantle",
        display_name="Amazon Bedrock Mantle",
        category="gateway",
        adapter_family="openai_compatible",
        implementation_status="requires_endpoint",
        api_key_env_vars=("AWS_BEARER_TOKEN_BEDROCK",),
        default_model="claude-opus-4.7",
        models=(_model("claude-opus-4.7", context_window=200000, supports_vision=True),),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/bedrock-mantle.md",
    ),
    ProviderPreset(
        provider="github_copilot",
        display_name="GitHub Copilot",
        category="subscription",
        adapter_family="provider_plugin",
        implementation_status="catalog_only",
        default_model="gpt-5.5",
        models=(_model("gpt-5.5", context_window=400000, supports_vision=True),),
        icon_uri=_GITHUB_ICON,
        docs_ref=".tmp_compare/openclaw/docs/providers/github-copilot.md",
    ),
    ProviderPreset(
        provider="kilocode",
        display_name="Kilo Gateway",
        category="gateway",
        endpoint="https://api.kilo-code.ai/v1",
        api_key_env_vars=("KILOCODE_API_KEY",),
        default_model="anthropic/claude-sonnet-4-6",
        models=(
            _model(
                "anthropic/claude-sonnet-4-6",
                context_window=200000,
                supports_vision=True,
            ),
        ),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/kilocode.md",
    ),
    ProviderPreset(
        provider="opencode",
        display_name="OpenCode",
        category="subscription",
        endpoint="https://api.opencode.ai/v1",
        api_key_env_vars=("OPENCODE_API_KEY", "OPENCODE_ZEN_API_KEY"),
        default_model="claude-opus-4-6",
        models=(_model("claude-opus-4-6", context_window=200000, supports_vision=True),),
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/opencode.md",
    ),
    ProviderPreset(
        provider="opencode_go",
        display_name="OpenCode Go",
        category="subscription",
        endpoint="https://api.opencode.ai/v1",
        api_key_env_vars=("OPENCODE_API_KEY",),
        default_model="kimi-k2.6",
        models=(_model("kimi-k2.6", context_window=262144),),
        docs_ref=".tmp_compare/openclaw/docs/providers/opencode-go.md",
    ),
    ProviderPreset(
        provider="litellm",
        display_name="LiteLLM",
        category="gateway",
        adapter_family="openai_compatible",
        implementation_status="requires_endpoint",
        api_key_env_vars=("LITELLM_API_KEY",),
        default_model="default",
        models=(_model("default", "Default"),),
        docs_ref=".tmp_compare/openclaw/docs/providers/litellm.md",
    ),
    ProviderPreset(
        provider="lmstudio",
        display_name="LM Studio",
        category="local",
        endpoint="http://127.0.0.1:1234/v1",
        api_key_env_vars=("LMSTUDIO_API_KEY",),
        default_model="local-model",
        models=(_model("local-model", "Local model", context_window=8192),),
        is_local_default=True,
        allow_cloud_default=False,
        docs_ref=".tmp_compare/openclaw/docs/providers/lmstudio.md",
    ),
    ProviderPreset(
        provider="ollama",
        display_name="Ollama",
        category="local",
        adapter_family="ollama",
        endpoint="http://127.0.0.1:11434",
        api_key_env_vars=("OLLAMA_API_KEY",),
        default_model="llama3.1",
        models=(
            _model("llama3.1", context_window=128000),
            _model("qwen2.5vl:7b", context_window=32768, supports_vision=True),
        ),
        is_local_default=True,
        allow_cloud_default=False,
        protocol_family="ollama",
        request_format="ollama_chat",
        response_format="ollama_chat",
        supports_vision=True,
        docs_ref=".tmp_compare/openclaw/docs/providers/ollama.md",
        notes=("Use native Ollama base URL without /v1 when tool behavior matters.",),
    ),
    ProviderPreset(
        provider="vllm",
        display_name="vLLM",
        category="local",
        endpoint="http://127.0.0.1:8000/v1",
        default_model="local-model",
        models=(_model("local-model", "Local model", context_window=32768),),
        is_local_default=True,
        allow_cloud_default=False,
        docs_ref=".tmp_compare/openclaw/docs/providers/vllm.md",
    ),
    ProviderPreset(
        provider="sglang",
        display_name="SGLang",
        category="local",
        endpoint="http://127.0.0.1:30000/v1",
        default_model="local-model",
        models=(_model("local-model", "Local model", context_window=32768),),
        is_local_default=True,
        allow_cloud_default=False,
        docs_ref=".tmp_compare/openclaw/docs/providers/sglang.md",
    ),
    ProviderPreset(
        provider="inferrs",
        display_name="inferrs",
        category="local",
        endpoint="http://127.0.0.1:8080/v1",
        default_model="local-model",
        models=(_model("local-model", "Local model", context_window=32768),),
        is_local_default=True,
        allow_cloud_default=False,
        docs_ref=".tmp_compare/openclaw/docs/providers/inferrs.md",
    ),
    ProviderPreset(
        provider="ds4",
        display_name="ds4",
        category="local",
        endpoint="http://127.0.0.1:8000/v1",
        default_model="deepseek-v4",
        models=(_model("deepseek-v4", context_window=128000, reasoning=True),),
        is_local_default=True,
        allow_cloud_default=False,
        docs_ref=".tmp_compare/openclaw/docs/providers/ds4.md",
    ),
    ProviderPreset(
        provider="xiaomi",
        display_name="Xiaomi",
        category="cloud",
        adapter_family="openai_compatible",
        implementation_status="requires_endpoint",
        api_key_env_vars=("XIAOMI_API_KEY",),
        default_model="default",
        models=(_model("default", "Default"),),
        docs_ref=".tmp_compare/openclaw/docs/providers/xiaomi.md",
    ),
    ProviderPreset(
        provider="perplexity",
        display_name="Perplexity",
        category="cloud",
        endpoint="https://api.perplexity.ai",
        api_key_env_vars=("PERPLEXITY_API_KEY",),
        default_model="sonar-pro",
        models=(_model("sonar-pro", context_window=128000),),
        docs_ref=".tmp_compare/openclaw/docs/providers/perplexity-provider.md",
    ),
    ProviderPreset(
        provider="synthetic",
        display_name="Synthetic",
        category="test",
        adapter_family="synthetic",
        implementation_status="catalog_only",
        default_model="synthetic",
        models=(_model("synthetic", "Synthetic", context_window=8192),),
        allow_cloud_default=False,
        docs_ref=".tmp_compare/openclaw/docs/providers/synthetic.md",
    ),
    ProviderPreset(
        provider="custom_openai_compatible",
        display_name="Custom OpenAI-compatible",
        category="custom",
        adapter_family="openai_compatible",
        implementation_status="requires_endpoint",
        default_model="custom-model",
        models=(_model("custom-model", "Custom model", context_window=8192),),
        allow_cloud_default=False,
        docs_ref=".tmp_compare/openclaw/docs/concepts/model-providers.md",
    ),
)

_PRESET_BY_ID = {_normalize_provider(preset.provider): preset for preset in _PRESETS}
