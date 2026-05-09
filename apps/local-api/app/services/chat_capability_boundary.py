from __future__ import annotations

from app.schemas.chat_routes import CapabilityBoundaryResult


class ChatCapabilityBoundaryService:
    def desktop_native_boundary(self) -> CapabilityBoundaryResult:
        return CapabilityBoundaryResult(
            status="capability_not_supported",
            capability_namespace="desktop",
            executed=False,
            safe_fallbacks=["browser.*", "file.*", "terminal.*"],
            failure_code="desktop_native_not_supported",
            reason_codes=["desktop_native_not_supported", "capability_boundary"],
            message=(
                "我没有执行桌面窗口操作。"
                "这次没有真正操作桌面窗口。"
                "当前后端还未提供 desktop.* 原生窗口、"
                "鼠标或键盘控制能力，所以不能替你最小化记事本这类桌面窗口。"
                "如果目标可以改成网页、文件或命令行路径，我可以继续按那些能力帮你处理。"
            ),
            metadata={
                "tool_namespace": "desktop",
                "supported_actions": [],
                "requires_future_design": [
                    "capability",
                    "approval",
                    "trace",
                    "artifact",
                    "sandbox",
                ],
            },
        )
