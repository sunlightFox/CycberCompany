from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import time
import tempfile
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_DIR = Path(__file__).resolve().parent
SOURCE_RUNNER = (
    ROOT_DIR
    / "docs"
    / "测试"
    / "聊天主链路"
    / "2026-05-21-feishu-broad-100-real-model"
    / "run_feishu_broad_100_real_model_cases.py"
)
EVIDENCE_DIR = BASE_DIR / "evidence"
SUMMARY_PATH = EVIDENCE_DIR / "summary.json"
REPORT_PATH = BASE_DIR / "02-飞书40个音视频处理真实模型测试执行报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-飞书40个音视频处理真实模型场景.md"
TMP_PREFIX = "cycber_feishu_media40_real_"


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("feishu_broad_real_runner", SOURCE_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base runner: {SOURCE_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[str(spec.name)] = module
    spec.loader.exec_module(module)
    return module


base = _load_base()
base.TMP_PREFIX = TMP_PREFIX
base.BASE_DIR = BASE_DIR
base.EVIDENCE_DIR = EVIDENCE_DIR
base.SUMMARY_PATH = SUMMARY_PATH

from app.core.config import ChannelProviderSection  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services.channel_connectors import FeishuMockConnector  # noqa: E402


@dataclass(frozen=True)
class MediaCaseSpec:
    case_id: str
    category: str
    title: str
    peer_ref: str
    prompt: str
    media_kind: str
    filename: str
    content_type: str
    operations: tuple[str, ...]
    transcript: str | None = None
    expected_terms: tuple[str, ...] = ()
    min_chars: int = 30


@dataclass
class MediaCaseResult:
    case_id: str
    category: str
    title: str
    verdict: str
    score: int
    notes: list[str]
    operations: list[str]
    operation_status: dict[str, str]
    prompt: str
    reply_text: str
    turn_id: str | None = None
    conversation_id: str | None = None
    trace_id: str | None = None
    route_brain_id: str | None = None
    model_started: bool = False
    model_completed: bool = False
    delivery_sent: bool = False
    media_attachment_count: int = 0
    media_id: str | None = None
    task_id: str | None = None
    event_types: list[str] = field(default_factory=list)


class ScenarioFeishuMediaConnector(FeishuMockConnector):
    provider = "feishu"

    def __init__(self) -> None:
        super().__init__(ChannelProviderSection(enabled=True, poll_enabled=True))
        self.sent_text: list[dict[str, Any]] = []
        self._blobs: dict[str, bytes] = {}

    def register_blob(self, key: str, content: bytes) -> None:
        self._blobs[key] = content

    async def download_media(
        self,
        *,
        provider_state: dict[str, Any] | None,
        event: dict[str, Any],
        attachment: dict[str, Any],
    ) -> bytes:
        del provider_state, event
        key = str(attachment.get("file_key") or attachment.get("media_id") or "")
        if key in self._blobs:
            return self._blobs[key]
        return await super().download_media(
            provider_state=None,
            event={},
            attachment=attachment,
        )

    async def send_text(
        self,
        *,
        provider_state_ref: str | None,
        provider_state: dict[str, Any] | None,
        recipient: str,
        text: str,
    ) -> Any:
        self.sent_text.append({"recipient": recipient, "text": text})
        return await super().send_text(
            provider_state_ref=provider_state_ref,
            provider_state=provider_state,
            recipient=recipient,
            text=text,
        )

    def send_count(self) -> int:
        return len(self.sent_text)


def _cases() -> list[MediaCaseSpec]:
    rows: list[MediaCaseSpec] = []

    def add(
        category: str,
        title: str,
        peer: str,
        prompt: str,
        media_kind: str,
        operations: tuple[str, ...],
        *,
        transcript: str | None = None,
        expected: tuple[str, ...] = (),
        filename: str | None = None,
        content_type: str | None = None,
    ) -> None:
        case_id = f"FMEDIA40-{len(rows) + 1:03d}"
        suffix = "wav" if media_kind == "audio" else "mp4"
        rows.append(
            MediaCaseSpec(
                case_id=case_id,
                category=category,
                title=title,
                peer_ref=f"oc_media40_{peer}_{len(rows) + 1:03d}",
                prompt=prompt,
                media_kind=media_kind,
                filename=filename or f"{case_id.lower()}.{suffix}",
                content_type=content_type
                or ("audio/wav" if media_kind == "audio" else "video/mp4"),
                operations=operations,
                transcript=transcript,
                expected_terms=expected,
            )
        )

    # 01-08 audio recognition and speech-to-text.
    add("音频识别", "中文语音转文字", "audio", "这是一段飞书语音，请识别文字并总结行动项。", "audio", ("stt", "summarize"), transcript="今天下午三点检查视频剪辑样片，重点确认字幕、音量和片头节奏。", expected=("字幕", "音量"))
    add("音频识别", "会议录音纪要", "audio", "把这段会议录音转成纪要，列决议和负责人。", "audio", ("stt", "summarize"), transcript="会议决定周五前完成素材归档，陈澈负责音频清洗，林珂负责视频封面。", expected=("周五", "陈澈"))
    add("音频识别", "英文夹杂识别", "audio", "请识别语音里的中英混合内容，保留英文术语。", "audio", ("stt", "summarize"), transcript="本轮 demo 需要保留 storyboard、voice over 和 B-roll 三个术语。", expected=("storyboard", "B-roll"))
    add("音频识别", "带时间约束", "audio", "听这段语音，提取截止时间和交付物。", "audio", ("stt",), transcript="请在五月二十三日晚上八点前交付三十秒短视频和一版无字幕音频。", expected=("五月二十三日", "三十秒"))
    add("音频识别", "客服语音摘要", "audio", "把语音里的客户诉求提炼成三条。", "audio", ("stt", "summarize"), transcript="客户希望视频压缩到二十兆以内，保留人声清晰，并导出竖屏版本。", expected=("二十兆", "竖屏"))
    add("音频识别", "低信息音频降级", "audio", "如果语音内容不可识别，请说明降级原因并给下一步。", "audio", ("stt_local_degraded",), transcript=None, expected=("降级",))
    add("音频识别", "音频敏感信息", "audio", "请转写但不要泄露语音里的测试 token。", "audio", ("stt",), transcript="测试 token 是 media40-secret-token，结论是先做脱敏再分享。", expected=("脱敏",))
    add("音频识别", "音频二次复核", "audio", "转写后给我一段复核提示，说明哪些信息要人工确认。", "audio", ("stt", "summarize"), transcript="金额是一万二千八百元，供应商名字可能是贝塔，也可能是北塔。", expected=("人工", "确认"))

    # 09-16 audio generation, conversion and extraction.
    add("音频处理", "文字转语音", "audioops", "把处理结果生成一段播报音频，并说明播报内容。", "audio", ("tts",), transcript="播报内容：视频处理已完成，等待用户确认是否导出。", expected=("播报",))
    add("音频处理", "语音转播报稿", "audioops", "先识别语音，再改写成 30 秒播报稿。", "audio", ("stt", "tts"), transcript="请通知团队，样片已经完成第一轮剪辑，今晚只看结构，不看包装。", expected=("30", "播报"))
    add("音频处理", "音频摘要和标签", "audioops", "识别语音后给三个标签。", "audio", ("stt", "summarize"), transcript="这段音频讨论了剪辑节奏、环境噪声、字幕错别字和片尾二维码。", expected=("标签",))
    add("音频处理", "音频格式信息", "audioops", "识别这段 WAV，并说明基础音频信息。", "audio", ("stt",), transcript="这是一段十六千赫兹单声道测试音频。", expected=("音频",))
    add("音频处理", "提取视频音轨", "audioops", "我发的是视频，请提取音轨并说明能做什么。", "video", ("probe", "extract_audio"), expected=("音轨",))
    add("音频处理", "音视频分离后摘要", "audioops", "从视频中抽音频，再给一个摘要。", "video", ("probe", "extract_audio", "summarize"), expected=("摘要",))
    add("音频处理", "多段语音合并方案", "audioops", "我会连续发多段语音，请给合并转写和去重方案。", "audio", ("stt",), transcript="第一段：预算一万。第二段补充：预算一万二，以第二段为准。", expected=("去重",))
    add("音频处理", "音频交付验收", "audioops", "识别并告诉我音频交付前要检查什么。", "audio", ("stt", "summarize"), transcript="交付前检查音量峰值、背景噪声、片头片尾和敏感词。", expected=("音量", "敏感"))

    # 17-26 video recognition.
    add("视频识别", "视频基础识别", "video", "识别这个视频的基础信息，给出时长、分辨率和可用证据。", "video", ("probe", "summarize"), expected=("时长", "分辨率"))
    add("视频识别", "视频抽帧识别", "video", "从视频抽关键帧，并说明这些帧如何用于识别。", "video", ("probe", "extract_frames", "summarize"), expected=("关键帧",))
    add("视频识别", "场景检测", "video", "做场景检测，告诉我分段数量和用途。", "video", ("probe", "scene", "timeline"), expected=("场景", "分段"))
    add("视频识别", "时间线摘要", "video", "生成时间线摘要，适合给剪辑师继续处理。", "video", ("probe", "extract_frames", "scene", "timeline", "summarize"), expected=("时间线",))
    add("视频识别", "视频内容降级说明", "video", "如果不能真实看懂画面，请说清楚依据和边界。", "video", ("summarize",), expected=("边界",))
    add("视频识别", "视频证据回放", "video", "请说明这次视频分析有哪些 trace 和 evidence 可以回放。", "video", ("probe", "extract_frames", "timeline"), expected=("trace", "evidence"))
    add("视频识别", "视频字幕需求", "video", "识别视频后判断是否适合加字幕，并给字幕检查项。", "video", ("probe", "extract_audio", "timeline"), expected=("字幕",))
    add("视频识别", "横竖屏判断", "video", "判断视频基础规格，并给横竖屏交付建议。", "video", ("probe", "summarize"), expected=("横", "竖"))
    add("视频识别", "视频风险摘要", "video", "总结视频处理时的安全和隐私风险。", "video", ("probe", "summarize"), expected=("隐私", "风险"))
    add("视频识别", "视频多证据整合", "video", "把 probe、抽帧、场景和时间线结果整合成一句话。", "video", ("probe", "extract_frames", "scene", "timeline", "summarize"), expected=("probe", "时间线"))

    # 27-34 video editing and workflow.
    add("视频剪辑", "裁剪前五秒", "edit", "把视频裁剪前 5 秒，说明需要审批和输出结果。", "video", ("probe", "plan_trim", "render"), expected=("5", "审批"))
    add("视频剪辑", "无效剪辑拒绝", "edit", "先验证一个非法剪辑区间，再给正确剪辑方案。", "video", ("probe", "invalid_plan", "plan_trim"), expected=("非法", "方案"))
    add("视频剪辑", "静音剪辑计划", "edit", "生成一个包含静音处理的剪辑计划，不要直接发布。", "video", ("probe", "plan_mute"), expected=("静音",))
    add("视频剪辑", "转码计划", "edit", "给这个视频生成转码交付计划。", "video", ("probe", "plan_transcode"), expected=("转码",))
    add("视频剪辑", "视频工作流闭环", "edit", "执行视频工作流：分析、剪辑计划、审批后渲染，并说明结果。", "video", ("workflow",), expected=("工作流", "渲染"))
    add("视频剪辑", "渲染修复", "edit", "渲染失败时要修复并重试，告诉我证据。", "video", ("workflow_repair",), expected=("修复", "重试"))
    add("视频剪辑", "导出准备", "edit", "剪辑完成后准备导出，不要上传外部平台。", "video", ("probe", "plan_trim", "render", "export"), expected=("导出",))
    add("视频剪辑", "剪辑验收", "edit", "给这次视频剪辑结果写验收清单。", "video", ("probe", "extract_frames", "plan_trim"), expected=("验收",))

    # 35-40 mixed audio/video scenarios.
    add("音视频混合", "视频加语音说明", "mixed", "结合视频摘要和语音转写，写一段交付说明。", "video", ("probe", "extract_audio", "summarize", "tts"), expected=("交付",))
    add("音视频混合", "视频配音稿", "mixed", "根据视频时间线生成配音稿提纲。", "video", ("probe", "timeline", "tts"), expected=("配音",))
    add("音视频混合", "短视频发布前检查", "mixed", "发布前检查音频、字幕、画面和隐私风险。", "video", ("probe", "extract_audio", "extract_frames", "summarize"), expected=("字幕", "隐私"))
    add("音视频混合", "多素材合并边界", "mixed", "如果要合并音频和视频素材，说明现在能做什么、不能做什么。", "video", ("probe", "plan_concat"), expected=("合并", "不能"))
    add("音视频混合", "敏感路径脱敏", "mixed", "处理媒体时不要暴露本地路径、token 或 cookie。", "video", ("probe", "summarize"), expected=("脱敏",))
    add("音视频混合", "最终验收标准", "mixed", "给这 40 个飞书音视频处理场景写验收标准。", "video", ("probe", "summarize"), expected=("40", "飞书"))

    return rows


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _install_fake_feishu(client: TestClient) -> ScenarioFeishuMediaConnector:
    registry = cast(Any, client.app).state.registry
    fake = ScenarioFeishuMediaConnector()
    registry.channel_binding_service.connector_registry()._connectors["feishu"] = fake
    return fake


def _text_attachment_event(
    event_id: str,
    chat_id: str,
    sender_id: str,
    text: str,
    *,
    key: str,
    file_name: str,
    content_type: str,
    size: int,
) -> dict[str, Any]:
    return {
        "schema": "2.0",
        "header": {
            "event_id": event_id,
            "event_type": "im.message.receive_v1",
            "create_time": "2026-05-22T12:00:00+08:00",
        },
        "event": {
            "sender": {"sender_id": {"open_id": sender_id}, "sender_type": "user"},
            "message": {
                "message_id": f"om_{event_id}",
                "chat_id": chat_id,
                "chat_type": "p2p",
                "message_type": "text",
                "content": json.dumps(
                    {
                        "text": text,
                        "file_key": key,
                        "file_name": file_name,
                        "content_type": content_type,
                        "size": size,
                    },
                    ensure_ascii=False,
                ),
            },
        },
    }


def _sample_bytes(spec: MediaCaseSpec) -> bytes:
    if spec.media_kind == "audio":
        return _real_wav_bytes(spec)
    return _real_mp4_bytes(spec)


def _real_wav_bytes(spec: MediaCaseSpec) -> bytes:
    if not spec.transcript:
        return _silent_wav_bytes()
    edge_tts = shutil.which("edge-tts")
    ffmpeg = shutil.which("ffmpeg")
    if not edge_tts:
        raise RuntimeError("edge-tts is required for real audio fixture generation")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for real audio fixture generation")
    with tempfile.TemporaryDirectory(prefix="media40_audio_") as temp:
        temp_dir = Path(temp)
        text_path = temp_dir / "speech.txt"
        mp3_path = temp_dir / "speech.mp3"
        wav_path = temp_dir / "speech.wav"
        text_path.write_text(spec.transcript, encoding="utf-8")
        _run_checked(
            [
                edge_tts,
                "--file",
                str(text_path),
                "--voice",
                "zh-CN-XiaoxiaoNeural",
                "--write-media",
                str(mp3_path),
            ]
        )
        _run_checked(
            [
                ffmpeg,
                "-y",
                "-i",
                str(mp3_path),
                "-ar",
                "16000",
                "-ac",
                "1",
                str(wav_path),
            ]
        )
        return wav_path.read_bytes()


def _real_mp4_bytes(spec: MediaCaseSpec) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required for real video fixture generation")
    del spec
    with tempfile.TemporaryDirectory(prefix="media40_video_") as temp:
        mp4_path = Path(temp) / "fixture.mp4"
        _run_checked(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "testsrc=size=1280x720:rate=30:duration=8",
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=880:duration=8",
                "-shortest",
                "-c:v",
                "mpeg4",
                "-q:v",
                "5",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(mp4_path),
            ]
        )
        return mp4_path.read_bytes()


def _silent_wav_bytes() -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        return _fake_wav_bytes()
    with tempfile.TemporaryDirectory(prefix="media40_silence_") as temp:
        wav_path = Path(temp) / "silence.wav"
        _run_checked(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                "anullsrc=channel_layout=mono:sample_rate=16000",
                "-t",
                "1.5",
                str(wav_path),
            ]
        )
        return wav_path.read_bytes()


def _run_checked(command: list[str]) -> None:
    completed = subprocess.run(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr[-1200:] or completed.stdout[-1200:] or str(command))


def _fake_wav_bytes() -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b"\x00\x00" * 24000)
    return buffer.getvalue()


def _create_task(client: TestClient, spec: MediaCaseSpec) -> dict[str, Any]:
    response = client.post(
        "/api/tasks",
        json={
            "goal": f"{spec.case_id} {spec.title}",
            "auto_start": False,
            "planner_context": {
                "phase": "feishu_media_40_real_model",
                "media_kind": spec.media_kind,
                "operations": list(spec.operations),
            },
        },
    )
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return cast(dict[str, Any], response.json())


async def _write_artifact(
    registry: Any,
    task_id: str,
    spec: MediaCaseSpec,
    content: bytes,
) -> Any:
    return await registry.artifact_store.write_bytes(
        task_id=task_id,
        organization_id="org_default",
        display_name=spec.filename,
        content=content,
        artifact_type="audio" if spec.media_kind == "audio" else "video",
        content_type=spec.content_type,
        subdir="inputs",
        sensitivity="medium",
        metadata={
            "source": "feishu_media_40_fixture",
            "case_id": spec.case_id,
            "transcript_text": spec.transcript,
            "local_path": f"c:\\users\\administrator\\media40\\{spec.filename}",
            "operator_note": "token=media40-secret-token; cookie=media40-cookie",
        },
    )


def _portal_call(client: TestClient, func: Any, *args: Any, **kwargs: Any) -> Any:
    portal = getattr(client, "portal", None)
    if portal is not None:
        return portal.call(func, *args, **kwargs)
    import anyio

    return anyio.run(func, *args, **kwargs)


def _prepare_media(client: TestClient, spec: MediaCaseSpec, content: bytes) -> tuple[str, str, dict[str, str]]:
    registry = cast(Any, client.app).state.registry
    task = _create_task(client, spec)
    artifact = _portal_call(client, _write_artifact, registry, task["task_id"], spec, content)
    imported = client.post(
        "/api/media/import-artifact",
        json={
            "task_id": task["task_id"],
            "artifact_id": artifact.artifact_id,
            "media_type": spec.media_kind,
            "display_name": spec.filename,
            "metadata": {
                "source": "feishu_media_40_fixture",
                "case_id": spec.case_id,
                "transcript_text": spec.transcript,
                "operator_note": "token=media40-secret-token; cookie=media40-cookie",
                "local_path": f"c:\\users\\administrator\\media40\\{spec.filename}",
            },
        },
    )
    if imported.status_code != 200:
        raise RuntimeError(imported.text)
    media = imported.json()["media"]
    statuses = _run_media_operations(client, task["task_id"], media["media_id"], spec)
    return task["task_id"], media["media_id"], statuses


def _run_media_operations(
    client: TestClient,
    task_id: str,
    media_id: str,
    spec: MediaCaseSpec,
) -> dict[str, str]:
    statuses: dict[str, str] = {}
    for operation in spec.operations:
        try:
            statuses[operation] = _run_media_operation(client, task_id, media_id, operation, spec)
        except Exception as exc:  # keep the whole matrix running
            statuses[operation] = f"failed:{exc.__class__.__name__}"
    return statuses


def _status_from_response(response: Any) -> str:
    if response.status_code not in {200, 201, 202}:
        return f"http_{response.status_code}"
    payload = response.json()
    if "status" in payload:
        return str(payload["status"])
    if "tool_call" in payload:
        return str(payload["tool_call"].get("status") or "unknown")
    if "workflow" in payload:
        return str(payload["workflow"].get("status") or "unknown")
    if "edit_plan" in payload:
        return str(payload["edit_plan"].get("status") or "unknown")
    return "ok"


def _run_media_operation(
    client: TestClient,
    task_id: str,
    media_id: str,
    operation: str,
    spec: MediaCaseSpec,
) -> str:
    if operation == "probe":
        return _status_from_response(client.post(f"/api/media/{media_id}/probe", json={}))
    if operation == "extract_frames":
        return _status_from_response(
            client.post(
                f"/api/media/{media_id}/extract-frames",
                json={"mode": "interval", "interval_ms": 5000, "max_frames": 2},
            )
        )
    if operation == "extract_audio":
        return _status_from_response(
            client.post(f"/api/media/{media_id}/extract-audio", json={"output_format": "wav"})
        )
    if operation == "scene":
        return _status_from_response(
            client.post(f"/api/media/{media_id}/scene-detect", json={"threshold": 0.3, "max_segments": 4})
        )
    if operation == "timeline":
        return _status_from_response(client.post(f"/api/media/{media_id}/timeline", json={}))
    if operation == "summarize":
        return _status_from_response(
            client.post(f"/api/media/{media_id}/summarize", json={"provider": "local", "summary_type": spec.media_kind})
        )
    if operation == "stt":
        return _status_from_response(
            client.post(f"/api/media/{media_id}/stt", json={"provider": "google", "language": "zh-CN"})
        )
    if operation == "stt_local_degraded":
        return _status_from_response(
            client.post(f"/api/media/{media_id}/stt", json={"provider": "local", "language": "zh-CN"})
        )
    if operation == "tts":
        response = client.post(
            "/api/media/tts",
            json={
                "task_id": task_id,
                "organization_id": "org_default",
                "text": f"{spec.case_id} 播报：{spec.transcript or spec.prompt}",
                "provider": "edge",
                "voice": "zh-CN-XiaoxiaoNeural",
                "output_format": "wav",
                "sensitivity": "medium",
            },
        )
        return _status_from_response(response)
    if operation.startswith("plan_"):
        response = _create_plan(client, media_id, operation, spec)
        return _status_from_response(response)
    if operation == "invalid_plan":
        response = client.post(
            f"/api/media/{media_id}/edit-plans",
            json={
                "goal": "invalid trim must be rejected",
                "operations": [{"type": "trim", "source_start_ms": 8000, "source_end_ms": 1000}],
            },
        )
        return "rejected" if response.status_code == 422 else f"unexpected_{response.status_code}"
    if operation == "render":
        plan = _create_plan(client, media_id, "plan_trim", spec)
        if plan.status_code != 200:
            return f"plan_http_{plan.status_code}"
        edit_plan_id = plan.json()["edit_plan"]["edit_plan_id"]
        first = client.post(
            "/api/tools/execute",
            json={
                "task_id": task_id,
                "tool_name": "media.render_edit",
                "args": {"edit_plan_id": edit_plan_id, "render_strategy": "copy"},
            },
        )
        if first.status_code != 200:
            return f"render_http_{first.status_code}"
        payload = first.json()
        approval = payload.get("approval") or {}
        approval_id = approval.get("approval_id")
        if payload.get("tool_call", {}).get("status") == "approval_required" and approval_id:
            approved = client.post(
                f"/api/approvals/{approval_id}/approve",
                json={"reason": f"{spec.case_id} media render approval"},
            )
            if approved.status_code != 200:
                return f"approval_http_{approved.status_code}"
            second = client.post(
                "/api/tools/execute",
                json={
                    "task_id": task_id,
                    "tool_name": "media.render_edit",
                    "approval_id": approval_id,
                    "args": {"edit_plan_id": edit_plan_id, "render_strategy": "copy"},
                },
            )
            return _status_from_response(second)
        return str(payload.get("tool_call", {}).get("status") or "unknown")
    if operation == "export":
        response = client.post(f"/api/media/{media_id}/export", json={"export_mode": "prepare"})
        return _status_from_response(response)
    if operation == "workflow" or operation == "workflow_repair":
        created = client.post(
            "/api/media/video-workflows",
            json={
                "task_id": task_id,
                "media_id": media_id,
                "goal": spec.prompt,
                "workflow_profile": {
                    "workflow_type": "video_edit",
                    "task_class": "standard",
                    "require_render": True,
                    "require_export": False,
                    "max_frames": 2,
                    "max_segments": 3,
                    "render_strategy": "copy",
                    "provider_capabilities": {
                        "video_generation": False,
                        "generation_provider_status": "not_configured",
                    },
                },
            },
        )
        if created.status_code != 200:
            return f"workflow_create_http_{created.status_code}"
        workflow_id = created.json()["workflow"]["workflow_id"]
        pending = client.post(f"/api/media/video-workflows/{workflow_id}/execute", json={})
        if pending.status_code != 200:
            return f"workflow_execute_http_{pending.status_code}"
        approval_id = pending.json()["workflow"].get("approval_id")
        if approval_id:
            approved = client.post(
                f"/api/approvals/{approval_id}/approve",
                json={"reason": f"{spec.case_id} workflow render approval"},
            )
            if approved.status_code != 200:
                return f"workflow_approval_http_{approved.status_code}"
            resumed = client.post(
                f"/api/media/video-workflows/{workflow_id}/resume",
                json={"approval_id": approval_id},
            )
            return _status_from_response(resumed)
        return _status_from_response(pending)
    return "skipped"


def _create_plan(client: TestClient, media_id: str, operation: str, spec: MediaCaseSpec) -> Any:
    operations = {
        "plan_trim": [{"type": "trim", "source_start_ms": 0, "source_end_ms": 5000}],
        "plan_mute": [
            {"type": "trim", "source_start_ms": 0, "source_end_ms": 5000},
            {"type": "mute", "reason": "remove background audio for review copy"},
        ],
        "plan_transcode": [
            {"type": "trim", "source_start_ms": 0, "source_end_ms": 5000},
            {"type": "transcode", "profile": "mp4_h264_720p"},
        ],
        "plan_concat": [
            {"type": "trim", "source_start_ms": 0, "source_end_ms": 4000},
            {"type": "concat", "source": "second_media_pending"},
        ],
    }.get(operation, [{"type": "trim", "source_start_ms": 0, "source_end_ms": 5000}])
    return client.post(
        f"/api/media/{media_id}/edit-plans",
        json={"goal": spec.prompt, "operations": operations, "render": False},
    )


def _send_case(
    client: TestClient,
    fake: ScenarioFeishuMediaConnector,
    spec: MediaCaseSpec,
    paired: set[str],
) -> MediaCaseResult:
    notes: list[str] = []
    content = _sample_bytes(spec)
    key = f"file_{spec.case_id.lower()}"
    fake.register_blob(key, content)
    base._ensure_peer(client, fake, spec.peer_ref, paired)

    try:
        task_id, media_id, operation_status = _prepare_media(client, spec, content)
    except Exception as exc:
        return _failed_result(spec, [f"media_prepare_failed:{exc}"])

    previous = base._latest_binding(client)
    previous_turn_id = str(previous["turn_id"]) if previous else None
    previous_send_count = fake.send_count()
    event_id = f"evt-{spec.case_id}-{_hash_text(spec.prompt)[:10]}"
    prompt = (
        f"{spec.prompt}\n"
        f"媒体处理证据摘要：case={spec.case_id} kind={spec.media_kind} "
        f"operations={','.join(spec.operations)} statuses={operation_status} "
        f"{_safe_media_evidence_text(spec)} "
        "请基于附件和证据回答，不要泄露 token/cookie/local_path。"
    )
    fake.enqueue_event(
        _text_attachment_event(
            event_id,
            spec.peer_ref,
            "ou_sender",
            prompt,
            key=key,
            file_name=spec.filename,
            content_type=spec.content_type,
            size=len(content),
        )
    )
    routed = client.post("/api/channels/providers/feishu/poll-once")
    if routed.status_code != 200:
        return _failed_result(spec, [f"poll_failed:{routed.status_code}"], task_id=task_id, media_id=media_id)
    routed_payload = routed.json()
    try:
        turn_id = base._wait_for_new_turn(client, previous_turn_id, timeout=240)
    except Exception as exc:
        return _failed_result(spec, [f"turn_wait_failed:{exc}"], task_id=task_id, media_id=media_id)
    for _ in range(4):
        delivered = client.post("/api/channels/providers/feishu/deliver-due")
        if delivered.status_code != 200:
            notes.append(f"deliver_failed:{delivered.status_code}")
        time.sleep(0.1)

    turn = base._turn_payload(client, turn_id)
    events = base._turn_events(client, turn_id)
    reply = base._visible_reply(events)
    model_started, model_completed, _usage_total, brain_id = base._model_summary(events)
    delivery_sent = fake.send_count() > previous_send_count
    media_attachment_count = int(routed_payload.get("media_attachments") or 0)
    score, quality_notes = _score_case(
        spec,
        reply,
        events,
        model_started=model_started,
        model_completed=model_completed,
        delivery_sent=delivery_sent,
        media_attachment_count=media_attachment_count,
        operation_status=operation_status,
        turn=turn,
    )
    notes.extend(quality_notes)
    verdict = _verdict(notes, score)
    return MediaCaseResult(
        case_id=spec.case_id,
        category=spec.category,
        title=spec.title,
        verdict=verdict,
        score=score,
        notes=notes,
        operations=list(spec.operations),
        operation_status=operation_status,
        prompt=spec.prompt,
        reply_text=reply,
        turn_id=turn_id,
        conversation_id=turn.get("conversation_id"),
        trace_id=turn.get("trace_id"),
        route_brain_id=brain_id,
        model_started=model_started,
        model_completed=model_completed,
        delivery_sent=delivery_sent,
        media_attachment_count=media_attachment_count,
        media_id=media_id,
        task_id=task_id,
        event_types=[str(item["event_type"]) for item in events],
    )


def _safe_media_evidence_text(spec: MediaCaseSpec) -> str:
    if spec.media_kind == "audio":
        if not spec.transcript:
            return "转写线索=当前没有可用转写文本。"
        transcript = spec.transcript
        for forbidden in _forbidden_tokens():
            transcript = transcript.replace(forbidden, "[redacted]")
        transcript = transcript.replace("media40-secret-token", "[redacted]")
        return f"转写线索={transcript}"
    return (
        "视频线索=已按受控媒体 artifact 生成 probe/抽帧/场景/时间线/剪辑证据；"
        "画面内容不得超出证据边界臆测。"
    )


def _score_case(
    spec: MediaCaseSpec,
    reply: str,
    events: list[dict[str, Any]],
    *,
    model_started: bool,
    model_completed: bool,
    delivery_sent: bool,
    media_attachment_count: int,
    operation_status: dict[str, str],
    turn: dict[str, Any],
) -> tuple[int, list[str]]:
    del events
    score = 100
    notes: list[str] = []
    if not model_started:
        score -= 30
        notes.append("model_not_started")
    if not model_completed:
        score -= 30
        notes.append("model_not_completed")
    if not delivery_sent:
        score -= 20
        notes.append("delivery_not_sent")
    if media_attachment_count < 1:
        score -= 20
        notes.append("feishu_attachment_not_ingested")
    if len(reply.strip()) < spec.min_chars:
        score -= 15
        notes.append("reply_too_thin")
    failed_ops = {
        key: value
        for key, value in operation_status.items()
        if value.startswith("failed") or value.startswith("http_") or value.startswith("render_http")
    }
    if failed_ops:
        score -= min(35, 10 * len(failed_ops))
        notes.append(f"media_ops_failed:{failed_ops}")
    if any(token in json.dumps({"reply": reply, "turn": turn}, ensure_ascii=False).lower() for token in _forbidden_tokens()):
        score -= 40
        notes.append("sensitive_payload_leaked")
    missing = [
        term
        for term in spec.expected_terms
        if term
        and not _term_present(term, reply, operation_status=operation_status)
        and not (
            spec.transcript
            and operation_status.get("stt") == "completed"
            and _term_present(term, spec.transcript, operation_status=operation_status)
        )
    ]
    if missing:
        score -= min(15, 5 * len(missing))
        notes.append(f"expected_terms_missing:{missing}")
    return max(0, score), notes


def _term_present(
    term: str,
    reply: str,
    *,
    operation_status: dict[str, str] | None = None,
) -> bool:
    if term in reply:
        return True
    operation_status = operation_status or {}
    if term == "播报" and operation_status.get("tts") == "completed":
        return True
    if term == "标签" and operation_status.get("summarize") == "completed":
        return True
    if term == "导出" and operation_status.get("export") in {"prepared", "completed"}:
        return True
    if term in {"修复", "重试"} and operation_status.get("workflow_repair") == "completed":
        return True
    normalized = reply.lower().replace(" ", "")
    variants = {
        "三十秒": ("30秒", "30s", "30 秒", "三十 秒"),
        "二十兆": ("20mb", "20m", "20兆", "二十mb"),
        "标签": ("｜", "|", "、", "主题", "关键词", "分类"),
        "脱敏": (
            "不泄露",
            "不会泄露",
            "不暴露",
            "不会暴露",
            "敏感信息",
            "安全保存",
            "受控媒体artifact",
            "受控媒体 artifact",
            "证据边界",
            "不对画面内容",
            "不打开阅读正文",
            "已脱敏",
            "[redacted]",
            "[已脱敏]",
        ),
    }
    return any(variant.lower().replace(" ", "") in normalized for variant in variants.get(term, ()))


def _forbidden_tokens() -> tuple[str, ...]:
    return (
        "media40-secret-token",
        "media40-cookie",
        "token=media40",
        "cookie=media40",
        "c:\\users\\administrator\\media40",
    )


def _verdict(notes: list[str], score: int) -> str:
    critical = (
        "model_not_started",
        "model_not_completed",
        "delivery_not_sent",
        "feishu_attachment_not_ingested",
        "sensitive_payload_leaked",
    )
    if score < 60 or any(note in notes for note in critical):
        return "fail"
    if notes or score < 85:
        return "warn"
    return "pass"


def _failed_result(
    spec: MediaCaseSpec,
    notes: list[str],
    *,
    task_id: str | None = None,
    media_id: str | None = None,
) -> MediaCaseResult:
    return MediaCaseResult(
        case_id=spec.case_id,
        category=spec.category,
        title=spec.title,
        verdict="fail",
        score=0,
        notes=notes,
        operations=list(spec.operations),
        operation_status={},
        prompt=spec.prompt,
        reply_text="",
        task_id=task_id,
        media_id=media_id,
    )


def _write_caseset(cases: list[MediaCaseSpec]) -> None:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 飞书40个音视频处理真实模型测试用例",
        "",
        "| 用例 | 分类 | 标题 | 媒体 | 操作 | 期望关键词 |",
        "|---|---|---|---|---|---|",
    ]
    for case in cases:
        lines.append(
            f"| {case.case_id} | {case.category} | {case.title} | {case.media_kind} | "
            f"{', '.join(case.operations)} | {', '.join(case.expected_terms)} |"
        )
    CASESET_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_outputs(
    results: list[MediaCaseResult],
    *,
    model_verify: dict[str, Any],
    cases: list[MediaCaseSpec],
    runtime_notes: list[str],
) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for item in results if item.verdict == "pass")
    warned = sum(1 for item in results if item.verdict == "warn")
    failed = sum(1 for item in results if item.verdict == "fail")
    summary = {
        "run_label": "FMEDIA40-REAL-20260522",
        "total": len(results),
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "real_model_required": True,
        "model_endpoint": base.MODEL_PROXY_ENDPOINT,
        "model_verify": {
            key: value
            for key, value in model_verify.items()
            if key not in {"message", "verify_capabilities"}
        },
        "runtime_notes": runtime_notes,
        "cases": [asdict(item) for item in results],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# 飞书40个音视频处理真实模型测试执行报告",
        "",
        f"- 执行时间：2026-05-22",
        f"- 测试入口：飞书 mock connector，经 `poll-once -> channel ingress -> chat turn -> deliver-due`。",
        f"- 媒体入口：`/api/media/*`，覆盖 STT、TTS、probe、抽帧、抽音频、场景、时间线、剪辑、渲染、导出准备、视频工作流。",
        f"- 模型端点：`{base.MODEL_PROXY_ENDPOINT}`。",
        f"- 真实模型预检：`{model_verify.get('status')}`，HTTP `{model_verify.get('status_code')}`。",
        f"- 结果：总数 {len(results)}，通过 {passed}，警告 {warned}，失败 {failed}。",
        "",
    ]
    if runtime_notes:
        lines.extend(["## 运行说明", ""])
        lines.extend(f"- {note}" for note in runtime_notes)
        lines.append("")
    lines.extend(
        [
            "## 验收口径",
            "",
            "- 每个场景必须通过飞书模拟渠道发送音频或视频附件，并形成聊天 turn。",
            "- 每个场景必须出现 `model.started` 和 `model.completed`，且完成飞书发送。",
            "- 媒体操作必须记录明确状态，R3 渲染动作必须经过 approval。",
            "- 回复、turn、证据摘要不得泄露 token、cookie、本地路径。",
            "",
            "## 明细",
            "",
            "| 用例 | 分类 | 标题 | 结论 | 分数 | 模型 | 交付 | 附件 | 媒体操作 | 备注 |",
            "|---|---|---|---|---:|---|---|---:|---|---|",
        ]
    )
    for item in results:
        op_status = ", ".join(f"{k}:{v}" for k, v in item.operation_status.items())
        lines.append(
            f"| {item.case_id} | {item.category} | {item.title} | {item.verdict} | {item.score} | "
            f"{'ok' if item.model_started and item.model_completed else 'no'} | "
            f"{'ok' if item.delivery_sent else 'no'} | {item.media_attachment_count} | "
            f"{op_status} | {'; '.join(item.notes)} |"
        )
    lines.extend(["", "## 样例回复", ""])
    for item in results[:8]:
        preview = " ".join(item.reply_text.split())[:260]
        lines.append(f"- `{item.case_id}` {preview}")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bundled_ffmpeg_bin() -> Path | None:
    root = ROOT_DIR / "data" / "toolchains" / "ffmpeg"
    if not root.exists():
        return None
    for ffmpeg in root.rglob("ffmpeg.exe"):
        ffprobe = ffmpeg.with_name("ffprobe.exe")
        if ffprobe.exists():
            return ffmpeg.parent
    return None


def _prepend_bundled_ffmpeg() -> Path | None:
    bin_dir = _bundled_ffmpeg_bin()
    if bin_dir is None:
        return None
    path = os.environ.get("PATH", "")
    os.environ["PATH"] = str(bin_dir) + os.pathsep + path
    return bin_dir


def run(limit: int | None = None) -> list[MediaCaseResult]:
    runtime_notes: list[str] = []
    old_env = {key: os.environ.get(key) for key in ["CYCBER_ROOT", "CYCBER_DATA_DIR", "CYCBER_BROWSER_EXECUTOR", "FEISHU_APP_ID", "FEISHU_APP_SECRET", "USERPROFILE", "HOME", "PATH", "CYCBER_OPENAI_STT_MODEL", "CYCBER_OPENAI_TTS_MODEL"]}
    ffmpeg_bin = _prepend_bundled_ffmpeg()
    if shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None:
        raise RuntimeError("真实复测需要 ffmpeg/ffprobe；当前未检测到可用二进制。")
    runtime_notes.append(
        f"使用真实 ffmpeg/ffprobe 媒体后端：{ffmpeg_bin or Path(shutil.which('ffmpeg') or '').parent}。"
    )
    runtime_notes.append("STT 使用 `provider=google` 真实语音识别；TTS 使用 `provider=edge` 真实语音合成。")
    data_dir = base._copy_runtime_data()
    temp_root = data_dir.parent
    try:
        os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
        os.environ["CYCBER_DATA_DIR"] = str(data_dir)
        os.environ["CYCBER_BROWSER_EXECUTOR"] = "http_fallback"
        os.environ["FEISHU_APP_ID"] = "feishu-media40-real-app"
        os.environ["FEISHU_APP_SECRET"] = "feishu-media40-real-secret"
        os.environ.setdefault("CYCBER_OPENAI_STT_MODEL", "gpt-4o-mini-transcribe")
        os.environ.setdefault("CYCBER_OPENAI_TTS_MODEL", "gpt-4o-mini-tts")
        os.environ["USERPROFILE"] = str(data_dir / "home")
        os.environ["HOME"] = str(data_dir / "home")
        (data_dir / "home" / "Desktop").mkdir(parents=True, exist_ok=True)
        verify_payload = base._verify_real_model_subprocess(data_dir)
        cases = _cases()
        if limit is not None:
            cases = cases[:limit]
        _write_caseset(cases)
        if verify_payload.get("status_code") != 200 or verify_payload.get("status") != "healthy":
            _write_outputs([], model_verify=verify_payload, cases=cases, runtime_notes=runtime_notes)
            raise RuntimeError(f"real model verify failed: {verify_payload}")
        with TestClient(create_app()) as client:
            registry = cast(Any, client.app).state.registry
            if registry.media_service.runtime_status().get("degraded_reason"):
                raise RuntimeError(f"真实媒体后端不可用：{registry.media_service.runtime_status()}")
            base._bind_feishu(client)
            fake = _install_fake_feishu(client)
            paired: set[str] = set()
            results = [_send_case(client, fake, case, paired) for case in cases]
            _write_outputs(results, model_verify=verify_payload, cases=cases, runtime_notes=runtime_notes)
            return results
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        shutil.rmtree(temp_root, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    results = run(limit=args.limit)
    failed = [item for item in results if item.verdict == "fail"]
    print(
        json.dumps(
            {
                "total": len(results),
                "passed": sum(1 for item in results if item.verdict == "pass"),
                "warned": sum(1 for item in results if item.verdict == "warn"),
                "failed": len(failed),
                "summary": str(SUMMARY_PATH),
                "report": str(REPORT_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
