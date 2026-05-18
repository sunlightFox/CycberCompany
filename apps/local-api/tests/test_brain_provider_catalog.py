from __future__ import annotations

from app.services.brain_provider_catalog import apply_provider_defaults, list_provider_presets
from fastapi.testclient import TestClient


def test_openclaw_provider_catalog_exposes_llm_provider_presets() -> None:
    providers = {item.provider: item for item in list_provider_presets()}

    assert "openai" in providers
    assert "deepseek" in providers
    assert "moonshot" in providers
    assert "ollama" in providers
    assert "vllm" in providers
    assert providers["openai"].icon_uri == "config/model-provider-icons/openai.svg"
    assert providers["nvidia"].icon_uri == "config/model-provider-icons/nvidia.svg"
    assert providers["vercel_ai_gateway"].icon_uri == "config/model-provider-icons/vercel.svg"
    assert providers["github_copilot"].icon_uri == "config/model-provider-icons/github.svg"
    assert providers["anthropic"].implementation_status == "catalog_only"


def test_provider_defaults_fill_brain_creation_contract() -> None:
    data = apply_provider_defaults(
        {
            "display_name": "DeepSeek main",
            "provider": "deepseek",
            "endpoint": None,
            "model_name": "deepseek-chat",
            "is_local": False,
            "supports_tools": False,
            "supports_vision": False,
            "supports_audio": False,
            "privacy_policy": {},
        }
    )

    assert data["endpoint"] == "https://api.deepseek.com/v1"
    assert data["protocol_family"] == "auto"
    assert data["request_format"] == "chat_completions"
    assert data["supports_stream"] is True
    assert data["allow_cloud"] is True
    assert data["context_window"] == 131072
    assert data["privacy_policy"]["provider_display_name"] == "DeepSeek"
    assert data["privacy_policy"]["adapter_family"] == "openai_compatible"


def test_provider_defaults_respect_explicit_locality() -> None:
    cloud_default = apply_provider_defaults(
        {
            "provider": "deepseek",
            "endpoint": None,
            "model_name": "deepseek-chat",
            "is_local": True,
            "allow_cloud": False,
            "privacy_policy": {},
        },
        explicit_fields={"provider", "model_name"},
    )
    explicit_local = apply_provider_defaults(
        {
            "provider": "deepseek",
            "endpoint": None,
            "model_name": "deepseek-chat",
            "is_local": True,
            "allow_cloud": False,
            "privacy_policy": {},
        },
        explicit_fields={"provider", "model_name", "is_local", "allow_cloud"},
    )

    assert cloud_default["is_local"] is False
    assert cloud_default["allow_cloud"] is True
    assert explicit_local["is_local"] is True
    assert explicit_local["allow_cloud"] is False


def test_brain_provider_catalog_api_route(client: TestClient) -> None:
    response = client.get("/api/brains/providers")

    assert response.status_code == 200, response.text
    providers = {item["provider"]: item for item in response.json()["items"]}
    assert providers["openai"]["default_model"] == "gpt-5.5"
    assert providers["deepseek"]["endpoint"] == "https://api.deepseek.com/v1"
    assert providers["ollama"]["is_local_default"] is True


def test_create_cloud_brain_uses_provider_defaults_and_requires_secret(
    client: TestClient,
) -> None:
    missing_secret = client.post(
        "/api/brains",
        json={
            "display_name": "DeepSeek no key",
            "provider": "deepseek",
            "model_name": "deepseek-chat",
        },
    )
    assert missing_secret.status_code == 422

    created = client.post(
        "/api/brains",
        json={
            "display_name": "DeepSeek with key",
            "provider": "deepseek",
            "model_name": "deepseek-chat",
            "api_key": "sk-test-deepseek",
        },
    )

    assert created.status_code == 200, created.text
    body = created.json()
    assert body["provider"] == "deepseek"
    assert body["endpoint"] == "https://api.deepseek.com/v1"
    assert body["is_local"] is False
    assert body["allow_cloud"] is True
    assert body["context_window"] == 131072
    assert body["has_api_key"] is True
    assert "api_key" not in body
