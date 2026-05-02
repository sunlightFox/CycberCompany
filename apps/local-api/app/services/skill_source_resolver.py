from __future__ import annotations

import hashlib
import io
import json
import shutil
import tarfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlparse

import httpx
import yaml
from core_types import ErrorCode

from app.core.errors import AppError
from app.schemas.skills import BundleInstallRequest

if TYPE_CHECKING:
    from app.services.skill_repositories import SkillRepositoryService

MAX_ARCHIVE_FILES = 400
MAX_ARCHIVE_BYTES = 10 * 1024 * 1024
MAX_DOWNLOAD_BYTES = 15 * 1024 * 1024


@dataclass(frozen=True)
class ResolvedSkillSource:
    root: Path
    source_type: str
    source_uri: str
    package_ref: str | None = None
    repository_id: str | None = None


class SkillSourceResolver:
    def __init__(
        self,
        *,
        root_dir: Path,
        cache_dir: Path,
        repository_service: SkillRepositoryService | None = None,
    ) -> None:
        self._root_dir = root_dir
        self._cache_dir = cache_dir
        self._repository_service = repository_service

    async def resolve(self, request: BundleInstallRequest) -> ResolvedSkillSource:
        source_type = request.source_type or "local_directory"
        if source_type == "repository_ref":
            return await self._resolve_repository_ref(request)
        if source_type == "local_directory":
            root = _resolve_path(self._root_dir, request.source_uri)
            if not root.exists() or not root.is_dir():
                raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "安装目录不存在", status_code=404)
            return ResolvedSkillSource(root=root, source_type=source_type, source_uri=str(root))
        if source_type == "local_archive":
            archive_path = _resolve_path(self._root_dir, request.source_uri)
            if not archive_path.exists() or not archive_path.is_file():
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "安装压缩包不存在",
                    status_code=404,
                )
            root = self._cache_path(source_type, str(archive_path), archive_path.read_bytes())
            _reset_dir(root)
            _extract_archive(archive_path.read_bytes(), root, archive_path.name)
            return ResolvedSkillSource(
                root=_bundle_root(root),
                source_type=source_type,
                source_uri=str(archive_path),
            )
        if source_type == "archive_url":
            _validate_download_url(request.source_uri)
            payload = await _download(request.source_uri)
            checksum = _checksum_from_options(request.install_options)
            _verify_checksum(payload, checksum)
            root = self._cache_path(source_type, request.source_uri, payload)
            _reset_dir(root)
            _extract_archive(payload, root, Path(urlparse(request.source_uri).path).name)
            return ResolvedSkillSource(
                root=_bundle_root(root),
                source_type=source_type,
                source_uri=request.source_uri,
            )
        if source_type == "skill_md_url":
            _validate_download_url(request.source_uri)
            payload = await _download(request.source_uri)
            checksum = _checksum_from_options(request.install_options)
            _verify_checksum(payload, checksum)
            root = self._cache_path(source_type, request.source_uri, payload)
            _reset_dir(root)
            _write_single_skill_bundle(
                root,
                skill_md=payload.decode("utf-8"),
                request=request,
            )
            return ResolvedSkillSource(
                root=root,
                source_type=source_type,
                source_uri=request.source_uri,
            )
        if source_type == "github_path":
            root = await self._resolve_github_path(request)
            return ResolvedSkillSource(
                root=root,
                source_type=source_type,
                source_uri=request.source_uri,
            )
        if source_type in {"well_known", "tap"}:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "well_known/tap 是仓库源添加方式，不能直接安装 Skill",
                status_code=422,
            )
        raise AppError(
            ErrorCode.PLUGIN_VALIDATE_FAILED,
            "不支持的 Skill 安装源",
            status_code=422,
            details={"source_type": source_type},
        )

    async def _resolve_repository_ref(self, request: BundleInstallRequest) -> ResolvedSkillSource:
        if self._repository_service is None:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 仓库服务未初始化",
                status_code=500,
            )
        entry = await self._repository_service.resolve_repository_ref(
            request.source_uri,
            repository_id=request.repository_id,
        )
        source = dict(entry.source)
        nested_request = BundleInstallRequest(
            source_type=str(source.get("type")),
            source_uri=str(source.get("uri") or source.get("url") or ""),
            requested_by_member_id=request.requested_by_member_id,
            install_options={
                **request.install_options,
                "checksum": request.install_options.get("checksum") or entry.checksum,
                "repository_id": entry.repository_id,
                "package_ref": entry.package_ref,
            },
            idempotency_key=request.idempotency_key,
            repository_id=entry.repository_id,
            package_ref=entry.package_ref,
        )
        resolved = await self.resolve(nested_request)
        return ResolvedSkillSource(
            root=resolved.root,
            source_type="repository_ref",
            source_uri=f"{entry.repository_id}:{entry.package_ref}",
            package_ref=entry.package_ref,
            repository_id=entry.repository_id,
        )

    async def _resolve_github_path(self, request: BundleInstallRequest) -> Path:
        github = _parse_github_source(request.source_uri, request.install_options)
        archive_url = f"https://codeload.github.com/{github['owner']}/{github['repo']}/tar.gz/{github['ref']}"
        payload = await _download(archive_url)
        checksum = _checksum_from_options(request.install_options)
        _verify_checksum(payload, checksum)
        root = self._cache_path("github_path", json.dumps(github, sort_keys=True), payload)
        _reset_dir(root)
        _extract_archive(payload, root, f"{github['repo']}.tar.gz")
        extracted = _single_child_or_self(root)
        subdir = str(github.get("path") or "").strip("/")
        bundle_root = (extracted / subdir).resolve() if subdir else _bundle_root(extracted)
        if not bundle_root.exists() or not bundle_root.is_dir():
            raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "GitHub path 不存在", status_code=422)
        return bundle_root

    def _cache_path(self, source_type: str, source_uri: str, payload: bytes) -> Path:
        digest = hashlib.sha256(
            source_type.encode("utf-8") + b"\n" + source_uri.encode("utf-8") + b"\n" + payload
        ).hexdigest()[:24]
        return (self._cache_dir / digest).resolve()


def _write_single_skill_bundle(root: Path, *, skill_md: str, request: BundleInstallRequest) -> None:
    source_name = Path(urlparse(request.source_uri).path).stem or "remote-skill"
    bundle_id = _safe_id(str(request.install_options.get("bundle_id") or source_name))
    display_name = str(request.install_options.get("display_name") or bundle_id)
    tool_name = str(request.install_options.get("default_tool") or "file.write")
    manifest = {
        "id": bundle_id,
        "bundle_revision": str(request.install_options.get("version") or "1.0.0"),
        "display_name": display_name,
        "description": "Imported single SKILL.md bundle",
        "author": "remote",
        "required_tools": [tool_name],
        "permissions": {
            "tools": [{"name": tool_name, "actions": ["write_task_artifact"], "risk": "R2"}],
            "assets": [],
        },
        "filesystem": {"allowed_roots": ["workspace://artifacts/**"]},
        "safety": {"unattended_allowed": False},
        "steps": [
            {
                "tool_name": tool_name,
                "args": {
                    "path": f"outputs/{bundle_id}.md",
                    "content": "# {skill_display_name}\n\n{content}",
                },
            }
        ],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / "bundle.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True),
        encoding="utf-8",
    )
    (root / "SKILL.md").write_text(skill_md, encoding="utf-8")


def _extract_archive(payload: bytes, target: Path, name: str) -> None:
    if len(payload) > MAX_DOWNLOAD_BYTES:
        raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "Skill 安装包过大", status_code=422)
    lowered = name.lower()
    if lowered.endswith(".zip"):
        _extract_zip(payload, target)
        return
    if lowered.endswith((".tar", ".tar.gz", ".tgz")):
        _extract_tar(payload, target)
        return
    raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "不支持的压缩包格式", status_code=422)


def _extract_zip(payload: bytes, target: Path) -> None:
    total = 0
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_FILES:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 安装包文件数过多",
                status_code=422,
            )
        for info in infos:
            if info.is_dir():
                continue
            total += int(info.file_size)
            if total > MAX_ARCHIVE_BYTES:
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "Skill 安装包解压后过大",
                    status_code=422,
                )
            dest = (target / info.filename).resolve()
            _ensure_inside(target, dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(archive.read(info))


def _extract_tar(payload: bytes, target: Path) -> None:
    total = 0
    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:*") as archive:
        members = archive.getmembers()
        if len(members) > MAX_ARCHIVE_FILES:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 安装包文件数过多",
                status_code=422,
            )
        for member in members:
            if member.isdir():
                continue
            if member.issym() or member.islnk() or not member.isfile():
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "Skill 安装包包含不安全文件",
                    status_code=422,
                )
            total += int(member.size)
            if total > MAX_ARCHIVE_BYTES:
                raise AppError(
                    ErrorCode.PLUGIN_VALIDATE_FAILED,
                    "Skill 安装包解压后过大",
                    status_code=422,
                )
            extracted = archive.extractfile(member)
            if extracted is None:
                continue
            dest = (target / member.name).resolve()
            _ensure_inside(target, dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(extracted.read())


async def _download(url: str) -> bytes:
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
        response = await client.get(url)
        if response.status_code >= 400:
            raise AppError(
                ErrorCode.PLUGIN_INSTALL_FAILED,
                "Skill 安装源下载失败",
                status_code=422,
                details={"status_code": response.status_code},
            )
        if len(response.content) > MAX_DOWNLOAD_BYTES:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "Skill 安装源下载内容过大",
                status_code=422,
            )
        return response.content


def _validate_download_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https":
        return
    raise AppError(
        ErrorCode.PLUGIN_VALIDATE_FAILED,
        "远端 Skill 安装源只允许 https",
        status_code=422,
    )


def _verify_checksum(payload: bytes, checksum: str | None) -> None:
    if not checksum:
        return
    expected = checksum.removeprefix("sha256:").lower()
    actual = hashlib.sha256(payload).hexdigest()
    if actual != expected:
        raise AppError(
            ErrorCode.PLUGIN_VALIDATE_FAILED,
            "Skill 安装源 checksum 不匹配",
            status_code=422,
        )


def _checksum_from_options(options: dict[str, Any]) -> str | None:
    value = options.get("checksum")
    return str(value) if value else None


def _parse_github_source(source_uri: str, options: dict[str, Any]) -> dict[str, str]:
    parsed = urlparse(source_uri)
    ref = str(options.get("ref") or "main")
    path = str(options.get("path") or "")
    if parsed.scheme in {"http", "https"}:
        if parsed.hostname not in {"github.com", "www.github.com"}:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "github_path 仅支持 github.com",
                status_code=422,
            )
        parts = [item for item in parsed.path.strip("/").split("/") if item]
        if len(parts) < 2:
            raise AppError(
                ErrorCode.PLUGIN_VALIDATE_FAILED,
                "github_path 缺少 owner/repo",
                status_code=422,
            )
        owner, repo = parts[0], parts[1]
        if len(parts) >= 4 and parts[2] in {"tree", "blob"}:
            ref = parts[3]
            path = "/".join(parts[4:])
        query = parse_qs(parsed.query)
        ref = str(query.get("ref", [ref])[0])
        return {"owner": owner, "repo": repo.removesuffix(".git"), "ref": ref, "path": path}
    value = source_uri.removeprefix("github:").strip("/")
    chunks = value.split("/")
    if len(chunks) < 2:
        raise AppError(
            ErrorCode.PLUGIN_VALIDATE_FAILED,
            "github_path 缺少 owner/repo",
            status_code=422,
        )
    owner, repo = chunks[0], chunks[1]
    path = path or "/".join(chunks[2:])
    return {"owner": owner, "repo": repo.removesuffix(".git"), "ref": ref, "path": path}


def _bundle_root(root: Path) -> Path:
    if (root / "bundle.yaml").exists() and (root / "SKILL.md").exists():
        return root
    child = _single_child_or_self(root)
    if (child / "bundle.yaml").exists() and (child / "SKILL.md").exists():
        return child
    return root


def _single_child_or_self(root: Path) -> Path:
    children = [path for path in root.iterdir()]
    if len(children) == 1 and children[0].is_dir():
        return children[0].resolve()
    return root


def _resolve_path(root_dir: Path, value: str) -> Path:
    path = Path(value).expanduser()
    if path.is_absolute():
        return path.resolve()
    return (root_dir / path).resolve()


def _reset_dir(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True, exist_ok=True)


def _ensure_inside(root: Path, target: Path) -> None:
    root = root.resolve()
    if root not in [target, *target.parents]:
        raise AppError(ErrorCode.PLUGIN_VALIDATE_FAILED, "Skill 安装包路径逃逸", status_code=422)


def _safe_id(value: str) -> str:
    import re

    lowered = value.strip().lower().replace("_", "-")
    return re.sub(r"[^a-z0-9.-]+", "-", lowered).strip("-") or "imported-skill"
