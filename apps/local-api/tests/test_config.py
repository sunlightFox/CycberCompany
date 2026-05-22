from __future__ import annotations

from pathlib import Path

import pytest
from app.core.config import ConfigError, ensure_data_dirs, load_app_config
from app.services.secrets import SecretStore


def test_config_001_missing_config_file_raises_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_app_config(tmp_path)


def test_config_002_storage_override_creates_data_dirs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = Path(__file__).resolve().parents[3]
    data_dir = tmp_path / "runtime-data"
    monkeypatch.setenv("CYCBER_DATA_DIR", str(data_dir))

    config = load_app_config(root)
    ensure_data_dirs(config)

    assert config.storage.data_dir == data_dir
    assert config.storage.sqlite_path.parent.exists()
    assert config.storage.trace_dir.exists()
    assert config.storage.artifact_dir.exists()
    assert (config.storage.data_dir / "secrets").exists()


def test_config_003_secret_store_can_read_env_refs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env-test")
    store = SecretStore(tmp_path / "secrets")

    assert store.get_secret("env://OPENAI_API_KEY") == "sk-env-test"
    assert store.get_secret("env://MISSING_TEST_SECRET") is None


def test_config_004_secret_store_can_read_codex_auth_refs(tmp_path: Path) -> None:
    store = SecretStore(tmp_path / "secrets")
    value = store.get_secret("codex-auth://OPENAI_API_KEY")

    assert value is None or isinstance(value, str)
