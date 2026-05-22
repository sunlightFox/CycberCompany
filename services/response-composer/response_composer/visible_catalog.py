from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core_types.voice_copy import pick_variant

VISIBLE_CATALOG_VERSION = "visible_catalog.openclaw_hermes.v4"

SCENARIO_IDS: tuple[str, ...] = (
    "casual_chat",
    "knowledge_answer",
    "clarification",
    "task_status",
    "approval_required",
    "action_status",
    "tool_boundary",
    "memory",
    "skill",
    "asset",
    "privacy",
    "professional_advice",
    "multimodal",
    "channel_silent",
    "progress_draft",
    "failure_recovery",
    "notification",
)


@dataclass(frozen=True)
class VisibleCopyEntry:
    scenario_id: str
    variants: tuple[str, ...]
    tone_strategy: str = "plain"
    fallback_text: str = ""


SCENE_CATALOG: dict[str, dict[str, Any]] = {
    scenario_id: {
        "scenario_id": scenario_id,
        "catalog_version": VISIBLE_CATALOG_VERSION,
        "quality_markers": [
            "no_internal_terms",
            "no_false_done",
            "boundary_honesty",
            "privacy_redacted",
            "current_message_priority",
            "evidence_required_before_done",
            "strict_format_preserved",
        ],
        "forbidden_visible_terms": [
            "trace_id",
            "approval_id",
            "tool_call_id",
            "task_id",
            "model_safe_text",
            "prompt_snapshot_id",
            "调度方式",
            "下一次执行时间",
            "后台流程",
            "本轮按",
            "格式约束作答",
            "probe",
            "completed",
            "planned",
            "artifact",
            "Asset Broker",
            "Capability Graph",
            "Safety",
            "Approval",
        ],
    }
    for scenario_id in SCENARIO_IDS
}


COPY_CATALOG: dict[str, VisibleCopyEntry] = {
    "notice.high_risk_default": VisibleCopyEntry(
        "tool_boundary",
        (
            "这一步影响比较大，我先把确认点讲清楚，再继续往下走。",
            "这件事先别急着硬冲，边界说清楚以后再推进。",
        ),
        tone_strategy="boundary",
    ),
    "notice.tool_boundary": VisibleCopyEntry(
        "tool_boundary",
        (
            "这一步得看真实执行结果；工具没真正跑完前，我不把结果说满。",
            "这里先按真实结果来，对应工具没落地前，我不先演一个已经收尾的版本，也不把结果说满。",
        ),
        tone_strategy="boundary",
    ),
    "knowledge.answer": VisibleCopyEntry(
        "knowledge_answer",
        (
            "先把能确认的部分说清楚，其他地方我不硬编。",
            "我先讲确定的信息，再补上还缺的条件。",
        ),
        tone_strategy="analytical",
    ),
    "notice.privacy_block": VisibleCopyEntry(
        "privacy",
        (
            "我看到疑似敏感信息了，这种值我先不复述。要是真凭据，建议先轮换。",
            "敏感信息我先拦住，不复述，也不继续传播。",
        ),
        tone_strategy="boundary",
    ),
    "boundary.desktop": VisibleCopyEntry(
        "tool_boundary",
        (
            "我没有执行桌面窗口这类原生操作，因为当前后端还没提供 desktop.* 能力。要是目标能换成网页、文件或命令行路径，我可以继续帮你处理。",
            "这次没有执行桌面窗口操作，也没有真正操作桌面窗口。原因是当前产品没有 desktop.* 原生控制能力；如果你愿意，我可以改走网页、文件或命令这些可用路径。",
        ),
        tone_strategy="boundary",
    ),
    "boundary.refusal": VisibleCopyEntry(
        "tool_boundary",
        (
            "我懂你想把事情尽快往前推，但这类高风险动作我不能替你跳过确认，也不可以绕开安全边界；你把合法目标和允许范围说清楚，我继续帮你把方案铺开。",
            "这类请求我会先按住：不能替你绕开确认，也不能越过安全边界。你把目标和允许范围说清楚，我继续帮你把方案铺开。",
        ),
        tone_strategy="boundary",
    ),
    "boundary.persona": VisibleCopyEntry(
        "tool_boundary",
        (
            "我不是真人，也不是现实中的人，没有隐藏账号能私下登录别的账号。真要做事，我也只能按合规流程、明确授权和必要确认来推进，不会假装自己有一条能绕过边界的私下入口。你要是愿意，我可以继续帮你说明合规登录、授权确认或替代处理方案。",
            "这点我不绕：我不是真人，也没有隐藏账号。能帮你的事，我会按安全流程、授权边界和必要工具来做，不会编一个越权入口出来；如果你想推进真实操作，我可以接着帮你把合规路径拆清楚。",
        ),
        tone_strategy="boundary",
    ),
    "boundary.internal": VisibleCopyEntry(
        "tool_boundary",
        (
            "这类内部内容我不能完整输出，不过可以改为说明能做什么、不能做什么。",
            "这部分我不能完整输出，但可以改为说明关键边界和能力。",
        ),
        tone_strategy="boundary",
    ),
    "boundary.privacy": VisibleCopyEntry(
        "privacy",
        (
            "我看到疑似敏感信息了，这种值我先不复述。真凭据先轮换，测试内容换占位符就行。",
            "疑似敏感信息我先帮你挡住，不往外带。要是真凭据，建议先轮换。",
        ),
        tone_strategy="boundary",
    ),
    "boundary.professional_medical": VisibleCopyEntry(
        "professional_advice",
        (
            "用药这事我不能只靠一段聊天就给个人化剂量。更稳的是先看说明书和禁忌，复杂情况直接问医生或药师。",
            "这类用药建议我不能给得太满，尤其是剂量。先看说明书和禁忌，特殊情况问医生更稳。",
        ),
        tone_strategy="boundary",
    ),
    "boundary.professional_finance": VisibleCopyEntry(
        "professional_advice",
        (
            "金融这类高风险建议我不能给保赚结论。更稳的是先把目标、期限和承受范围摆清楚。",
            "这种金融判断我不会给一锤定音式保证，先把目标、风险承受和期限讲透更靠谱。",
        ),
        tone_strategy="boundary",
    ),
    "natural.ambiguous_no_pending": VisibleCopyEntry(
        "action_status",
        (
            "现在没有哪一步在等你点头，我不会把一句“好的”当成执行许可。",
            "眼下没挂起动作，我不会乱动，尤其是下载、删除、登录这种事。",
        ),
    ),
    "natural.always_denied_for_risk": VisibleCopyEntry(
        "approval_required",
        (
            "这种事不适合开成长期免确认，你回“只允许这一次”或者“拒绝”就够了。",
            "这类动作不能设成长期自动放行，风险太大了。你只要给我一次性的确认口径，我就按这个走。",
        ),
        tone_strategy="boundary",
    ),
    "natural.pending_invalid": VisibleCopyEntry(
        "action_status",
        (
            "{label} 这一步的确认记录对不上，我先不动它。你重新发一下目标，我马上接。",
            "{label} 这步的确认信息不完整，我先不硬上。重新发起一下就好。",
        ),
    ),
    "natural.resolution_failed": VisibleCopyEntry(
        "failure_recovery",
        (
            "{label} 这步没走通：{reason}。你想重试、改目标，还是先取消，都行。",
            "{label} 这次没落地：{reason}。你要是愿意，我们换个方向继续。",
        ),
    ),
    "natural.plain_next_step_none": VisibleCopyEntry(
        "casual_chat",
        (
            "现在没有待确认动作。以后遇到这种事，你直接回“确认”“拒绝”或者“改成……”就行。",
            "眼下没东西等你点头。真到确认环节，你不用复制编号，直接说人话就行。",
        ),
    ),
    "natural.no_pending_deny": VisibleCopyEntry(
        "action_status",
        (
            "现在没有等待你取消的动作，我也没有往外执行什么。",
            "这会儿没有待取消的动作，我先按兵不动。",
        ),
    ),
    "natural.no_pending_edit": VisibleCopyEntry(
        "action_status",
        (
            "现在没有待改的确认项。你先把要做的事发出来，我再接新的目标。",
            "眼下没有待修改的确认项，先把动作发起起来，我再继续接。",
        ),
    ),
    "natural.no_pending_confirm": VisibleCopyEntry(
        "approval_required",
        (
            "现在没有待确认动作，我不会拿这句话去触发下载、删除、登录或提交。",
            "没有待确认的挂起动作，我不会顺着这句话直接去做高风险事。",
        ),
        tone_strategy="boundary",
    ),
    "natural.ambiguous_pending": VisibleCopyEntry(
        "approval_required",
        (
            "{label} 这步还差一句明确口径，我不能只凭一句“好”就往前冲。你直接告诉我是放行、拒绝，还是换目标。",
            "{label} 这步还差一句明确的话。你回只允许这一次、拒绝，或者给新目标都行。",
        ),
        tone_strategy="boundary",
    ),
    "natural.multiple_pending": VisibleCopyEntry(
        "approval_required",
        (
            "现在有几件事一起在等你点头：{labels}。你明确点名一个，我就接着往下推。",
            "我这边挂着不止一件事：{labels}。你明确说要处理哪一个就好。",
        ),
        tone_strategy="boundary",
    ),
    "natural.after_edited": VisibleCopyEntry(
        "action_status",
        (
            "{label} 我已经收到你的修改要求，接下来会按新口径重新检查需要确认的地方。",
            "{label} 修改要求我记下了，后面只按新的目标继续，不把旧目标带下去。",
        ),
    ),
    "natural.after_session": VisibleCopyEntry(
        "action_status",
        (
            "{label} 这次我记住你的口径了，本会话内同类动作会少打扰你一次。",
            "这次 {label} 放行；同一会话里类似范围我会顺着这个口径走。",
        ),
    ),
    "natural.after_once": VisibleCopyEntry(
        "action_status",
        (
            "{label} 这次收到明确口径了，我接着往下处理。",
            "{label} 这一步可以继续，有变化我会直接跟你说。",
        ),
    ),
    "natural.after_no_status": VisibleCopyEntry(
        "action_status",
        (
            "{prefix} 后面走到哪一步，我就说哪一步。",
            "{prefix} 进展我会照实告诉你，不会提前报喜。",
        ),
    ),
    "natural.after_completed": VisibleCopyEntry(
        "action_status",
        (
            "{prefix} 已经处理完，过程记录也留着，回头能复核。",
            "{prefix} 这次已经跑完了，结果和记录都能翻得到。",
        ),
    ),
    "natural.after_waiting": VisibleCopyEntry(
        "approval_required",
        (
            "{prefix} 不过后面还有一步在等你点头，我会停在那里。",
            "{prefix} 只是后面还有一步要你确认，确认前我不会继续执行。",
        ),
        tone_strategy="boundary",
    ),
    "natural.after_failed": VisibleCopyEntry(
        "failure_recovery",
        (
            "{prefix} 但这次没走通；你想重试、缩范围，还是只保留方案都可以。",
            "{prefix} 但这轮没落地成功，要不要换个更稳的方向继续？",
        ),
    ),
    "natural.hard_block_file": VisibleCopyEntry(
        "privacy",
        (
            "这类 `file` 地址不能直接打开，免得把本机内容带出去。",
            "本机 `file` 路径风险太直白了，我不能打开，已经先拦住。",
        ),
        tone_strategy="boundary",
    ),
    "natural.hard_block_network": VisibleCopyEntry(
        "privacy",
        (
            "这种 metadata 或私网敏感地址我不能直接访问，安全策略已经先拦住了。",
            "这个地址踩到私网边界了，我不能继续，已经停住。",
        ),
        tone_strategy="boundary",
    ),
    "natural.hard_block_secret": VisibleCopyEntry(
        "privacy",
        (
            "这里有密钥或越权风险，我先替你挡住，不继续拿它处理。需要的话我能帮你整理安全步骤。",
            "这类敏感内容我不能继续碰，但可以帮你换成脱敏示例或轮换方案。",
        ),
        tone_strategy="boundary",
    ),
    "task.completed": VisibleCopyEntry(
        "task_status",
        (
            "{title} 这件事已经办完了，结果和对应记录都能翻，过程记录也能查。",
            "任务完成了：{title}。后面能看到结果和对应记录。",
        ),
    ),
    "task.waiting_approval": VisibleCopyEntry(
        "approval_required",
        (
            "{title} 已经起步了，只是中间卡在等待确认上，尚未完成。",
            "任务已经跑起来了，不过有一步还在等待确认，现在尚未完成。",
        ),
        tone_strategy="boundary",
    ),
    "task.failed": VisibleCopyEntry(
        "failure_recovery",
        (
            "{title} 这轮没跑通，尚未完成；失败原因还在，我们可以接着看。",
            "任务碰到阻断了：{title}，尚未完成。我们可以继续把卡点理清。",
        ),
    ),
    "task.paused": VisibleCopyEntry(
        "task_status",
        (
            "{title} 现在先停在这儿，尚未完成；通常是还差信息、范围确认，或者前面有个阻断没解开。",
            "任务暂停了：{title}，尚未完成。把缺的那块补上，我们就能接着跑。",
        ),
    ),
    "task.cancelled": VisibleCopyEntry(
        "task_status",
        (
            "{title} 这次就先停在这里，后面没有继续执行。",
            "任务已经取消了：{title}。",
        ),
    ),
    "task.running": VisibleCopyEntry(
        "task_status",
        (
            "{title} 现在还在处理中，尚未完成，我先不替它提前收尾。",
            "{title} 正在推进中，尚未完成，还没到最后一步。",
        ),
    ),
    "task.default": VisibleCopyEntry(
        "task_status",
        (
            "{title} 现在处在 {state}，尚未完成，还没到完成那一步。",
            "任务已创建，当前状态是 {state}：{title}，尚未完成。",
        ),
    ),
    "action.pending": VisibleCopyEntry(
        "approval_required",
        (
            "{label} 这步现在差你一句确认口径，我还没动手。",
            "{label} 这步我先停在确认前，等你一句明确口径再继续。",
        ),
        tone_strategy="boundary",
    ),
    "action.no_pending": VisibleCopyEntry(
        "action_status",
        (
            "现在没有待确认动作，我不会把这句话当成执行许可。",
            "眼下没有待确认动作，我不会顺着一句模糊口径直接往前走。",
        ),
        tone_strategy="boundary",
    ),
    "action.ambiguous_blocked": VisibleCopyEntry(
        "approval_required",
        (
            "{label} 这步还差一句明确口径，我不能只凭一句“好”就往前冲。你直接告诉我是放行、拒绝，还是换目标。",
            "{label} 这步还差一句明确的话。你回只允许这一次、拒绝，或者给新目标都行。",
        ),
        tone_strategy="boundary",
    ),
    "action.multiple_pending": VisibleCopyEntry(
        "approval_required",
        (
            "现在有几件事一起在等你点头：{labels}。你明确点名一个，我就接着往下推。",
            "我这边挂着不止一件事：{labels}。你明确说要处理哪一个就好。",
        ),
        tone_strategy="boundary",
    ),
    "action.blocked": VisibleCopyEntry(
        "approval_required",
        (
            "{label} 这步我先卡住了。{reason}",
            "{label} 这回先停一下。{reason}",
        ),
        tone_strategy="boundary",
    ),
    "action.manual_only": VisibleCopyEntry(
        "tool_boundary",
        (
            "{label} 这步我先不硬上。{reason}",
            "{label} 我先停在这儿。{reason}",
        ),
        tone_strategy="boundary",
    ),
    "action.approved_completed": VisibleCopyEntry(
        "action_status",
        (
            "已确认，{label} 已经完成了，后面能查得到记录。",
            "已确认，{label} 已经收尾完成，回头要查也能翻得到。",
        ),
    ),
    "action.approved_waiting": VisibleCopyEntry(
        "action_status",
        (
            "{label} 我已经接着往前推了，不过后面还有一步要你点头。",
            "{label} 继续推进中，只是中间还会停一下等你确认。",
        ),
    ),
    "action.approved_failed": VisibleCopyEntry(
        "failure_recovery",
        (
            "{label} 这次没有顺利完成。{reason}",
            "{label} 这轮没有顺利完成。{reason}",
        ),
    ),
    "action.approved_progress": VisibleCopyEntry(
        "action_status",
        (
            "{label} 我还在跟进，有变化我会按真实状态告诉你。",
            "{label} 继续推进中，我不会把过程说成结尾。",
        ),
    ),
    "action.denied": VisibleCopyEntry(
        "action_status",
        (
            "{label} 这次先不做了，我已经停下这一步，不继续执行。",
            "{label} 我先收回去，不继续执行。",
        ),
    ),
    "action.edited": VisibleCopyEntry(
        "action_status",
        (
            "{label} 已经按你的修改改好了，我会再检查一遍。",
            "{label} 这步已经修改好，我会重新过一遍后面的确认点。",
        ),
    ),
    "action.edit_missing_target": VisibleCopyEntry(
        "clarification",
        (
            "想改 {label} 没问题，不过新目标还差一句更具体的话。{reason}",
            "{label} 可以改，但你还没把新内容说实。{reason}",
        ),
    ),
    "action.resolution_failed": VisibleCopyEntry(
        "failure_recovery",
        (
            "{label} 这步卡住了，没有完成。{reason}",
            "{label} 这次没落地。{reason}",
        ),
    ),
    "action.default": VisibleCopyEntry(
        "action_status",
        (
            "{label} 目前是 {status}，我会按真实进展继续告诉你。",
            "{label} 还在 {status}，我继续盯着真实状态。",
        ),
    ),
    "action.pending_reason.host.uninstall_software": VisibleCopyEntry(
        "approval_required",
        ("这会把电脑里的软件拿掉，我先等你明确点头。",),
        tone_strategy="boundary",
    ),
    "action.pending_reason.host.install_software": VisibleCopyEntry(
        "approval_required",
        ("这会往电脑里装东西，可能碰到系统环境，所以我先等确认。",),
        tone_strategy="boundary",
    ),
    "action.pending_reason.browser.download": VisibleCopyEntry(
        "approval_required",
        ("这会在本机留下下载文件，确认前我还没动手，先等你确认。",),
        tone_strategy="boundary",
    ),
    "action.pending_reason.file.delete": VisibleCopyEntry(
        "approval_required",
        ("这可能删文件，我先稳住，不会手滑。",),
        tone_strategy="boundary",
    ),
    "action.pending_opener.host.install_software": VisibleCopyEntry(
        "approval_required",
        ("安装这边我先看好了：",),
        tone_strategy="boundary",
    ),
    "action.pending_opener.host.uninstall_software": VisibleCopyEntry(
        "approval_required",
        ("卸载这边我先踩稳了：",),
        tone_strategy="boundary",
    ),
    "action.pending_opener.file.delete": VisibleCopyEntry(
        "approval_required",
        ("删文件这步我先按住了：",),
        tone_strategy="boundary",
    ),
    "action.pending_opener.browser.download": VisibleCopyEntry(
        "approval_required",
        ("下载这边我先盯住了：",),
        tone_strategy="boundary",
    ),
    "action.boundary.host": VisibleCopyEntry(
        "approval_required",
        ("这类动作会改机器状态，所以我先停一下，确认过再继续。",),
        tone_strategy="boundary",
    ),
    "action.boundary.approval": VisibleCopyEntry(
        "approval_required",
        ("这一步还差一次明确确认，我先按住；你确认好了，我就接着推进。",),
        tone_strategy="boundary",
    ),
    "clarification.default": VisibleCopyEntry(
        "clarification",
        (
            "先确认一下关键信息，免得后面跑偏。",
            "这事我先问细一点，确保接下来的判断站得住。",
        ),
    ),
    "memory.written": VisibleCopyEntry(
        "memory",
        (
            "我把这条偏好记下了，后面会顺着这个口径走。",
            "这条记忆已经收进去了，后面表达会尽量保持一致。",
        ),
    ),
    "memory.conflict": VisibleCopyEntry(
        "memory",
        (
            "这条记忆和当前说法有点打架，我先不直接覆盖。",
            "这条记忆和现在的说法不完全一致，我先按当前指令走。",
        ),
    ),
    "skill.boundary": VisibleCopyEntry(
        "skill",
        (
            "这类方法我能说明思路，但真实执行还是得看工具结果和权限。",
            "这只是可用方法，不等于已经执行，我先把限制说清。",
        ),
        tone_strategy="boundary",
    ),
    "asset.boundary": VisibleCopyEntry(
        "asset",
        (
            "资产这块我先不越权碰，访问和操作都要先有授权。",
            "资产访问要按权限来，我先把限制说清。",
        ),
        tone_strategy="boundary",
    ),
    "multimodal.unavailable": VisibleCopyEntry(
        "multimodal",
        (
            "多模态内容现在还不够完整，我先诚实说明当前识别不到位。",
            "我还没拿到足够的解析信息，先不把图片、语音或文件说满。",
        ),
    ),
    "channel.silent": VisibleCopyEntry("channel_silent", ("",)),
    "progress.draft": VisibleCopyEntry(
        "progress_draft",
        (
            "我先把进展说实一点：{summary}",
            "这轮还没收尾，先照实说进度：{summary}",
        ),
    ),
    "failure.recovery": VisibleCopyEntry(
        "failure_recovery",
        (
            "这一步遇到阻断了，但原因还没丢，后面还能继续看：{summary}",
            "没跑顺，但不是没法修，我们先看失败点：{summary}",
        ),
    ),
    "notification.default": VisibleCopyEntry(
        "notification",
        (
            "这边有个提醒：{summary}",
            "我把这条通知整理好了：{summary}",
        ),
    ),
}

_MECHANICAL_OPENERS = (
    "当然可以，",
    "当然可以。",
    "可以的，",
    "可以的。",
    "好，",
    "好。",
    "行，",
    "行。",
    "好的，",
    "好的。",
    "好的：",
    "没问题，",
    "没问题。",
    "明白，",
    "明白。",
    "收到，",
    "收到。",
    "可以，",
    "可以。",
    "当然，",
    "当然。",
    "先说结果：",
    "先说结果，",
    "先说结论：",
    "先说结论，",
    "先给结论：",
    "先给结论，",
    "结论是：",
    "结论：",
    "这点我不绕弯：",
    "这点我不绕：",
    "这块我先坦白一句：",
)

_STRICT_FORMAT_RE = re.compile(r"```|^\s*[{[]")
_CONVERSATIONAL_CUES = (
    "咱们",
    "你这个",
    "先别急",
    "别急",
    "我陪你",
    "我顺着",
    "接着刚才",
    "我懂",
    "我听你这意思",
)


def opening_copy(key: str, seed: str = "", **values: Any) -> str:
    entry = COPY_CATALOG[key]
    variants = tuple(item.format(**values) for item in entry.variants)
    return pick_variant(seed or key, variants, default=entry.fallback_text)


def visible_opening_normalizer(text: str) -> str:
    candidate = str(text or "").strip()
    if not candidate:
        return candidate
    changed = True
    while changed:
        changed = False
        for opener in _MECHANICAL_OPENERS:
            if candidate.startswith(opener):
                candidate = candidate[len(opener) :].lstrip(" ，。:：")
                changed = True
                break
    candidate = re.sub(
        r"^(我来帮你|我来|我先帮你|我先|我可以帮你|我可以|我会帮你|我会)"
        r"\s*[,，。:：]?\s*",
        "",
        candidate,
    )
    candidate = re.sub(
        r"^(下面是|以下是|这里是|给你一版|我给你一版)"
        r"\s*[,，。:：]?\s*",
        "",
        candidate,
    )
    return candidate.lstrip(" ，。:：")


def conversation_voice_strategy(
    *,
    text: str,
    scenario: str,
    persona: dict[str, Any] | None = None,
    heart: dict[str, Any] | None = None,
    high_risk: bool = False,
) -> dict[str, Any]:
    body = str(text or "").strip()
    persona = persona or {}
    heart = heart or {}
    tone_hints = {str(item) for item in persona.get("tone_hints") or []}
    strict_format = _strict_format_text(body)
    deescalated = bool(high_risk or heart.get("deescalation_required"))
    if strict_format:
        scene = "strict_format"
        warmth_level = "low"
        humor_level = "none"
        directness_level = "high"
        opener_policy = "preserve"
    elif scenario in {"approval_required", "safety_deny", "tool_boundary"} or deescalated:
        scene = "boundary"
        warmth_level = "medium"
        humor_level = "none"
        directness_level = "high"
        opener_policy = "strip_only"
    elif scenario in {"action_status", "task_status", "task_completed"}:
        scene = "followthrough"
        warmth_level = "medium"
        humor_level = "low"
        directness_level = "medium"
        opener_policy = "strip_only"
    elif heart.get("mood") in {"anxious", "frustrated"}:
        scene = "supportive"
        warmth_level = "high"
        humor_level = "none" if deescalated else "low"
        directness_level = "medium"
        opener_policy = "strip_only"
    elif len(body) >= 180 or "\n" in body:
        scene = "analytical"
        warmth_level = "medium"
        humor_level = "low"
        directness_level = "medium"
        opener_policy = "strip_only"
    elif {"playful", "light_humor"} & tone_hints:
        scene = "casual"
        warmth_level = "high"
        humor_level = "medium"
        directness_level = "medium"
        opener_policy = "strip_only"
    else:
        scene = "plain"
        warmth_level = "medium"
        humor_level = "none"
        directness_level = "medium"
        opener_policy = "strip_only"
    return {
        "strategy_version": VISIBLE_CATALOG_VERSION,
        "scene": scene,
        "warmth_level": warmth_level,
        "humor_level": humor_level,
        "directness_level": directness_level,
        "deescalated": deescalated,
        "strict_format": strict_format,
        "opener_policy": opener_policy,
        "opener_family": scene if scene != "boundary" else "boundary_soft",
    }


def apply_conversation_voice(
    text: str,
    *,
    seed: str,
    scenario: str,
    persona: dict[str, Any] | None = None,
    heart: dict[str, Any] | None = None,
    high_risk: bool = False,
) -> tuple[str, dict[str, Any]]:
    del seed
    strategy = conversation_voice_strategy(
        text=text,
        scenario=scenario,
        persona=persona,
        heart=heart,
        high_risk=high_risk,
    )
    if strategy["strict_format"]:
        return str(text or "").strip(), strategy
    body = visible_opening_normalizer(text)
    if any(cue in body for cue in _CONVERSATIONAL_CUES):
        return body, strategy
    return body or str(text or "").strip(), strategy


def catalog_metadata() -> dict[str, Any]:
    used_scenarios = {entry.scenario_id for entry in COPY_CATALOG.values()}
    return {
        "catalog_version": VISIBLE_CATALOG_VERSION,
        "required_scenarios": list(SCENARIO_IDS),
        "covered_scenarios": [item for item in SCENARIO_IDS if item in used_scenarios],
        "coverage": len(used_scenarios & set(SCENARIO_IDS)) / len(SCENARIO_IDS),
        "copy_keys": sorted(COPY_CATALOG),
        "scenarios": [
            {
                **SCENE_CATALOG[scenario_id],
                "covered": scenario_id in used_scenarios,
                "copy_keys": [
                    key for key, entry in COPY_CATALOG.items() if entry.scenario_id == scenario_id
                ],
            }
            for scenario_id in SCENARIO_IDS
        ],
    }


def catalog_runtime_texts() -> list[str]:
    return [item for entry in COPY_CATALOG.values() for item in entry.variants]


def _strict_format_text(text: str) -> bool:
    stripped = str(text or "").strip()
    if not stripped:
        return False
    if _STRICT_FORMAT_RE.search(stripped):
        return True
    if (stripped.startswith("{") and stripped.endswith("}")) or (
        stripped.startswith("[") and stripped.endswith("]")
    ):
        return True
    lines = [line.strip() for line in stripped.splitlines() if line.strip()]
    return len(lines) >= 2 and any("|---" in line or "---|" in line for line in lines[:4])
