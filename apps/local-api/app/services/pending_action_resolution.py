from __future__ import annotations

import re
from typing import Any

_URL_RE = re.compile(r"https?://[^\s，。；;）)]+", re.IGNORECASE)


def hard_block_reason(text: str) -> str | None:
    lowered = text.lower()
    if "169.254.169.254" in lowered or "metadata" in lowered:
        return "metadata_or_private_network_target"
    if "file://" in lowered:
        return "browser_file_url_denied"
    if any(marker in lowered for marker in ["private key", "private_key", "mnemonic"]):
        return "secret_or_key_request"
    if any(marker in text for marker in ["私钥", "助记词", "系统密钥"]):
        return "secret_or_key_request"
    return None


def looks_like_resolution(text: str) -> bool:
    text = control_text(text)
    if looks_like_new_action_request(text):
        return False
    return (
        is_confirm(text)
        or is_deny(text)
        or is_edit(text)
        or is_session_allow(text)
        or is_always_allow(text)
    )


def is_confirm(text: str) -> bool:
    text = control_text(text)
    normalized_cn = text.strip().strip("。?!！？~，,；; ")
    compact_cn = re.sub(r"[\s，,。.!！?？；;~]+", "", text)
    if normalized_cn in {"确认", "同意", "允许", "只允许这一次", "本次允许"}:
        return True
    if compact_cn in {"确认继续", "同意继续", "允许继续", "确认执行", "确认操作"}:
        return True
    if any(marker in text for marker in ["确认这次", "确认本次", "确认下载", "确认继续", "确认执行", "确认操作"]):
        return True
    normalized = text.strip().strip("。?!！？~")
    explicit_markers = [
        "确认下载",
        "确认这次",
        "确认执行",
        "确认继续",
        "确认操作",
        "只允许这一次",
        "本次允许",
        "允许这一回",
    ]
    return normalized in {"确认", "同意", "允许"} or any(
        marker in text for marker in explicit_markers
    )


def is_session_allow(text: str) -> bool:
    text = control_text(text)
    return "本会话" in text and any(marker in text for marker in ["允许", "同类", "都可以"])


def is_always_allow(text: str) -> bool:
    text = control_text(text)
    return any(marker in text for marker in ["总是允许", "以后都允许", "永久允许"])


def is_deny(text: str) -> bool:
    text = control_text(text)
    normalized_cn = text.strip().strip("。?!！？~，,；; ")
    if normalized_cn in {"拒绝", "取消", "不允许", "不要删除", "不要执行", "停止", "不用了"}:
        return True
    if any(marker in text for marker in ["拒绝这次", "取消这次", "取消本次", "拒绝本次", "不允许这次", "停止这次", "算了"]):
        return True
    normalized = text.strip().strip("。?!！？~")
    exact = {"拒绝", "取消", "不允许", "不要删除", "不要执行", "停止", "不用了"}
    contextual = [
        "拒绝这次",
        "取消这次",
        "取消本次",
        "拒绝本次",
        "不允许这次",
        "停止这次",
        "算了",
    ]
    return normalized in exact or any(marker in text for marker in contextual)


def is_edit(text: str) -> bool:
    text = control_text(text)
    return any(marker in text for marker in ["改成", "修改", "换成"]) and any(
        marker in text
        for marker in ["地址", "目标", "参数", "url", "URL", "标题", "正文", "内容", "评论", "消息", "标签"]
    )


def is_ambiguous_continue(text: str) -> bool:
    text = control_text(text)
    normalized_cn = text.strip().strip("。?!！？~，,；; ")
    if normalized_cn in {"好的", "好", "嗯", "继续", "可以", "行", "走吧", "ok", "OK"}:
        return True
    normalized = text.strip().strip("。?!！？~")
    return normalized in {"好的", "好", "嗯", "继续", "可以", "行", "走吧", "ok", "OK"}


def control_text(text: str) -> str:
    stripped = text.strip()
    if "：" not in stripped:
        return stripped
    prefix, suffix = stripped.split("：", 1)
    normalized_prefix = prefix.strip().upper()
    if normalized_prefix.startswith(("CHAT-E2E-", "PHASE34-", "NAT-", "WECHAT-REAL-")):
        return suffix.strip()
    suffix_normalized = suffix.strip().strip("。?!！？~")
    if suffix_normalized in {
        "好的",
        "好",
        "嗯",
        "继续",
        "可以",
        "行",
        "走吧",
        "OK",
        "ok",
        "确认",
        "同意",
        "允许",
        "拒绝",
        "取消",
        "不用了",
    }:
        return suffix.strip()
    return stripped


def asks_how_to_confirm(text: str) -> bool:
    markers = ["不懂什么是审批", "不想复制", "怎么回复", "告诉我应该怎么回复"]
    return any(marker in text for marker in markers)


def looks_like_new_action_request(text: str) -> bool:
    text = control_text(text)
    lowered = text.lower()
    standalone_resolution = (
        (
            is_confirm(text)
            or is_deny(text)
            or is_edit(text)
            or is_session_allow(text)
            or is_always_allow(text)
        )
        and "http://" not in lowered
        and "https://" not in lowered
        and not any(
            marker in text
            for marker in ["请下载", "帮我下载", "请打开", "帮我打开", "截图留证", "下载完告诉我结果"]
        )
    )
    if standalone_resolution:
        return False
    if "http://" in lowered or "https://" in lowered:
        return any(
            marker in text or marker in lowered
            for marker in [
                "请下载",
                "帮我下载",
                "下载完告诉我结果",
                "请打开",
                "帮我看一个",
                "帮我看看",
                "截图留证",
                "保存页面截图",
                "登录",
            ]
        )
    if any(marker in text for marker in ["登录", "截图", "截屏", "下载", "请打开", "帮我打开"]):
        request_markers = ["请", "帮我", "然后", "留证", "执行", "打开", "下载", "登录", "截图"]
        explanation_markers = ["不要伪称完成", "不要说已完成", "还没真正执行", "等什么证据"]
        if any(marker in text for marker in request_markers):
            return True
        if any(marker in text for marker in explanation_markers) and any(
            marker in text for marker in ["登录", "截图", "下载"]
        ):
            return True
    return any(
        marker in text or marker in lowered
        for marker in ["帮我下载", "请下载", "下载完告诉我结果", "帮我安装", "请安装", "请打开"]
    )


def first_url(text: str) -> str | None:
    match = _URL_RE.search(text)
    return match.group(0) if match else None


def edit_payload_for_action(action: dict[str, Any], text: str) -> dict[str, Any] | None:
    action_type = str(action.get("action_type") or "")
    if action_type == "account.publish_post":
        args: dict[str, str] = {}
        title = _extract_edited_title(text)
        body = _extract_edited_body(text)
        if title:
            args["title"] = title
        if body:
            args["body"] = body
        return {"arguments": args} if args else None
    if action_type == "external_platform.publish_content":
        args: dict[str, Any] = {}
        title = _extract_edited_title(text)
        body = _extract_edited_body(text)
        first_comment = _extract_after_markers(text, ("首条评论改成", "首评改成", "评论改成"))
        tags = _extract_tags(text)
        if title:
            args["title"] = title
        if body:
            args["publish_text"] = body
        if first_comment:
            args["comment_text"] = first_comment
        if tags:
            args["tags"] = tags
        return {"arguments": args} if args else None
    if action_type == "external_platform.comment_content":
        comment = _extract_after_markers(text, ("评论改成", "正文改成", "内容改成"))
        return {"arguments": {"comment_text": comment}} if comment else None
    if action_type == "external_platform.send_message":
        message = _extract_after_markers(text, ("消息改成", "私信改成", "正文改成", "内容改成"))
        return {"arguments": {"message_text": message}} if message else None
    if action_type in {"browser.download", "browser.open_url"}:
        url = first_url(text)
        if url:
            return {"arguments": {"url": url}}
    return None


def _extract_edited_title(text: str) -> str | None:
    for marker in ("标题改成", "标题换成", "title 改成", "title换成"):
        if marker in text:
            return text.split(marker, 1)[-1].strip(" ：:，。")
    return None


def _extract_edited_body(text: str) -> str | None:
    for marker in ("正文改成", "内容改成", "body 改成", "正文换成"):
        if marker in text:
            return text.split(marker, 1)[-1].strip(" ：:，。")
    return None


def _extract_after_markers(text: str, markers: tuple[str, ...]) -> str | None:
    for marker in markers:
        if marker in text:
            value = text.split(marker, 1)[-1].strip(" ：:，。")
            if value:
                return value
    return None


def _extract_tags(text: str) -> list[str]:
    for marker in ("标签改成", "标签换成", "tag 改成", "tags 改成"):
        if marker not in text:
            continue
        raw = text.split(marker, 1)[-1].strip(" ：:，。")
        parts = re.split(r"[\s,，、#]+", raw)
        tags = [part for part in parts if part]
        if tags:
            return tags
    return []
