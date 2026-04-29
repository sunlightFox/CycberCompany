from __future__ import annotations

import json
from pathlib import Path

from cryptography.fernet import Fernet

from app.core.config import ConfigError
from app.core.time import new_id


class SecretStore:
    def __init__(self, secret_dir: Path) -> None:
        self._secret_dir = secret_dir
        self._key_path = secret_dir / "master.key"
        self._store_path = secret_dir / "local_secrets.json"

    def put_secret(self, value: str) -> tuple[str, str]:
        self._secret_dir.mkdir(parents=True, exist_ok=True)
        fernet = self._fernet()
        secret_ref = new_id("sec")
        data = self._read_store()
        data[secret_ref] = fernet.encrypt(value.encode("utf-8")).decode("ascii")
        self._write_store(data)
        return secret_ref, f"local://secrets/{secret_ref}"

    def rotate_secret(self, secret_ref: str, value: str) -> str:
        self._secret_dir.mkdir(parents=True, exist_ok=True)
        fernet = self._fernet()
        data = self._read_store()
        data[secret_ref] = fernet.encrypt(value.encode("utf-8")).decode("ascii")
        self._write_store(data)
        return f"local://secrets/{secret_ref}"

    def get_secret(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        try:
            data = self._read_store()
            encrypted = data.get(secret_ref)
            if not encrypted:
                return None
            return self._fernet().decrypt(encrypted.encode("ascii")).decode("utf-8")
        except Exception as exc:
            raise ConfigError("Secret store could not read requested secret") from exc

    def _fernet(self) -> Fernet:
        if not self._key_path.exists():
            self._secret_dir.mkdir(parents=True, exist_ok=True)
            _atomic_write_bytes(self._key_path, Fernet.generate_key())
            _best_effort_private(self._key_path)
        try:
            return Fernet(self._key_path.read_bytes())
        except Exception as exc:
            raise ConfigError("Secret store master key could not be read") from exc

    def _read_store(self) -> dict[str, str]:
        if not self._store_path.exists():
            return {}
        try:
            raw = json.loads(self._store_path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("secret store JSON must be an object")
            return {str(key): str(value) for key, value in raw.items()}
        except Exception as exc:
            raise ConfigError("Secret store could not read local secret index") from exc

    def _write_store(self, data: dict[str, str]) -> None:
        try:
            _atomic_write_text(
                self._store_path,
                json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True),
            )
            _best_effort_private(self._store_path)
        except Exception as exc:
            raise ConfigError("Secret store could not write local secret index") from exc


def _atomic_write_text(path: Path, content: str) -> None:
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_text(content, encoding="utf-8")
    temp_path.replace(path)


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    temp_path = path.with_suffix(f"{path.suffix}.tmp")
    temp_path.write_bytes(content)
    temp_path.replace(path)


def _best_effort_private(path: Path) -> None:
    try:
        path.chmod(0o600)
    except OSError:
        pass
