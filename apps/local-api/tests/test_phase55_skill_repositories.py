from __future__ import annotations

import hashlib
import tarfile
import zipfile
from pathlib import Path

from fastapi.testclient import TestClient


def test_phase55_default_repositories_search_and_repository_install(
    client: TestClient,
) -> None:
    repositories = client.get("/api/skills/repositories")
    assert repositories.status_code == 200, repositories.text
    items = repositories.json()["items"]
    by_id = {item["repository_id"]: item for item in items}
    assert by_id["clawhub"]["is_default"] is True
    assert by_id["skillhub"]["auth"] == {"env_ref": "SKILLHUB_API_KEY"}

    search = client.get("/api/skills/catalog/search", params={"q": "draft"})
    assert search.status_code == 200, search.text
    results = search.json()["items"]
    assert results
    assert results[0]["repository_id"] == "clawhub"

    install = client.post(
        "/api/skills/install",
        json={
            "source_type": "repository_ref",
            "source_uri": "clawhub:official/content/local-draft",
        },
    )
    assert install.status_code == 200, install.text
    payload = install.json()
    assert payload["bundle"]["bundle_id"] == "clawhub-local-draft"
    assert payload["bundle"]["status"] == "installed_disabled"


def test_phase55_clawhub_office_catalog_contains_popular_office_skills(
    client: TestClient,
) -> None:
    search = client.get(
        "/api/skills/catalog/search",
        params={"q": "office", "repository_id": "clawhub", "limit": 20},
    )
    assert search.status_code == 200, search.text
    refs = {item["package_ref"] for item in search.json()["items"]}

    assert {
        "official/office/daily-brief",
        "official/office/meeting-notes",
        "official/office/email-draft",
        "official/office/calendar-plan",
        "official/office/web-research-brief",
        "official/office/file-conversion-plan",
        "official/office/spreadsheet-analysis",
        "official/office/project-followup",
        "official/office/customer-followup",
    }.issubset(refs)


def test_phase55_preview_install_local_archive_and_blocks_traversal(
    client: TestClient,
    tmp_path: Path,
) -> None:
    bundle_dir = _write_bundle(tmp_path, "archive-skill")
    archive = tmp_path / "archive-skill.zip"
    _zip_dir(bundle_dir, archive)

    preview = client.post(
        "/api/skills/preview-install",
        json={"source_type": "local_archive", "source_uri": str(archive)},
    )
    assert preview.status_code == 200, preview.text
    assert preview.json()["preview"]["bundle_id"] == "archive-skill"

    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("../escape.txt", "nope")
    blocked = client.post(
        "/api/skills/preview-install",
        json={"source_type": "local_archive", "source_uri": str(bad)},
    )
    assert blocked.status_code == 422
    assert blocked.json()["error"]["code"] == "PLUGIN_VALIDATE_FAILED"


def test_phase55_skill_md_url_preview_with_checksum(
    client: TestClient,
    monkeypatch,
) -> None:
    skill_md = (
        "# Remote Skill\n\n"
        "## 用途\n测试。\n\n"
        "## 何时使用\n测试。\n\n"
        "## 输入\ncontent。\n\n"
        "## 输出\n工件。\n\n"
        "## 步骤\n写入文件。\n\n"
        "## 禁止\n不得访问密钥。\n"
    )
    payload = skill_md.encode("utf-8")

    async def fake_download(url: str) -> bytes:
        assert url == "https://example.test/SKILL.md"
        return payload

    from app.services import skill_source_resolver

    monkeypatch.setattr(skill_source_resolver, "_download", fake_download)
    checksum = "sha256:" + hashlib.sha256(payload).hexdigest()
    response = client.post(
        "/api/skills/preview-install",
        json={
            "source_type": "skill_md_url",
            "source_uri": "https://example.test/SKILL.md",
            "install_options": {"checksum": checksum, "bundle_id": "remote-md-skill"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["preview"]["bundle_id"] == "remote-md-skill"


def test_phase55_github_path_preview_uses_archive_download(
    client: TestClient,
    tmp_path: Path,
    monkeypatch,
) -> None:
    bundle_dir = _write_bundle(tmp_path, "github-skill")
    payload = _tar_dir(bundle_dir, top_dir="repo-main/skills/github-skill")

    async def fake_download(url: str) -> bytes:
        assert url == "https://codeload.github.com/example/repo/tar.gz/main"
        return payload

    from app.services import skill_source_resolver

    monkeypatch.setattr(skill_source_resolver, "_download", fake_download)
    response = client.post(
        "/api/skills/preview-install",
        json={
            "source_type": "github_path",
            "source_uri": "github:example/repo/skills/github-skill",
            "install_options": {"ref": "main"},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["preview"]["bundle_id"] == "github-skill"


def _write_bundle(tmp_path: Path, bundle_id: str) -> Path:
    root = tmp_path / bundle_id
    root.mkdir()
    (root / "bundle.yaml").write_text(
        f"""
id: {bundle_id}
bundle_revision: 1.0.0
display_name: Archive Skill
description: Archive skill test
author: local
required_tools: [file.write]
permissions:
  tools:
    - name: file.write
      actions: [write_task_artifact]
      risk: R2
  assets: []
steps:
  - tool_name: file.write
    args:
      path: outputs/{bundle_id}.md
      content: archive
""".strip(),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(
        """
# Archive Skill

## 用途
测试。

## 何时使用
测试。

## 输入
content。

## 输出
工件。

## 步骤
写入文件。

## 禁止
不得访问密钥。
""".strip(),
        encoding="utf-8",
    )
    return root


def _zip_dir(root: Path, archive: Path) -> None:
    with zipfile.ZipFile(archive, "w") as zf:
        for path in root.rglob("*"):
            if path.is_file():
                zf.write(path, path.relative_to(root.parent).as_posix())


def _tar_dir(root: Path, *, top_dir: str) -> bytes:
    import io

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in root.rglob("*"):
            if path.is_file():
                arcname = f"{top_dir}/{path.relative_to(root).as_posix()}"
                archive.add(path, arcname=arcname)
    return buffer.getvalue()
