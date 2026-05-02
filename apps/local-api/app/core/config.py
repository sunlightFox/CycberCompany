from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ConfigError(RuntimeError):
    pass


class EnvironmentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CYCBER_", extra="ignore")

    root: Path | None = None
    data_dir: Path | None = None
    api_host: str = "127.0.0.1"
    api_port: int = 8765
    background_workers_enabled: bool = False
    background_worker_interval_seconds: float = 5.0
    background_worker_startup_tick: bool = False
    background_worker_timeout_seconds: float = 60.0


class AppSection(BaseModel):
    mode: str
    default_shell: str
    trace_level: str
    locale: str
    version: str = "0.1.0"


class DesktopSection(BaseModel):
    auto_start_api: bool = True
    api_port: int = 8765


class FeatureSection(BaseModel):
    cloud_models: str = "optional"
    mcp: str = "optional"
    plugins: str = "optional"


class StorageSection(BaseModel):
    data_dir: Path
    sqlite_path: Path
    trace_dir: Path
    artifact_dir: Path


class RuntimePaths(BaseModel):
    root_dir: Path
    config_dir: Path
    shells_dir: Path
    migrations_dir: Path


class WorkerSection(BaseModel):
    enabled: bool = False
    interval_seconds: float = 5.0
    startup_tick: bool = False
    timeout_seconds: float = 60.0


class ChannelProviderSection(BaseModel):
    enabled: bool = False
    test_only: bool = False
    state_dir: Path | None = None
    timeout_seconds: float = 10.0
    min_version: str = "0.4.0"
    private_chat_only: bool = True
    group_messages: str = "disabled"
    allow_mock_fallback: bool = False
    poll_enabled: bool = False
    poll_interval_seconds: float = 5.0
    poll_batch_size: int = 20
    pairing_required: bool = True
    allow_unknown_private: bool = False
    media: dict[str, Any] = Field(default_factory=dict)


class ChannelsSection(BaseModel):
    providers: dict[str, ChannelProviderSection] = Field(default_factory=dict)


class AppConfig(BaseModel):
    app: AppSection
    desktop: DesktopSection
    features: FeatureSection
    storage: StorageSection
    model_routing: dict[str, Any] = Field(default_factory=dict)
    safety: dict[str, Any] = Field(default_factory=dict)
    mcp: dict[str, Any] = Field(default_factory=dict)
    skills: dict[str, Any] = Field(default_factory=dict)
    channels: ChannelsSection = Field(default_factory=ChannelsSection)
    workers: WorkerSection = Field(default_factory=WorkerSection)
    paths: RuntimePaths


def default_root_dir() -> Path:
    return Path(__file__).resolve().parents[4]


def load_app_config(root_dir: Path | None = None) -> AppConfig:
    env = EnvironmentSettings()
    root = (root_dir or env.root or default_root_dir()).resolve()
    config_dir = root / "config"

    app_data = _read_yaml(config_dir / "app.yaml")
    storage_data = _read_yaml(config_dir / "storage.yaml")
    model_routing = _read_yaml(config_dir / "model-routing.yaml")
    safety = _read_yaml(config_dir / "safety.yaml")
    mcp = _read_yaml(config_dir / "mcp.yaml")
    skills = _read_optional_yaml(config_dir / "skills.yaml")
    channels_data = _read_optional_yaml(config_dir / "channels.yaml")

    storage_raw = dict(storage_data.get("storage", {}))
    if env.data_dir is not None:
        data_dir = _resolve_path(root, env.data_dir)
        storage_raw["data_dir"] = data_dir
        storage_raw["sqlite_path"] = data_dir / "sqlite" / "app.db"
        storage_raw["trace_dir"] = data_dir / "traces"
        storage_raw["artifact_dir"] = data_dir / "artifacts"

    storage = StorageSection(
        data_dir=_resolve_path(root, storage_raw["data_dir"]),
        sqlite_path=_resolve_path(root, storage_raw["sqlite_path"]),
        trace_dir=_resolve_path(root, storage_raw["trace_dir"]),
        artifact_dir=_resolve_path(root, storage_raw["artifact_dir"]),
    )

    return AppConfig(
        app=AppSection(**app_data["app"]),
        desktop=DesktopSection(**app_data.get("desktop", {})),
        features=FeatureSection(**app_data.get("features", {})),
        storage=storage,
        model_routing=model_routing,
        safety=safety,
        mcp=mcp,
        skills=skills,
        channels=_channels_section(root, channels_data),
        workers=WorkerSection(
            enabled=env.background_workers_enabled,
            interval_seconds=max(0.5, float(env.background_worker_interval_seconds)),
            startup_tick=env.background_worker_startup_tick,
            timeout_seconds=max(1.0, float(env.background_worker_timeout_seconds)),
        ),
        paths=RuntimePaths(
            root_dir=root,
            config_dir=config_dir,
            shells_dir=root / "shells",
            migrations_dir=root / "apps" / "local-api" / "app" / "db" / "migrations",
        ),
    )


def ensure_data_dirs(config: AppConfig) -> None:
    config.storage.data_dir.mkdir(parents=True, exist_ok=True)
    config.storage.sqlite_path.parent.mkdir(parents=True, exist_ok=True)
    config.storage.trace_dir.mkdir(parents=True, exist_ok=True)
    config.storage.artifact_dir.mkdir(parents=True, exist_ok=True)
    (config.storage.data_dir / "secrets").mkdir(parents=True, exist_ok=True)
    (config.storage.data_dir / "backups").mkdir(parents=True, exist_ok=True)
    (config.storage.data_dir / "restore-workspaces").mkdir(parents=True, exist_ok=True)
    (config.storage.data_dir / "diagnostics").mkdir(parents=True, exist_ok=True)
    (config.storage.data_dir / "release-reports").mkdir(parents=True, exist_ok=True)
    for provider in config.channels.providers.values():
        if provider.state_dir is not None:
            provider.state_dir.mkdir(parents=True, exist_ok=True)


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Required config file not found: {path}")
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"Config file must contain a YAML mapping: {path}")
    return data


def _read_optional_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return _read_yaml(path)


def _resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    return (root / path).resolve()


def _channels_section(root: Path, data: dict[str, Any]) -> ChannelsSection:
    raw = dict(data.get("channels", {}))
    providers = {}
    for name, provider_raw in dict(raw.get("providers", {})).items():
        provider_data = dict(provider_raw or {})
        if provider_data.get("state_dir") is not None:
            provider_data["state_dir"] = _resolve_path(root, provider_data["state_dir"])
        providers[str(name)] = ChannelProviderSection(**provider_data)
    return ChannelsSection(providers=providers)
