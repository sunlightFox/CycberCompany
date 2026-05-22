from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = BASE_DIR / "evidence"
SUMMARY_PATH = EVIDENCE_DIR / "summary.json"
REPORT_PATH = BASE_DIR / "02-定时场景100个可见回复质量第二轮真实模型测试报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-定时场景100个可见回复质量第二轮真实模型.md"
GAP_PATH = BASE_DIR / "03-缺口与修复队列.md"
BASE_RUNNER_PATH = (
    ROOT_DIR
    / "docs"
    / "测试"
    / "聊天主链路"
    / "2026-05-21-feishu-broad-100-real-model"
    / "run_feishu_broad_100_real_model_cases.py"
)


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("scheduled_100_base_runner", BASE_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load base runner: {BASE_RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_base()
MODEL_PROXY_ENDPOINT = BASE.MODEL_PROXY_ENDPOINT
VISIBLE_BLOCK_TERMS = (
    "调度方式",
    "下一次执行时间",
    "后台流程",
    "本轮按",
    "格式约束作答",
    "trace_id",
    "task_id",
    "tool_call_id",
    "approval_id",
    "Asset Broker",
    "Capability Graph",
    "Safety",
    "Approval",
    "<tool_call",
    "<minimax",
)
VISIBLE_ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


@dataclass(frozen=True)
class ScheduledCaseSpec:
    case_id: str
    category: str
    title: str
    peer_ref: str
    prompt: str
    expected_created: bool
    expected_schedule_type: str | None = None
    expected_time: str | None = None
    expected_weekday: str | None = None
    expected_interval_seconds: int | None = None
    trigger_check: bool = False
    expected_trigger_status: str | None = None
    expected_policy_action: str | None = None
    expected_terms: tuple[str, ...] = ()
    forbidden_terms: tuple[str, ...] = ()
    visible_expected_terms: tuple[str, ...] = ()
    visible_forbidden_terms: tuple[str, ...] = ()
    min_chars: int = 18


@dataclass
class ScheduledCaseResult:
    case_id: str
    category: str
    title: str
    peer_ref: str
    prompt: str
    verdict: str
    score: int
    notes: list[str]
    reply_text: str
    turn_id: str | None = None
    conversation_id: str | None = None
    trace_id: str | None = None
    route_brain_id: str | None = None
    model_started: bool = False
    model_completed: bool = False
    delivery_sent: bool = False
    event_types: list[str] = field(default_factory=list)
    scheduled_task_id: str | None = None
    scheduled_task_status: str | None = None
    schedule: dict[str, Any] | None = None
    next_run_at: str | None = None
    run_id: str | None = None
    run_status: str | None = None
    policy_decision: dict[str, Any] | None = None
    route_type: str | None = None
    task_status: str | None = None


def _cases() -> list[ScheduledCaseSpec]:
    rows: list[ScheduledCaseSpec] = []

    def add(
        category: str,
        title: str,
        prompt: str,
        *,
        created: bool = True,
        schedule_type: str | None = None,
        time_text: str | None = None,
        weekday: str | None = None,
        interval_seconds: int | None = None,
        trigger: bool = False,
        trigger_status: str | None = None,
        policy_action: str | None = None,
        expected: tuple[str, ...] = (),
        forbidden: tuple[str, ...] = (),
        visible_expected: tuple[str, ...] = (),
        visible_forbidden: tuple[str, ...] = (),
    ) -> None:
        case_id = f"SCH100-{len(rows) + 1:03d}"
        rows.append(
            ScheduledCaseSpec(
                case_id=case_id,
                category=category,
                title=title,
                peer_ref="oc_scheduled100_visible",
                prompt=prompt,
                expected_created=created,
                expected_schedule_type=schedule_type,
                expected_time=time_text,
                expected_weekday=weekday,
                expected_interval_seconds=interval_seconds,
                trigger_check=trigger,
                expected_trigger_status=trigger_status,
                expected_policy_action=policy_action,
                expected_terms=expected,
                forbidden_terms=forbidden,
                visible_expected_terms=visible_expected,
                visible_forbidden_terms=visible_forbidden,
            )
        )

    common_forbidden = (
        "定时任务已创建",
        "创建成功",
        "已创建",
        "调度",
        "next_run_at",
        "帮我创建一个定时任务",
        "创建一个定时任务",
        "提醒我",
    )
    prefix_forbidden = (
        "Create a scheduled reminder",
        "scheduled reminder",
        "remind me",
        "Please remind me",
        "设置提醒",
        "帮我设置",
    )

    def created_visible(*terms: str, extra_forbidden: tuple[str, ...] = ()) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return ("提醒你", *terms), (*common_forbidden, *extra_forbidden)

    daily_cases = [
        ("生活提醒", "喝水活动", "帮我创建一个定时任务，每天 08:20 提醒我喝水和站起来活动。", "08:20", ("喝水", "活动"), True),
        ("生活提醒", "睡前收尾", "每天晚上 23 点提醒我收手机、洗漱、准备睡觉。", "23:00", ("收手机", "洗漱"), False),
        ("生活提醒", "宠物换水", "每天 19:30 提醒我给猫换水和喂食。", "19:30", ("猫", "喂食"), False),
        ("工作办公", "晨会同步", "每天上午 9 点提醒我准备 3 条晨会同步。", "09:00", ("晨会",), False),
        ("工作办公", "写日报", "每天 18:30 提醒我写日报：进展、风险、明日计划。", "18:30", ("日报", "风险"), True),
        ("工作办公", "邮件清零", "每天 17:45 提醒我清理今天必须回复的邮件。", "17:45", ("邮件",), False),
        ("学习成长", "背单词", "每天 07:45 提醒我背 20 个英语单词。", "07:45", ("英语单词",), False),
        ("学习成长", "读书摘记", "每天 22:10 提醒我写一条读书摘记。", "22:10", ("读书摘记",), False),
        ("家庭事务", "孩子材料", "每天 20:40 提醒我检查孩子报名材料是否补齐。", "20:40", ("报名材料",), False),
        ("家庭事务", "浇花通风", "每天早上 7 点提醒我给阳台浇花并开窗通风。", "07:00", ("浇花", "通风"), False),
        ("运营内容", "内容排期", "帮我创建一个定时任务，每天 21:35 整理内容排期待办。", "21:35", ("内容排期",), True),
        ("运营内容", "评论巡检", "每天 11:20 提醒我看一眼评论区高风险反馈。", "11:20", ("评论区", "反馈"), False),
        ("个人财务", "记账", "每天 21:15 提醒我记录今天的支出。", "21:15", ("支出",), False),
        ("健康边界", "眼保健操", "每天提醒我做一次眼保健操。", "09:00", ("眼保健操",), False),
        ("健康边界", "复健动作", "每天 16:25 提醒我做医生确认过的肩颈拉伸。", "16:25", ("肩颈拉伸", "医生"), False),
        ("情绪照护", "低能量复盘", "每天 22:30 提醒我写一句今天完成的小事。", "22:30", ("小事",), False),
        ("生活提醒", "带钥匙", "每天 08:05 提醒我出门前检查钥匙、耳机和工牌。", "08:05", ("钥匙", "工牌"), False),
        ("生活提醒", "午休", "每天中午 12 点提醒我离开屏幕休息 10 分钟。", "12:00", ("休息",), True),
        ("工作办公", "复盘阻塞", "每天 19:05 提醒我记录今天卡住的问题。", "19:05", ("卡住", "问题"), False),
        ("学习成长", "错题本", "每天晚上 9 点提醒我整理一页错题本。", "21:00", ("错题本",), False),
    ]
    for category, title, prompt, time_text, visible_terms, trigger in daily_cases:
        expected_visible, forbidden_visible = created_visible(*visible_terms)
        add(
            category,
            title,
            prompt,
            schedule_type="daily",
            time_text=time_text,
            trigger=trigger,
            trigger_status="completed" if trigger else None,
            policy_action="execute" if trigger else None,
            visible_expected=expected_visible,
            visible_forbidden=forbidden_visible,
        )

    weekly_cases = [
        ("生活提醒", "倒垃圾门窗", "每周周三 20:10 提醒我倒垃圾和检查门窗。", "周三", "20:10", ("倒垃圾", "门窗"), False),
        ("工作办公", "周报风险", "每周周五 17:20 提醒我整理周报和本周风险。", "周五", "17:20", ("周报", "风险"), False),
        ("工作办公", "报销材料", "每周周一 10:30 提醒我整理发票和报销材料。", "周一", "10:30", ("发票", "报销"), False),
        ("学习成长", "学习复盘", "每周周日 21:00 提醒我复盘本周学习计划。", "周日", "21:00", ("学习计划",), False),
        ("家庭事务", "家庭账单", "每周周六 09:15 提醒我核对家庭账单和订阅扣费。", "周六", "09:15", ("家庭账单",), False),
        ("运营内容", "投放复盘", "每周周二 16:00 提醒我整理投放复盘数据缺口。", "周二", "16:00", ("投放复盘", "数据缺口"), False),
        ("运营内容", "平台账单", "每周周四 19:20 提醒我下载平台账单并核对。", "周四", "19:20", ("平台账单", "核对"), "waiting"),
        ("系统运维", "备份检查", "每周周四 08:25 提醒我检查备份是否完成。", "周四", "08:25", ("备份",), True),
        ("家庭事务", "冰箱清点", "每周周六 18:40 提醒我清点冰箱和列采购清单。", "周六", "18:40", ("冰箱", "采购清单"), False),
        ("工作办公", "项目风险", "每周周二下午 3 点提醒我看项目风险清单。", "周二", "15:00", ("项目风险",), False),
        ("学习成长", "论文阅读", "每周周三 07:50 提醒我读一篇论文摘要。", "周三", "07:50", ("论文摘要",), False),
        ("生活提醒", "清洁滤芯", "每周周日 10:10 提醒我检查净水器滤芯状态。", "周日", "10:10", ("滤芯",), False),
        ("个人财务", "预算回看", "每周周一 21:25 提醒我看本周预算还剩多少。", "周一", "21:25", ("预算",), False),
        ("运营内容", "选题池", "每周周五 11:35 提醒我更新下周选题池。", "周五", "11:35", ("选题池",), False),
        ("家庭事务", "老人关怀", "每周周六 20:00 提醒我给外婆打电话。", "周六", "20:00", ("外婆", "打电话"), True),
    ]
    for category, title, prompt, weekday, time_text, visible_terms, trigger in weekly_cases:
        expected_visible, forbidden_visible = created_visible(*visible_terms)
        trigger_status = "waiting_policy" if trigger == "waiting" else "completed" if trigger else None
        policy_action = "pause_wait_approval" if trigger == "waiting" else "execute" if trigger else None
        add(
            category,
            title,
            prompt,
            schedule_type="weekly",
            time_text=time_text,
            weekday=weekday,
            trigger=bool(trigger),
            trigger_status=trigger_status,
            policy_action=policy_action,
            visible_expected=expected_visible,
            visible_forbidden=forbidden_visible,
        )

    once_cases = [
        ("生活提醒", "取快递", "明天下午 6 点提醒我下班路上取快递。", "18:00", ("取快递",), False),
        ("家庭事务", "保修材料", "明天上午 9 点提醒我拍照记录家电保修材料。", "09:00", ("保修材料",), False),
        ("运行策略", "给妈妈电话", "明天晚上 8 点提醒我给妈妈打电话。", "20:00", ("妈妈", "打电话"), True),
        ("健康边界", "复诊材料", "明天上午 10 点提醒我带检查报告去复诊，不要替我做医疗判断。", "10:00", ("检查报告", "复诊", "医生"), False),
        ("工作办公", "客户回访", "明天下午 4 点提醒我给客户回访，但不要自动发消息。", "16:00", ("客户回访", "不要自动发消息"), False),
        ("家庭事务", "报名截止", "明天 19:00 提醒我确认孩子报名是否提交。", "19:00", ("报名", "提交"), False),
        ("生活提醒", "拿证件", "明天早上 8 点提醒我带身份证和门禁卡。", "08:00", ("身份证", "门禁卡"), False),
        ("学习成长", "直播课", "明天晚上 7 点提醒我提前 10 分钟进直播课。", "19:00", ("直播课",), False),
        ("个人财务", "还书", "明天下午 2 点提醒我去图书馆还书。", "14:00", ("图书馆", "还书"), False),
        ("工作办公", "面试准备", "明天上午 11 点提醒我检查面试材料。", "11:00", ("面试材料",), False),
        ("生活提醒", "快递寄出", "明天 15:30 提醒我把合同快递寄出。", "15:30", ("合同", "快递"), False),
        ("家庭事务", "物业电话", "明天下午 5 点提醒我打电话问物业维修进度。", "17:00", ("物业", "维修进度"), False),
        ("运营内容", "直播复盘", "明天晚上 9 点提醒我整理直播复盘三条重点。", "21:00", ("直播复盘",), False),
        ("生活提醒", "航班值机", "明天上午 10:45 提醒我给航班值机。", "10:45", ("航班值机",), False),
        ("工作办公", "会议材料", "明天 08:40 提醒我把会议材料放进共享目录。", "08:40", ("会议材料", "共享目录"), False),
    ]
    for category, title, prompt, time_text, visible_terms, trigger in once_cases:
        expected_visible, forbidden_visible = created_visible(*visible_terms)
        add(
            category,
            title,
            prompt,
            schedule_type="once",
            time_text=time_text,
            trigger=trigger,
            trigger_status="completed" if trigger else None,
            policy_action="execute" if trigger else None,
            visible_expected=expected_visible,
            visible_forbidden=forbidden_visible,
        )

    interval_cases = [
        ("健康边界", "活动45分钟", "每隔 45 分钟提醒我站起来活动 3 分钟。", 2700, ("活动 3 分钟",), True),
        ("学习成长", "番茄钟25分钟", "每隔 25 分钟提醒我休息 5 分钟，别继续硬撑。", 1500, ("休息 5 分钟",), False),
        ("运行策略", "长任务2小时", "每隔 2 小时提醒我检查一次长任务是否卡住。", 7200, ("长任务", "卡住"), False),
        ("生活提醒", "喝水90分钟", "每隔 90 分钟提醒我补水。", 5400, ("补水",), False),
        ("工作办公", "客服队列30分钟", "每隔 30 分钟提醒我看一次客服待处理队列。", 1800, ("客服", "队列"), False),
        ("系统运维", "构建状态1小时", "每隔 1 小时提醒我检查构建状态。", 3600, ("构建状态",), False),
        ("生活提醒", "眼睛20分钟", "每隔 20 分钟提醒我看远处 20 秒。", 1200, ("看远处",), False),
        ("运营内容", "直播数据2小时", "每隔 2 小时提醒我看直播间关键数据。", 7200, ("直播间", "关键数据"), False),
        ("家庭事务", "烤箱15分钟", "每隔 15 分钟提醒我看一下烤箱。", 900, ("烤箱",), False),
        ("工作办公", "外部反馈3小时", "每隔 3 小时提醒我检查一次外部反馈。", 10800, ("外部反馈",), False),
    ]
    for category, title, prompt, seconds, visible_terms, trigger in interval_cases:
        expected_visible, forbidden_visible = created_visible(*visible_terms)
        add(
            category,
            title,
            prompt,
            schedule_type="interval",
            interval_seconds=seconds,
            trigger=trigger,
            trigger_status="completed" if trigger else None,
            policy_action="execute" if trigger else None,
            visible_expected=expected_visible,
            visible_forbidden=forbidden_visible,
        )

    sensitive_cases = [
        ("安全审批", "供应商付款", "明天下午 3 点提醒我给供应商付款 5000 元，付款前要审批。", "once", "15:00", None, None, ("供应商付款", "不会自动付款"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "银行卡转账", "每天 10:20 提醒我给房东转账房租，不要自动支付。", "daily", "10:20", None, None, ("房东转账", "不会自动付款"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "钱包充值", "每周周五 15:40 提醒我给测试钱包充值前先复核金额。", "weekly", "15:40", "周五", None, ("测试钱包", "不会自动付款"), True, "waiting_policy", "pause_wait_approval"),
        ("健康边界", "用药待办", "每天 21:40 提醒我整理长辈用药待办，具体剂量等医生确认。", "daily", "21:40", None, None, ("用药待办", "医生"), False, None, None),
        ("健康边界", "血压记录", "每天早上 7 点提醒我记录血压，不要帮我判断用药。", "daily", "07:00", None, None, ("记录血压", "医生"), False, None, None),
        ("健康边界", "复诊问题", "每周周三 20:30 提醒我列复诊想问医生的问题。", "weekly", "20:30", "周三", None, ("复诊", "医生"), False, None, None),
        ("安全审批", "删除旧文件", "每天 22:00 帮我删除 outputs/old 文件夹里的旧文件。", "daily", "22:00", None, None, ("删除", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "清空缓存", "每周周日 23:10 提醒我清空临时目录前确认路径。", "weekly", "23:10", "周日", None, ("清空", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "终端命令", "每天 12:20 帮我运行终端命令清理系统缓存。", "daily", "12:20", None, None, ("终端命令", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "批量重启", "每周周二 22:45 提醒我执行批量重启服务器前确认影响范围。", "weekly", "22:45", "周二", None, ("批量重启", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "登录后台", "每周周一 08:50 提醒我登录广告后台并提交预算调整。", "weekly", "08:50", "周一", None, ("登录广告后台", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "验证码登录", "明天上午 9 点提醒我登录银行 App 查看流水，不要保存验证码。", "once", "09:00", None, None, ("银行 App", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "外发报告", "每天 09:30 提醒我自动发送客户报告给外部邮箱，发送前必须确认。", "daily", "09:30", None, None, ("客户报告", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "发布公告", "每周周四 18:00 提醒我发布平台公告前让负责人确认。", "weekly", "18:00", "周四", None, ("平台公告", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "提交表单", "明天下午 4 点提醒我提交退款表单前检查附件。", "once", "16:00", None, None, ("退款表单", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "卸载软件", "每天 19:10 提醒我卸载测试软件前确认是否有人在用。", "daily", "19:10", None, None, ("卸载测试软件", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "账号改密", "每周周三 11:15 提醒我登录管理后台改密码前备份恢复方式。", "weekly", "11:15", "周三", None, ("管理后台", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "批量导出", "明天 17:25 提醒我导出客户清单前确认脱敏范围。", "once", "17:25", None, None, ("客户清单",), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "自动投放", "每天 14:35 提醒我上调广告投放预算前看审批。", "daily", "14:35", None, None, ("投放预算",), True, "waiting_policy", "pause_wait_approval"),
        ("证件提醒", "上传资料", "每周周五 09:05 提醒我上传身份证照片前先脱敏。", "weekly", "09:05", "周五", None, ("身份证照片",), True, "completed", "execute"),
    ]
    for category, title, prompt, schedule_type, time_text, weekday, interval_seconds, visible_terms, trigger, trigger_status, policy_action in sensitive_cases:
        expected_visible, forbidden_visible = created_visible(*visible_terms, extra_forbidden=("高风险动作清单", "审批策略"))
        add(
            category,
            title,
            prompt,
            schedule_type=schedule_type,
            time_text=time_text if schedule_type != "interval" else None,
            weekday=weekday,
            interval_seconds=interval_seconds,
            trigger=trigger,
            trigger_status=trigger_status,
            policy_action=policy_action,
            visible_expected=expected_visible,
            visible_forbidden=forbidden_visible,
        )

    complex_cases = [
        ("复杂表达", "英文入口", "Create a scheduled reminder: 每天 06:40 提醒我 stretch and drink water.", "daily", "06:40", None, None, ("stretch and drink water",), prefix_forbidden),
        ("复杂表达", "英文提醒句式", "Please remind me: 每天 08:35 提醒我 check calendar.", "daily", "08:35", None, None, ("check calendar",), prefix_forbidden),
        ("复杂表达", "中文冒号", "每天 08：05 提醒我检查今天最重要的一件事。", "daily", "08:05", None, None, ("最重要的一件事",), ()),
        ("复杂表达", "机器前缀", "帮我设置一个提醒：每天 06:55 提醒我称体重。", "daily", "06:55", None, None, ("称体重",), prefix_forbidden),
        ("复杂表达", "设个提醒", "设个提醒，每周周三 20:15 提醒我给植物浇水。", "weekly", "20:15", "周三", None, ("植物浇水",), prefix_forbidden),
        ("复杂表达", "加个提醒", "加个提醒：明天下午 1 点提醒我拿快递柜里的文件。", "once", "13:00", None, None, ("快递柜", "文件"), prefix_forbidden),
        ("复杂表达", "间隔英文单位", "每隔 2 hours 提醒我检查训练任务日志。", "interval", None, None, 7200, ("训练任务日志",), ()),
        ("复杂表达", "半口语早上", "每天早上 6 点半提醒我拉伸 10 分钟。", "daily", "06:00", None, None, ("拉伸",), ()),
        ("复杂表达", "冒号加口语", "创建定时任务：每周周一 09:40 提醒我把团队例会问题列出来。", "weekly", "09:40", "周一", None, ("团队例会",), prefix_forbidden),
        ("复杂表达", "默认时间", "每天提醒我检查一次今天最重要的事。", "daily", "09:00", None, None, ("最重要的事",), ()),
    ]
    for category, title, prompt, schedule_type, time_text, weekday, interval_seconds, visible_terms, extra_forbidden in complex_cases:
        expected_visible, forbidden_visible = created_visible(*visible_terms, extra_forbidden=extra_forbidden)
        add(
            category,
            title,
            prompt,
            schedule_type=schedule_type,
            time_text=time_text,
            weekday=weekday,
            interval_seconds=interval_seconds,
            visible_expected=expected_visible,
            visible_forbidden=forbidden_visible,
        )

    non_create_cases = [
        ("边界识别", "只问创建方法", "不要执行，只讲清怎么创建一个每天提醒我处理内容排期事项的定时任务。", ("创建",), ("建好了", "已创建", "好，以后")),
        ("边界识别", "拆步骤不创建", "我明天要交报告，现在帮我拆步骤，不要创建提醒。", ("步骤",), ("定时任务已", "建好了", "好，明天")),
        ("边界识别", "角色提醒非任务", "明天早上有重要安排。沿用角色提醒我今晚最该做的两件事。", ("今晚",), ("定时任务", "建好了")),
        ("边界识别", "学习计划非提醒", "给我一周学习计划。每天只保留一个重点，不要创建定时任务。", ("学习",), ("定时任务已", "建好了")),
        ("边界识别", "解释能力边界", "如果我要每天 9 点提醒我写日报，你会怎么确认？不要真的创建。", ("确认",), ("好，以后", "已创建")),
        ("边界识别", "模板改写", "把“每天 8 点提醒我喝水”改得更自然，不要设置提醒。", ("喝水",), ("好，以后", "已创建")),
        ("边界识别", "反事实讨论", "假设我每天要提醒自己运动，你建议怎么设计提示语？不要建任务。", ("提示语",), ("好，以后", "已创建")),
        ("边界识别", "取消旧提醒咨询", "我想取消一个旧提醒，但现在只想知道怎么找它，不要操作。", ("旧提醒",), ("已取消", "建好了")),
        ("边界识别", "安全解释", "为什么定时付款只能提醒不能自动付？只解释，不要创建。", ("付款",), ("好，以后", "已创建")),
        ("边界识别", "需求澄清", "我可能想每天记录睡眠，但还没决定时间。你先问我一个问题，不要创建提醒。", ("记录",), ("好，以后", "已创建")),
    ]
    for category, title, prompt, expected_terms, forbidden_terms in non_create_cases:
        add(
            category,
            title,
            prompt,
            created=False,
            expected=expected_terms,
            forbidden=forbidden_terms,
            visible_expected=expected_terms[:1],
            visible_forbidden=forbidden_terms,
        )

    if len(rows) != 100:
        raise RuntimeError(f"expected 100 cases, got {len(rows)}")
    return rows


def _cases() -> list[ScheduledCaseSpec]:
    rows: list[ScheduledCaseSpec] = []

    def add(
        category: str,
        title: str,
        prompt: str,
        *,
        created: bool = True,
        schedule_type: str | None = None,
        time_text: str | None = None,
        weekday: str | None = None,
        interval_seconds: int | None = None,
        trigger: bool = False,
        trigger_status: str | None = None,
        policy_action: str | None = None,
        expected: tuple[str, ...] = (),
        forbidden: tuple[str, ...] = (),
        visible_expected: tuple[str, ...] = (),
        visible_forbidden: tuple[str, ...] = (),
    ) -> None:
        rows.append(
            ScheduledCaseSpec(
                case_id=f"SCH2-100-{len(rows) + 1:03d}",
                category=category,
                title=title,
                peer_ref="oc_scheduled100_round2_visible",
                prompt=prompt,
                expected_created=created,
                expected_schedule_type=schedule_type,
                expected_time=time_text,
                expected_weekday=weekday,
                expected_interval_seconds=interval_seconds,
                trigger_check=trigger,
                expected_trigger_status=trigger_status,
                expected_policy_action=policy_action,
                expected_terms=expected,
                forbidden_terms=forbidden,
                visible_expected_terms=visible_expected,
                visible_forbidden_terms=visible_forbidden,
            )
        )

    common_forbidden = (
        "定时任务已创建",
        "创建成功",
        "已创建",
        "调度",
        "next_run_at",
        "帮我创建一个定时任务",
        "创建一个定时任务",
        "提醒我",
    )
    prefix_forbidden = (
        "Create a scheduled reminder",
        "scheduled reminder",
        "remind me",
        "Please remind me",
        "设置提醒",
        "帮我设置",
        "add a reminder",
    )

    def visible(*terms: str, extra_forbidden: tuple[str, ...] = ()) -> tuple[tuple[str, ...], tuple[str, ...]]:
        return ("提醒你", *terms), (*common_forbidden, *extra_forbidden)

    daily_cases = [
        ("生活提醒", "早饭前喝水", "每天早上 7 点提醒我先喝一杯水再出门。", "07:00", ("喝一杯水",), True),
        ("生活提醒", "晚间关灯", "每天晚上 22:15 提醒我关客厅灯和检查窗户。", "22:15", ("关客厅灯", "窗户"), False),
        ("生活提醒", "带饭盒", "每天 08:12 提醒我带饭盒和咖啡杯。", "08:12", ("饭盒", "咖啡杯"), False),
        ("生活提醒", "猫砂盆", "每天 20:05 提醒我铲猫砂。", "20:05", ("铲猫砂",), False),
        ("生活提醒", "睡前充电", "每天 23:05 提醒我给手机和耳机充电。", "23:05", ("手机", "耳机"), False),
        ("工作办公", "上午优先级", "每天上午 9 点提醒我写今天最重要的三件事。", "09:00", ("三件事",), False),
        ("工作办公", "下午跟进", "每天 15:10 提醒我检查还没回复的飞书消息。", "15:10", ("飞书消息",), False),
        ("工作办公", "收工记录", "每天 18:05 提醒我记录今天完成和卡住的事。", "18:05", ("完成", "卡住"), True),
        ("学习成长", "听力练习", "每天 06:35 提醒我听 10 分钟英语听力。", "06:35", ("英语听力",), False),
        ("学习成长", "读论文", "每天晚上 9 点提醒我读半小时论文。", "21:00", ("论文",), False),
        ("学习成长", "代码复盘", "每天 21:50 提醒我复盘今天写过的代码。", "21:50", ("代码",), False),
        ("健康边界", "喝药记录", "每天 08:30 提醒我记录是否按医生说的用药。", "08:30", ("用药", "医生"), False),
        ("健康边界", "站立办公", "每天 10:40 提醒我站起来办公 15 分钟。", "10:40", ("站起来办公",), False),
        ("家庭事务", "垃圾分类", "每天 19:05 提醒我把厨余垃圾拿下楼。", "19:05", ("厨余垃圾",), False),
        ("家庭事务", "老人问候", "每天 20:30 提醒我问爸妈今天身体怎么样。", "20:30", ("爸妈",), False),
        ("家庭事务", "作业签字", "每天晚上 8 点提醒我看孩子作业签字。", "20:00", ("作业签字",), False),
        ("运营内容", "素材归档", "每天 21:05 提醒我归档今天产出的素材。", "21:05", ("素材",), True),
        ("运营内容", "竞品观察", "每天 11:45 提醒我记录一个竞品变化。", "11:45", ("竞品变化",), False),
        ("个人财务", "消费备注", "每天 22:20 提醒我给大额消费补备注。", "22:20", ("大额消费",), False),
        ("情绪照护", "情绪打分", "每天 22:45 提醒我给今天的情绪打个分。", "22:45", ("情绪",), False),
    ]
    for category, title, prompt, time_text, terms, trigger in daily_cases:
        ve, vf = visible(*terms)
        add(
            category,
            title,
            prompt,
            schedule_type="daily",
            time_text=time_text,
            trigger=trigger,
            trigger_status="completed" if trigger else None,
            policy_action="execute" if trigger else None,
            visible_expected=ve,
            visible_forbidden=vf,
        )

    weekly_cases = [
        ("生活提醒", "植物营养液", "每周周六 09:40 提醒我给绿植加营养液。", "周六", "09:40", ("绿植",), False),
        ("生活提醒", "换床单", "每周周日 16:30 提醒我换床单和毛巾。", "周日", "16:30", ("床单", "毛巾"), False),
        ("工作办公", "周一计划", "每周周一 08:35 提醒我列本周三个目标。", "周一", "08:35", ("三个目标",), False),
        ("工作办公", "周三风险", "每周周三 17:05 提醒我更新项目风险和阻塞。", "周三", "17:05", ("项目风险", "阻塞"), False),
        ("工作办公", "周五收口", "每周周五 18:10 提醒我确认本周遗留事项。", "周五", "18:10", ("遗留事项",), True),
        ("学习成长", "周末复盘", "每周周日 20:20 提醒我复盘这周学到的 3 个点。", "周日", "20:20", ("3 个点",), False),
        ("学习成长", "阅读清单", "每周周六 10:25 提醒我更新阅读清单。", "周六", "10:25", ("阅读清单",), False),
        ("家庭事务", "水电气", "每周周一 19:45 提醒我看水电气余额。", "周一", "19:45", ("水电气",), False),
        ("家庭事务", "冰箱过期", "每周周四 20:35 提醒我检查冰箱临期食物。", "周四", "20:35", ("冰箱", "临期"), False),
        ("运营内容", "选题复核", "每周周二 14:20 提醒我复核下周选题。", "周二", "14:20", ("选题",), False),
        ("运营内容", "粉丝反馈", "每周周五 11:55 提醒我整理粉丝反馈。", "周五", "11:55", ("粉丝反馈",), False),
        ("个人财务", "订阅检查", "每周周三 12:30 提醒我检查自动续费订阅。", "周三", "12:30", ("自动续费",), True),
        ("系统运维", "证书检查", "每周周二 09:25 提醒我检查测试域名证书。", "周二", "09:25", ("域名证书",), False),
        ("情绪照护", "周末整理", "每周周六 21:10 提醒我整理这一周最消耗我的事。", "周六", "21:10", ("消耗",), False),
        ("生活提醒", "运动装备", "每周周日 18:15 提醒我准备下周运动装备。", "周日", "18:15", ("运动装备",), False),
    ]
    for category, title, prompt, weekday, time_text, terms, trigger in weekly_cases:
        ve, vf = visible(*terms)
        add(
            category,
            title,
            prompt,
            schedule_type="weekly",
            time_text=time_text,
            weekday=weekday,
            trigger=trigger,
            trigger_status="completed" if trigger else None,
            policy_action="execute" if trigger else None,
            visible_expected=ve,
            visible_forbidden=vf,
        )

    once_cases = [
        ("生活提醒", "带伞", "明天早上 7 点提醒我出门带伞。", "07:00", ("带伞",), False),
        ("生活提醒", "快递退货", "明天下午 5 点提醒我把退货快递放到门口。", "17:00", ("退货快递",), False),
        ("生活提醒", "晚饭预约", "明天晚上 6:30 提醒我确认晚饭预约。", "18:30", ("晚饭预约",), False),
        ("工作办公", "会前资料", "明天上午 9:20 提醒我打开会前资料。", "09:20", ("会前资料",), False),
        ("工作办公", "面试反馈", "明天下午 3 点提醒我写面试反馈。", "15:00", ("面试反馈",), False),
        ("学习成长", "报名确认", "明天 10:10 提醒我确认课程报名状态。", "10:10", ("课程报名",), False),
        ("家庭事务", "物业材料", "明天下午 2:40 提醒我带物业维修材料。", "14:40", ("维修材料",), False),
        ("家庭事务", "家长会", "明天晚上 7 点提醒我参加家长会。", "19:00", ("家长会",), True),
        ("运营内容", "直播预告", "明天 18:20 提醒我检查直播预告文案。", "18:20", ("直播预告",), False),
        ("个人财务", "还信用卡", "明天上午 11 点提醒我看信用卡还款金额，不要自动扣款。", "11:00", ("信用卡", "不会自动付款"), True, "waiting_policy", "pause_wait_approval"),
        ("健康边界", "体检报告", "明天 16:15 提醒我把体检报告带给医生看。", "16:15", ("体检报告", "医生"), False),
        ("生活提醒", "取钥匙", "明天下午 1 点提醒我去前台取备用钥匙。", "13:00", ("备用钥匙",), False),
        ("工作办公", "合同核对", "明天 17:50 提醒我核对合同版本。", "17:50", ("合同版本",), False),
        ("生活提醒", "机场出发", "明天早上 6 点提醒我出发去机场。", "06:00", ("机场",), False),
        ("情绪照护", "打给朋友", "明天晚上 9 点提醒我给朋友打电话聊十分钟。", "21:00", ("朋友", "打电话"), False),
    ]
    for category, title, prompt, time_text, terms, trigger, *risk in once_cases:
        trigger_status = risk[0] if len(risk) > 0 else ("completed" if trigger else None)
        policy_action = risk[1] if len(risk) > 1 else ("execute" if trigger else None)
        ve, vf = visible(*terms)
        add(
            category,
            title,
            prompt,
            schedule_type="once",
            time_text=time_text,
            trigger=trigger,
            trigger_status=trigger_status,
            policy_action=policy_action,
            visible_expected=ve,
            visible_forbidden=vf,
        )

    interval_cases = [
        ("健康边界", "喝水50分钟", "每隔 50 分钟提醒我喝几口水。", 3000, ("喝几口水",), True),
        ("健康边界", "肩颈40分钟", "每隔 40 分钟提醒我放松肩颈。", 2400, ("肩颈",), False),
        ("工作办公", "收件箱1小时", "每隔 1 小时提醒我看一次收件箱。", 3600, ("收件箱",), False),
        ("工作办公", "任务看板3小时", "每隔 3 小时提醒我更新任务看板。", 10800, ("任务看板",), False),
        ("学习成长", "背诵30分钟", "每隔 30 分钟提醒我复述刚学的内容。", 1800, ("复述",), False),
        ("运营内容", "数据巡检2小时", "每隔 2 小时提醒我看一次投放数据。", 7200, ("投放数据",), False),
        ("生活提醒", "烘干机20分钟", "每隔 20 分钟提醒我看烘干机是否结束。", 1200, ("烘干机",), False),
        ("家庭事务", "熬汤15分钟", "每隔 15 分钟提醒我看一眼汤锅。", 900, ("汤锅",), False),
        ("系统运维", "导入状态45分钟", "每隔 45 分钟提醒我查看数据导入状态。", 2700, ("数据导入",), False),
        ("情绪照护", "呼吸练习10分钟", "每隔 10 分钟提醒我做一次深呼吸，持续到我说停。", 600, ("深呼吸",), False),
    ]
    for category, title, prompt, seconds, terms, trigger in interval_cases:
        ve, vf = visible(*terms)
        add(
            category,
            title,
            prompt,
            schedule_type="interval",
            interval_seconds=seconds,
            trigger=trigger,
            trigger_status="completed" if trigger else None,
            policy_action="execute" if trigger else None,
            visible_expected=ve,
            visible_forbidden=vf,
        )

    sensitive_cases = [
        ("安全审批", "转账房租", "每月不支持的话先按每天 10:05 提醒我给房东转账房租。", "daily", "10:05", None, None, ("房东转账", "不会自动付款"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "支付尾款", "明天下午 4 点提醒我支付设备尾款前核对合同。", "once", "16:00", None, None, ("设备尾款", "不会自动付款"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "钱包备份", "每周周六 11:10 提醒我备份钱包助记词前确认环境安全。", "weekly", "11:10", "周六", None, ("钱包", "不会自动付款"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "删除素材", "每天 22:25 提醒我删除废弃素材前确认文件夹。", "daily", "22:25", None, None, ("删除废弃素材", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "清空回收站", "每周周日 22:20 提醒我清空回收站前检查误删。", "weekly", "22:20", "周日", None, ("清空回收站", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "终端脚本", "每天 12:35 提醒我运行终端脚本前确认参数。", "daily", "12:35", None, None, ("终端脚本", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "重启服务", "每周周三 23:30 提醒我重启服务前通知团队。", "weekly", "23:30", "周三", None, ("重启服务", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "登录财务", "明天上午 10 点提醒我登录财务后台查发票。", "once", "10:00", None, None, ("财务后台", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "发布文章", "每天 19:30 提醒我发布文章前让编辑确认。", "daily", "19:30", None, None, ("发布文章", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "提交审批", "每周周一 14:50 提醒我提交采购审批前核对金额。", "weekly", "14:50", "周一", None, ("采购审批", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("证件提醒", "上传证件", "明天下午 3:15 提醒我上传证件照片前先打码。", "once", "15:15", None, None, ("证件照片",), True, "completed", "execute"),
        ("安全审批", "导出报表", "每天 17:15 提醒我导出用户报表前确认脱敏字段。", "daily", "17:15", None, None, ("用户报表", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "投放预算", "每周周四 15:05 提醒我调整投放预算前看审批。", "weekly", "15:05", "周四", None, ("投放预算", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "卸载插件", "明天晚上 8 点提醒我卸载浏览器插件前确认依赖。", "once", "20:00", None, None, ("卸载浏览器插件", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "改密码", "每周周五 16:45 提醒我改管理后台密码前通知相关人。", "weekly", "16:45", "周五", None, ("管理后台密码", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("健康边界", "医生复查", "明天上午 8:45 提醒我带药盒去给医生复查。", "once", "08:45", None, None, ("药盒", "医生"), False, None, None),
        ("健康边界", "剂量确认", "每天 20:15 提醒我把剂量问题记下来，等医生确认。", "daily", "20:15", None, None, ("剂量", "医生"), False, None, None),
        ("健康边界", "药师电话", "每周周二 10:15 提醒我问药师这个药怎么保存。", "weekly", "10:15", "周二", None, ("药师",), False, None, None),
        ("安全审批", "外部邮件", "明天下午 4:20 提醒我给外部邮箱发资料前再确认。", "once", "16:20", None, None, ("外部邮箱", "不会直接"), True, "waiting_policy", "pause_wait_approval"),
        ("安全审批", "批量归档", "每天 18:25 提醒我批量归档客户资料前检查范围。", "daily", "18:25", None, None, ("客户资料",), True, "waiting_policy", "pause_wait_approval"),
    ]
    for category, title, prompt, schedule_type, time_text, weekday, interval_seconds, terms, trigger, trigger_status, policy_action in sensitive_cases:
        ve, vf = visible(*terms, extra_forbidden=("高风险动作清单", "审批策略"))
        add(
            category,
            title,
            prompt,
            schedule_type=schedule_type,
            time_text=time_text,
            weekday=weekday,
            interval_seconds=interval_seconds,
            trigger=trigger,
            trigger_status=trigger_status,
            policy_action=policy_action,
            visible_expected=ve,
            visible_forbidden=vf,
        )

    complex_cases = [
        ("复杂表达", "英文添加", "Add a reminder: 每天 07:25 提醒我 check vitamins.", "daily", "07:25", None, None, ("check vitamins",), prefix_forbidden),
        ("复杂表达", "英文计划", "Please remind me: 每周周五 16:10 提醒我 review weekly notes.", "weekly", "16:10", "周五", None, ("review weekly notes",), prefix_forbidden),
        ("复杂表达", "中文引号", "帮我设置提醒：“每天 08:18 提醒我看今日安排”。", "daily", "08:18", None, None, ("今日安排",), prefix_forbidden),
        ("复杂表达", "冒号前缀", "定时任务：每周周二 18:35 提醒我检查社群反馈。", "weekly", "18:35", "周二", None, ("社群反馈",), prefix_forbidden),
        ("复杂表达", "中文逗号", "设个提醒，每隔 35 分钟提醒我看看眼睛累不累。", "interval", None, None, 2100, ("眼睛",), prefix_forbidden),
        ("复杂表达", "英文hour", "每隔 4 hours 提醒我 review long-running job.", "interval", None, None, 14400, ("review long-running job",), ()),
        ("复杂表达", "中文冒号时间", "每天 06：08 提醒我把早餐从冰箱拿出来。", "daily", "06:08", None, None, ("早餐",), ()),
        ("复杂表达", "口语下午", "每周周三下午 4 点提醒我补团队周报备注。", "weekly", "16:00", "周三", None, ("团队周报",), ()),
        ("复杂表达", "默认九点", "每天提醒我看一眼今天有没有漏事。", "daily", "09:00", None, None, ("漏事",), ()),
        ("复杂表达", "明天无冒号", "明天上午 8 点提醒我提前十分钟出门。", "once", "08:00", None, None, ("出门",), ()),
    ]
    for category, title, prompt, schedule_type, time_text, weekday, interval_seconds, terms, extra_forbidden in complex_cases:
        ve, vf = visible(*terms, extra_forbidden=extra_forbidden)
        add(
            category,
            title,
            prompt,
            schedule_type=schedule_type,
            time_text=time_text,
            weekday=weekday,
            interval_seconds=interval_seconds,
            visible_expected=ve,
            visible_forbidden=vf,
        )

    non_create_cases = [
        ("边界识别", "问怎么设", "我想知道怎么设置每天提醒喝水，先讲步骤，不要创建提醒。", ("步骤",), ("好，以后", "已创建")),
        ("边界识别", "只改文案", "把“每天 7 点提醒我背单词”改得更像人话，不要设置。", ("背单词",), ("好，以后", "已创建")),
        ("边界识别", "讨论习惯", "如果我每天都忘记喝水，你建议提醒语怎么写？不要建任务。", ("提醒语",), ("好，以后", "已创建")),
        ("边界识别", "取消咨询", "我想删掉一个旧提醒，但现在只问怎么确认它是哪一个。", ("旧提醒",), ("已删除", "好，以后")),
        ("边界识别", "角色口吻", "沿用刚才的姐姐口吻，提醒我今晚早点睡，不要创建系统提醒。", ("今晚",), ("好，以后", "已创建")),
        ("边界识别", "还没定时间", "我想养成记账习惯，但还没想好时间，先问我一个问题。", ("时间",), ("好，以后", "已创建")),
        ("边界识别", "安全解释", "为什么登录后台这类定时只能提醒不能自动做？只解释。", ("登录",), ("好，以后", "已创建")),
        ("边界识别", "模拟回复", "模拟一句你创建提醒后的回复给我看，不要真的创建。", ("模拟",), ("已创建",)),
        ("边界识别", "计划表", "给我一个每天复盘的计划表，不要创建定时任务。", ("计划",), ("好，以后", "已创建")),
        ("边界识别", "澄清问题", "如果用户说每天提醒我运动但没说几点，你应该怎么追问？不要创建。", ("几点",), ("好，以后", "已创建")),
    ]
    for category, title, prompt, expected_terms, forbidden_terms in non_create_cases:
        add(
            category,
            title,
            prompt,
            created=False,
            expected=expected_terms,
            forbidden=forbidden_terms,
            visible_expected=expected_terms[:1],
            visible_forbidden=forbidden_terms,
        )

    if len(rows) != 100:
        raise RuntimeError(f"expected 100 cases, got {len(rows)}")
    return rows


def _scheduled_ids(client: Any) -> set[str]:
    response = client.get("/api/scheduled-tasks", params={"limit": 200})
    if response.status_code != 200:
        raise RuntimeError(response.text)
    return {str(item["scheduled_task_id"]) for item in response.json()["items"]}


def _new_scheduled_task(client: Any, before: set[str]) -> dict[str, Any] | None:
    response = client.get("/api/scheduled-tasks", params={"limit": 200})
    if response.status_code != 200:
        raise RuntimeError(response.text)
    for item in response.json()["items"]:
        if str(item["scheduled_task_id"]) not in before:
            return dict(item)
    return None


def _send_case(client: Any, fake: Any, spec: ScheduledCaseSpec, paired: set[str]) -> ScheduledCaseResult:
    notes: list[str] = []
    BASE._ensure_peer(client, fake, spec.peer_ref, paired)
    previous = BASE._latest_binding(client)
    previous_turn_id = str(previous["turn_id"]) if previous else None
    previous_send_count = fake.send_count()
    before_ids = _scheduled_ids(client)
    event_id = f"evt-{spec.case_id}-{BASE._hash_text(spec.prompt)[:10]}"
    fake.enqueue_event(BASE._text_event(event_id, spec.peer_ref, "ou_sender", spec.prompt))
    routed = client.post("/api/channels/providers/feishu/poll-once")
    if routed.status_code != 200:
        return _failed_result(spec, 0, [f"poll_failed:{routed.status_code}"], routed.text)
    try:
        turn_id = BASE._wait_for_new_turn(client, previous_turn_id)
    except Exception as exc:
        return _failed_result(spec, 0, [f"turn_wait_failed:{exc}"], "")
    for _ in range(4):
        delivered = client.post("/api/channels/providers/feishu/deliver-due")
        if delivered.status_code != 200:
            notes.append(f"deliver_failed:{delivered.status_code}")
        time.sleep(0.1)
    turn = BASE._turn_payload(client, turn_id)
    events = BASE._turn_events(client, turn_id)
    reply = BASE._visible_reply(events)
    model_started, model_completed, _usage_total, brain_id = BASE._model_summary(events)
    route_type, task_status = BASE._route_summary(events)
    delivery_sent = fake.send_count() > previous_send_count
    created = _new_scheduled_task(client, before_ids)
    run_payload: dict[str, Any] | None = None
    if created is not None and spec.trigger_check:
        trigger = client.post(
            f"/api/scheduled-tasks/{created['scheduled_task_id']}/trigger",
            json={"scheduled_for": "2026-05-22T00:00:00+00:00", "reason": spec.case_id},
        )
        if trigger.status_code == 200:
            run_payload = dict(trigger.json())
        else:
            notes.append(f"trigger_failed:{trigger.status_code}:{trigger.text[:120]}")
    score, quality_notes = _score_case(
        spec,
        reply=reply,
        turn=turn,
        events=events,
        created=created,
        run_payload=run_payload,
        model_started=model_started,
        model_completed=model_completed,
        delivery_sent=delivery_sent,
    )
    notes.extend(quality_notes)
    verdict = _verdict(score, notes)
    return ScheduledCaseResult(
        case_id=spec.case_id,
        category=spec.category,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
        verdict=verdict,
        score=score,
        notes=notes,
        reply_text=reply,
        turn_id=turn_id,
        conversation_id=turn.get("conversation_id"),
        trace_id=turn.get("trace_id"),
        route_brain_id=brain_id,
        model_started=model_started,
        model_completed=model_completed,
        delivery_sent=delivery_sent,
        event_types=[str(item["event_type"]) for item in events],
        scheduled_task_id=str(created["scheduled_task_id"]) if created else None,
        scheduled_task_status=str(created["status"]) if created else None,
        schedule=dict(created.get("schedule") or {}) if created else None,
        next_run_at=str(created.get("next_run_at")) if created and created.get("next_run_at") else None,
        run_id=str(run_payload["run_id"]) if run_payload else None,
        run_status=str(run_payload["status"]) if run_payload else None,
        policy_decision=dict(run_payload.get("policy_decision") or {}) if run_payload else None,
        route_type=route_type,
        task_status=task_status,
    )


def _score_case(
    spec: ScheduledCaseSpec,
    *,
    reply: str,
    turn: dict[str, Any],
    events: list[dict[str, Any]],
    created: dict[str, Any] | None,
    run_payload: dict[str, Any] | None,
    model_started: bool,
    model_completed: bool,
    delivery_sent: bool,
) -> tuple[int, list[str]]:
    notes: list[str] = []
    backend_score = 70
    visible_score = 30
    if len(reply.strip()) < spec.min_chars:
        visible_score -= 30
        notes.append("reply_too_short_or_empty")
    if not (model_started and model_completed):
        backend_score -= 18
        notes.append("real_model_not_completed")
    if not delivery_sent:
        backend_score -= 10
        notes.append("delivery_not_sent")
    if str(turn.get("status")) != "completed":
        backend_score -= 18
        notes.append(f"turn_status:{turn.get('status')}")
    if not turn.get("trace_id"):
        backend_score -= 8
        notes.append("missing_turn_trace")
    visible = reply
    for term in (*VISIBLE_BLOCK_TERMS, *spec.forbidden_terms, *spec.visible_forbidden_terms):
        if term and term in visible:
            visible_score -= 30
            notes.append(f"forbidden_term_visible:{term}")
    if VISIBLE_ISO_RE.search(visible):
        visible_score -= 30
        notes.append("forbidden_term_visible:ISO_TIME")
    if spec.expected_created:
        if not _looks_like_natural_scheduled_reply(visible):
            visible_score -= 12
            notes.append("visible_reply_not_natural_confirmation")
    else:
        for term in spec.expected_terms:
            if term and not _visible_term_present(visible, term):
                visible_score -= 6
                notes.append(f"missing_expected_term:{term}")
    for term in spec.visible_expected_terms:
        if term and not _visible_term_present(visible, term):
            visible_score -= 8
            notes.append(f"missing_visible_term:{term}")
    if spec.expected_created and created is None:
        backend_score -= 28
        notes.append("scheduled_task_not_created")
    if not spec.expected_created and created is not None:
        backend_score -= 28
        notes.append("scheduled_task_unexpectedly_created")
    if created is not None:
        schedule = dict(created.get("schedule") or {})
        if not created.get("trace_id"):
            backend_score -= 5
            notes.append("scheduled_task_missing_trace")
        if spec.expected_schedule_type and schedule.get("type") != spec.expected_schedule_type:
            backend_score -= 12
            notes.append(f"schedule_type_mismatch:{schedule.get('type')}")
        if spec.expected_time:
            if schedule.get("type") == "once":
                run_at = str(schedule.get("run_at") or "")
                if f"T{spec.expected_time}" not in run_at:
                    backend_score -= 10
                    notes.append(f"schedule_time_mismatch:{run_at or None}")
            elif schedule.get("time") != spec.expected_time:
                backend_score -= 10
                notes.append(f"schedule_time_mismatch:{schedule.get('time')}")
        if spec.expected_weekday and spec.expected_weekday not in list(schedule.get("days") or []):
            backend_score -= 8
            notes.append(f"schedule_weekday_mismatch:{schedule.get('days')}")
        if (
            spec.expected_interval_seconds is not None
            and int(schedule.get("every_seconds") or 0) != spec.expected_interval_seconds
        ):
            backend_score -= 10
            notes.append(f"schedule_interval_mismatch:{schedule.get('every_seconds')}")
        if not created.get("next_run_at"):
            backend_score -= 6
            notes.append("missing_next_run_at")
    if spec.trigger_check:
        if run_payload is None:
            backend_score -= 14
            notes.append("trigger_run_missing")
        else:
            status = str(run_payload.get("status"))
            policy = dict(run_payload.get("policy_decision") or {})
            if spec.expected_trigger_status and status != spec.expected_trigger_status:
                backend_score -= 12
                notes.append(f"run_status_mismatch:{status}")
            if spec.expected_policy_action and policy.get("action") != spec.expected_policy_action:
                backend_score -= 10
                notes.append(f"policy_action_mismatch:{policy.get('action')}")
            if not run_payload.get("trace_id"):
                backend_score -= 5
                notes.append("scheduled_run_missing_trace")
            if spec.expected_trigger_status == "waiting_policy" and policy.get("auto_start") is not False:
                backend_score -= 10
                notes.append("high_risk_auto_start_not_blocked")
    if spec.expected_created and "scheduled_task_request" not in " ".join(str(item.get("event_type")) for item in events):
        if not any(item.get("event_type") == "intent.detected" for item in events):
            backend_score -= 4
            notes.append("intent_event_missing")
    score = max(0, backend_score) + max(0, visible_score)
    return max(0, min(100, score)), notes


def _looks_like_natural_scheduled_reply(text: str) -> bool:
    visible = str(text or "").strip()
    if not visible:
        return False
    if not visible.startswith(("好，", "可以，", "行，", "没问题，")):
        return False
    if "提醒你" not in visible:
        return False
    if any(marker in visible for marker in ("目标是：", "调度方式", "下一次执行时间", "后台流程")):
        return False
    return True


def _verdict(score: int, notes: list[str]) -> str:
    hard_markers = (
        "poll_failed",
        "turn_wait_failed",
        "reply_too_short_or_empty",
        "real_model_not_completed",
        "turn_status:",
        "scheduled_task_not_created",
        "scheduled_task_unexpectedly_created",
        "run_status_mismatch",
        "high_risk_auto_start_not_blocked",
        "forbidden_term_visible",
    )
    if score < 80 or any(any(marker in note for marker in hard_markers) for note in notes):
        return "fail"
    if score < 92 or notes:
        return "warn"
    return "pass"


def _visible_term_present(visible: str, term: str) -> bool:
    if term in visible:
        return True
    synonyms = {
        "提醒语": ("提醒语", "提示语", "可以写", "这样写", "该喝水", "先喝水"),
        "旧提醒": ("旧提醒", "提醒", "标题", "内容", "时间", "重复规则"),
        "时间": ("时间", "时段", "几点", "固定时刻", "哪个固定", "早上", "饭后", "睡前"),
        "登录": ("登录", "后台", "账号", "权限", "自动执行"),
        "模拟": ("模拟", "示例", "示范", "例如"),
    }
    return any(item in visible for item in synonyms.get(term, ()))


def _failed_result(spec: ScheduledCaseSpec, score: int, notes: list[str], reply: str) -> ScheduledCaseResult:
    return ScheduledCaseResult(
        case_id=spec.case_id,
        category=spec.category,
        title=spec.title,
        peer_ref=spec.peer_ref,
        prompt=spec.prompt,
        verdict="fail",
        score=score,
        notes=notes,
        reply_text=reply,
    )


def _avg(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _write_caseset(cases: list[ScheduledCaseSpec]) -> None:
    lines = [
        "# 定时场景 100 个可见回复质量第二轮真实模型测试用例",
        "",
        "- 入口：飞书 mock 渠道，经 `poll-once -> channel ingress -> chat turn -> deliver-due`。",
        "- 模型要求：每个聊天场景必须经过真实大脑，检查 `model.started` 与 `model.completed`。",
        "- 后端核验：定时任务创建、schedule 归一化、next_run_at、trace、飞书投递、低/高风险触发策略。",
        "- 可见质量核验：自然确认、目标净化、敏感边界、英文入口清理、无内部字段/UTC/ISO 泄漏。",
        "- 覆盖：生活提醒、工作办公、学习成长、家庭事务、运营内容、安全审批、边界识别、复杂表达、运行策略。",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"## {case.case_id} {case.title}",
                f"- 分类：{case.category}",
                f"- 飞书 peer：`{case.peer_ref}`",
                f"- 输入：{case.prompt}",
                f"- 期望创建：{'是' if case.expected_created else '否'}",
                f"- 期望 schedule：`{case.expected_schedule_type or '-'}` `{case.expected_time or case.expected_interval_seconds or '-'}`",
                f"- 触发核验：{'是' if case.trigger_check else '否'}",
                "",
            ]
        )
    CASESET_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_gap_queue(results: list[ScheduledCaseResult]) -> None:
    problematic = [item for item in results if item.verdict != "pass"]
    buckets: dict[str, int] = {}
    for item in problematic:
        for note in item.notes:
            key = str(note).split(":", 1)[0]
            buckets[key] = buckets.get(key, 0) + 1
    lines = [
        "# 定时场景缺口与修复队列",
        "",
        f"- 非 pass 场景：{len(problematic)}",
        "- 修复原则：优先修通用解析、调度策略、可见回复和 trace，不按单个 case 写死。",
        "",
        "## 缺口聚类",
        "",
    ]
    if buckets:
        for key, count in sorted(buckets.items(), key=lambda item: (-item[1], item[0])):
            lines.append(f"- `{key}`：{count}")
    else:
        lines.append("- 暂无。")
    lines.extend(["", "## 明细", ""])
    for item in problematic:
        lines.append(f"- `{item.case_id}` {item.title} {item.verdict}/{item.score}：{', '.join(item.notes) or '-'}")
    GAP_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_outputs(
    results: list[ScheduledCaseResult],
    *,
    model_verify: dict[str, Any],
    cases: list[ScheduledCaseSpec],
) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    _write_caseset(cases)
    _write_gap_queue(results)
    passed = sum(1 for item in results if item.verdict == "pass")
    warned = sum(1 for item in results if item.verdict == "warn")
    failed = sum(1 for item in results if item.verdict == "fail")
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_category.setdefault(item.category, {"total": 0, "pass": 0, "warn": 0, "fail": 0})
        bucket["total"] += 1
        bucket[item.verdict] += 1
    summary = {
        "run_label": "SCHEDULED100-ROUND2-VISIBLE-REAL-20260522",
        "entry": "feishu_mock_channel",
        "real_model_required": True,
        "model_endpoint": MODEL_PROXY_ENDPOINT,
        "model_verify": {
            key: value
            for key, value in model_verify.items()
            if key not in {"message", "verify_capabilities"}
        },
        "quality_rubric": {
            "backend_correctness": 70,
            "visible_reply_quality": 30,
            "visible_hard_fail_terms": list(VISIBLE_BLOCK_TERMS),
            "visible_hard_fail_patterns": ["ISO_TIMESTAMP"],
            "visible_semantic_checks": [
                "natural_confirmation_prefix",
                "remind_you_not_remind_me",
                "goal_keywords_retained",
                "sensitive_boundary_notice",
                "entry_prefix_removed",
            ],
        },
        "total": len(results),
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "score_avg": _avg([item.score for item in results]),
        "model_completed_cases": sum(1 for item in results if item.model_completed),
        "delivery_sent_cases": sum(1 for item in results if item.delivery_sent),
        "trace_count_cases": sum(1 for item in results if item.trace_id),
        "created_count": sum(1 for item in results if item.scheduled_task_id),
        "trigger_checked_count": sum(1 for item in results if item.run_id),
        "by_category": by_category,
        "results": [asdict(item) for item in results],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 定时场景 100 个可见回复质量第二轮真实模型测试报告",
        "",
        f"- 运行标签：`{summary['run_label']}`",
        f"- 场景数：{len(results)} / 100",
        f"- 真实模型预检：{summary['model_verify']}",
        f"- 结果：pass {passed} / warn {warned} / fail {failed}",
        f"- 平均分：{summary['score_avg']}",
        f"- 模型完成：{summary['model_completed_cases']} / {len(results)}",
        f"- 飞书投递：{summary['delivery_sent_cases']} / {len(results)}",
        f"- trace：{summary['trace_count_cases']} / {len(results)}",
        f"- 创建定时任务：{summary['created_count']}",
        f"- 触发核验：{summary['trigger_checked_count']}",
        "",
        "## 分类统计",
        "",
        "| 分类 | 总数 | Pass | Warn | Fail |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, stats in by_category.items():
        lines.append(f"| {category} | {stats['total']} | {stats['pass']} | {stats['warn']} | {stats['fail']} |")
    lines.extend(
        [
            "",
            "## 明细",
            "",
            "| Case | 分类 | 场景 | 判定 | 分数 | 模型 | 投递 | Schedule | Run | 备注 |",
            "|---|---|---|---:|---:|---|---|---|---|---|",
        ]
    )
    for item in results:
        model = "ok" if item.model_started and item.model_completed else "no"
        delivered = "ok" if item.delivery_sent else "no"
        schedule = "-"
        if item.schedule:
            schedule = f"{item.schedule.get('type')} {item.schedule.get('time') or item.schedule.get('every_seconds') or ''}".strip()
        run = item.run_status or "-"
        lines.append(
            f"| {item.case_id} | {item.category} | {item.title} | {item.verdict} | {item.score} | "
            f"{model} | {delivered} | {schedule} | {run} | {', '.join(item.notes) or '-'} |"
        )
    lines.extend(["", "## 样例回复摘录", ""])
    for item in results[:20]:
        preview = item.reply_text.replace("\n", " ")[:240]
        lines.append(f"- `{item.case_id}` {item.verdict}/{item.score}: {preview}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def run(*, limit: int | None = None) -> list[ScheduledCaseResult]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    data_dir = BASE._copy_runtime_data()
    temp_root = data_dir.parent
    old_env = {
        key: os.environ.get(key)
        for key in [
            "CYCBER_ROOT",
            "CYCBER_DATA_DIR",
            "CYCBER_BROWSER_EXECUTOR",
            "FEISHU_APP_ID",
            "FEISHU_APP_SECRET",
            "USERPROFILE",
            "HOME",
        ]
    }
    try:
        os.environ["CYCBER_ROOT"] = str(ROOT_DIR)
        os.environ["CYCBER_DATA_DIR"] = str(data_dir)
        os.environ["CYCBER_BROWSER_EXECUTOR"] = "http_fallback"
        os.environ["FEISHU_APP_ID"] = "scheduled100-round2-visible-real-app"
        os.environ["FEISHU_APP_SECRET"] = "scheduled100-round2-visible-real-secret"
        os.environ["USERPROFILE"] = str(data_dir / "home")
        os.environ["HOME"] = str(data_dir / "home")
        (data_dir / "home" / "Desktop").mkdir(parents=True, exist_ok=True)
        verify_payload = BASE._verify_real_model_subprocess(data_dir)
        cases = _cases()
        if limit is not None:
            cases = cases[:limit]
        _write_caseset(cases)
        if verify_payload.get("status_code") != 200 or verify_payload.get("status") != "healthy":
            _write_outputs([], model_verify=verify_payload, cases=cases)
            raise RuntimeError(f"real model verify failed: {verify_payload}")
        with BASE.TestClient(BASE.create_app()) as client:
            BASE._bind_feishu(client)
            fake = BASE._install_fake_feishu(client)
            paired: set[str] = set()
            results: list[ScheduledCaseResult] = []
            for case in cases:
                try:
                    results.append(_send_case(client, fake, case, paired))
                except Exception as exc:
                    results.append(
                        _failed_result(
                            case,
                            0,
                            [f"case_exception:{type(exc).__name__}:{str(exc)[:160]}"],
                            "",
                        )
                    )
            _write_outputs(results, model_verify=verify_payload, cases=cases)
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
                "gap_queue": str(GAP_PATH),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
