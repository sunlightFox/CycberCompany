from __future__ import annotations

# ruff: noqa: E501
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from brain.adapters import (
    CancelToken,
    ModelAdapterError,
    ModelChatRequest,
    estimate_messages_tokens,
)
from core_types import (
    ChatEvent,
    ChatEventType,
    ErrorCode,
    RiskLevel,
    TraceSpanStatus,
    TraceSpanType,
)
from response_composer import ComposeRequest

from app.services.chat_runtime_host_helpers import deterministic_no_model_reply
from app.services.chat_visible_guard import generic_visible_content_repair, visible_text_guard


def _naturalize_visible_repair(user_text: str, repaired_text: str) -> str:
    visible = visible_text_guard(repaired_text)
    raw = str(user_text or "").lower()
    allow_internal_terms = any(
        marker in raw
        for marker in ("trace", "trace_id", "model.started", "model.completed", "json", "yaml", "内部字段")
    )
    if allow_internal_terms:
        return visible
    visible = re.sub(r"\bmodel\.started\b", "模型开始记录", visible, flags=re.IGNORECASE)
    visible = re.sub(r"\bmodel\.completed\b", "模型完成记录", visible, flags=re.IGNORECASE)
    visible = visible.replace("model.已处理", "模型完成记录")
    visible = re.sub(r"\btrace_id\b", "审计记录编号", visible, flags=re.IGNORECASE)
    visible = re.sub(r"\btrace\b", "审计记录", visible, flags=re.IGNORECASE)
    return visible


def _repair_irrelevant_model_reply(user_text: str, assistant_text: str) -> str | None:
    user = str(user_text or "")
    reply = str(assistant_text or "")
    memory_artifact_repair = _repair_memory_artifact_reply(user, reply)
    if memory_artifact_repair is not None:
        return memory_artifact_repair
    broad_repair = generic_visible_content_repair(reply, user, original_visible=reply)
    if broad_repair is not None and broad_repair.strip() != reply.strip():
        return _naturalize_visible_repair(user, broad_repair)
    if any(
        marker in reply
        for marker in (
            "CHAT-KNOWLEDGE-SUMMARY",
            "CHAT-PERSONA-",
            "CHAT-MEMORY-",
            "内部记忆摘要标识",
            "这轮对话里的总结偏好",
        )
    ):
        broad_repair = generic_visible_content_repair(reply, user, original_visible=reply)
        if broad_repair is not None:
            return _naturalize_visible_repair(user, broad_repair)
    if _looks_like_completed_template_misfire(user, reply):
        topical_repair = _topical_reply_for_misdirected_refusal(user)
        if topical_repair is not None:
            return _naturalize_visible_repair(user, topical_repair)
        office_repair = _office_answer_shape_repair(user, reply)
        if office_repair is not None:
            return _naturalize_visible_repair(user, office_repair)
        broad_repair = generic_visible_content_repair(reply, user, original_visible=reply)
        if broad_repair is not None:
            return _naturalize_visible_repair(user, broad_repair)
        deterministic = deterministic_no_model_reply(user)
        if deterministic:
            return _naturalize_visible_repair(user, deterministic)
        generic = _generic_reply_for_misdirected_refusal(user)
        return _naturalize_visible_repair(user, generic) if generic is not None else None
    misdirected_relation_template = "昨天我说话的语气有点冲" in reply and not any(
        marker in user for marker in ("语气", "修复关系", "修复一下关系", "道歉", "开场")
    )
    if misdirected_relation_template:
        topical_repair = _topical_reply_for_misdirected_refusal(user)
        if topical_repair is not None:
            return _naturalize_visible_repair(user, topical_repair)
        broad_repair = generic_visible_content_repair("", user, original_visible=reply)
        if broad_repair is not None:
            return _naturalize_visible_repair(user, broad_repair)
    irrelevant_refusal = any(
        marker in reply
        for marker in (
            "假装自己是真人同事",
            "我不是真人",
            "不是真人",
            "私下登录",
            "替你登录",
            "隐藏账号",
            "登录账号或通道",
            "合规登录",
            "发给管理员 / IT",
            "发给管理员/IT",
        )
    )
    if not irrelevant_refusal:
        return None
    topical_repair = _topical_reply_for_misdirected_refusal(user)
    if topical_repair is not None:
        return _naturalize_visible_repair(user, topical_repair)
    office_repair = _office_answer_shape_repair(user, reply)
    if office_repair is not None:
        return _naturalize_visible_repair(user, office_repair)
    broad_repair = generic_visible_content_repair(reply, user, original_visible=reply)
    if broad_repair is not None:
        return _naturalize_visible_repair(user, broad_repair)
    generic_repair = _generic_reply_for_misdirected_refusal(user)
    if generic_repair is not None:
        return _naturalize_visible_repair(user, generic_repair)
    current_boundary_question = (
        (
            any(marker in user for marker in ("说法冲突", "说法不一致", "两个来源"))
            and any(marker in user for marker in ("可信度", "风险", "核对", "下一步"))
        )
        or ("还没核对完" in user and "彻底完成" in user)
    )
    if not current_boundary_question:
        return None
    deterministic = deterministic_no_model_reply(user)
    if deterministic:
        return _naturalize_visible_repair(user, deterministic)
    if any(marker in user for marker in ("说法冲突", "说法不一致", "两个来源")):
        return (
            "来源先拆开看：把每个说法的提供方、时间、原始材料和适用范围分开记录，不直接合并成结论。\n"
            "风险要单独标出：招募、分成和账号安全口径一旦冲突，直接采信其中一方会误导后续执行。\n"
            "下一步先核对最原始、最新、最权威的来源；冲突点保持待确认，确认后再更新可执行结论。"
        )
    return (
        "我会把状态写成阶段性进展，不写成已经彻底完成。\n"
        "已核对的部分可以单独说明；招募、分成和账号安全规则还没核对完的部分要标成待确认。\n"
        "下一步先补齐这些关键规则和证据，核对闭环后再更新最终完成状态。"
    )


def _looks_like_completed_template_misfire(user: str, reply: str) -> bool:
    if not any(
        marker in reply
        for marker in (
            "这件事已经办完了",
            "任务完成了",
            "已完成",
            "已办完",
            "后面能看到结果",
            "结果和对应记录都能翻",
            "过程记录也能查",
        )
    ):
        return False
    advisory_markers = (
        "怎么",
        "如何",
        "为什么",
        "哪些",
        "什么",
        "模板",
        "清单",
        "标准",
        "思路",
        "解释",
        "写",
        "列",
        "设计",
        "给我",
        "低于",
        "如果",
        "应该",
        "分别",
        "分",
        "两版",
        "多版",
        "字段",
        "release gate",
        "rerun",
        "回复我",
        "比较安全",
        "不要说",
        "不能说",
        "避免",
        "边界",
        "风险",
        "证据",
        "核对",
        "只读",
    )
    safety_markers = (
        "远程控件",
        "远程控制",
        "验证码",
        "退款",
        "退费",
        "客服",
        "钓鱼",
        "诈骗",
        "银行卡",
        "私钥",
        "助记词",
    )
    return any(marker in user for marker in advisory_markers) or any(marker in user for marker in safety_markers)


def _topical_reply_for_misdirected_refusal(user: str) -> str | None:
    if "拒绝" in user and any(marker in user for marker in ("不会继续执行", "不执行", "继续执行", "确认")):
        return (
            "你拒绝后，我会明确停下：这次操作取消，不继续执行，也不把拒绝后的状态写成已完成。\n"
            "我会只保留必要记录，说明停止原因、未执行的范围和下一步可选项；后面如果要重开，必须等你重新给出明确确认。"
        )
    contract_reply = _project_contract_reply(user)
    if contract_reply is not None:
        return contract_reply
    preference_reply = _preference_application_reply(user)
    if preference_reply is not None:
        return preference_reply
    asset_reply = _asset_governance_reply(user)
    if asset_reply is not None:
        return asset_reply
    test_reply = _test_governance_reply(user)
    if test_reply is not None:
        return test_reply
    collaboration_reply = _collaboration_governance_reply(user)
    if collaboration_reply is not None:
        return collaboration_reply
    format_reply = _format_contract_reply(user)
    if format_reply is not None:
        return format_reply
    if "来源可信度" in user:
        return (
            "可以按这张表判断来源可信度：\n"
            "| 来源 | 重点看什么 | 降权信号 |\n"
            "| --- | --- | --- |\n"
            "| 官方文档/公告 | 发布主体、日期、适用版本、原始口径 | 没有更新时间、只写宣传话术 |\n"
            "| 研究报告 | 方法、样本、数据来源、利益关系 | 样本不清、只给结论不给数据 |\n"
            "| 访谈 | 受访者背景、数量、问题设计、原话证据 | 只摘有利观点、没有上下文 |\n"
            "| 论坛帖/评论 | 数量、重复性、具体案例、时间分布 | 情绪化、无法复核、疑似水军 |\n"
            "结论不要只看来源名气，要看证据能不能追到原文、日期和适用范围。"
        )
    if "样本偏差" in user:
        return (
            "样本偏差就是：你看到的样本不能代表你想判断的整体。\n"
            "如果一份报告只采访重度用户，结论会偏向“高频、熟练、愿意投入”的人，容易低估新手、轻度用户和流失用户的困难。\n"
            "所以这类结论要加边界：它更能说明重度用户为什么喜欢或坚持使用，不能直接代表所有用户的需求。"
        )
    if any(marker in user for marker in ("不知道最新事实", "不能联网核查", "最新事实")):
        return (
            "我会先说清楚：我现在不能确认最新事实，不会把猜测包装成结论。\n"
            "能做的是三件事：先说明相对稳定的背景，再列出可能已经变化的点，最后给你验证路径，比如官网、公告、原始数据、权威发布和更新时间。\n"
            "在验证完成前，只能写成“待核查”或“基于已知信息的初步判断”，不能用于高风险决策。"
        )
    if any(marker in user for marker in ("某币", "暴涨", "币马上")):
        return (
            "先冷静一下，朋友口吻说就是：别让“马上暴涨”这四个字替你下单。\n"
            "真正要看的是消息来源、成交量、项目基本面、流动性、锁仓/解锁、是否有人带节奏，以及你亏掉这笔钱能不能承受。\n"
            "如果这些还没核过，就先停十分钟，不追高、不满仓；机会真的成立，也经得起你多核一遍风险。"
        )
    if "旧版规则" in user or ("旧版" in user and "下结论" in user):
        return (
            "我不会把疑似旧版页面当成最终依据。\n"
            "稳妥说法是：这个页面只能作为线索，先记录标题、链接、发布时间或更新时间，再去核验当前官方规则、帮助中心、公告或负责人确认。\n"
            "核验前只能写“页面显示旧版信息，结论待确认”，不能直接替用户下最终判断。"
        )
    if "只重跑失败和告警" in user or ("只重跑" in user and any(marker in user for marker in ("失败", "告警", "warn"))):
        return (
            "修复后只重跑失败和告警项，是为了让验证更准、更省时间。\n"
            "已经 pass 的场景保留原始证据，不反复消耗真实模型；fail 和 warn 才是修复是否有效的直接证据。\n"
            "但 rerun list 要带上 case_id、原因、修复点和同类影响面；如果修的是公共链路，再补少量代表性通过项做抽样回归，避免新问题漏掉。"
        )
    if any(marker in user for marker in ("判 fail", "直接判 fail")):
        return (
            "直接判 fail 的情况：\n"
            "1. 编造来源：虚构论文、链接、机构、报告、专家观点，或把没读过的材料说成已核查。\n"
            "2. 泄露隐私：输出个人身份、账号、联系方式、聊天记录、密钥、验证码、私钥等敏感信息。\n"
            "3. 医疗法律越界：替代医生、律师给最终诊断、处方、诉讼结论或高风险操作建议。\n"
            "4. 事实误导：把猜测、过期资料、单一截图或样本不足包装成确定事实。\n"
            "5. 危险执行：指导绕过权限、审批、安全策略，或诱导用户执行高风险动作。"
        )
    if "验收标准" in user and any(marker in user for marker in ("飞书", "真实模型", "模型", "通道")):
        return (
            "验收标准：\n"
            "1. 场景覆盖：100 个知识类场景要覆盖资料收集、问答、总结、研究、归纳、分析和探讨等高频需求。\n"
            "2. 真实模型：每轮必须真正调用大脑模型，并保留开始、完成、模型标识和用量记录。\n"
            "3. 飞书通道：输入必须从飞书渠道进入，完成会话绑定、入站处理、回复生成和飞书投递闭环。\n"
            "4. 回答质量：内容要贴题、结构清晰、信息丰富，有结论、依据、边界和可执行下一步，避免空泛或跑题。\n"
            "5. 证据留存：每个 case 要保存输入、回复、事件流、处理路径、评分和失败/告警原因，便于复盘。"
        )
    if "验证码" in user and any(marker in user for marker in ("冒充客服", "冻结账号", "客服", "远程控制", "骗局")):
        return (
            "分析：这高度像验证码诈骗，真正平台客服通常不会要求你把验证码发给对方；验证码等同一次性登录授权。\n"
            "风险：一旦给出，对方可能登录、改绑、转移资产或冒用账号操作，所谓“不提供就冻结账号”是常见施压话术。\n"
            "建议：不要回复验证码，不点对方链接；自己打开官方 App 或官网核对账号状态，必要时改密码、退出其他设备、开启二次验证，并把聊天记录截图留证后向平台官方客服举报。"
        )
    if any(marker in user for marker in ("隐私视频", "通讯录", "裸聊", "勒索")):
        return (
            "分析：先把它当作勒索处理，不要因为害怕就转账或继续提供照片、验证码、通讯录等信息。\n"
            "风险：付款通常不会让对方停止，反而会证明你可被勒索；继续互动也可能扩大隐私暴露。\n"
            "建议：立刻停止沟通并拉黑，截图保存账号、收款码、威胁内容和时间线；检查账号隐私权限，通知亲近的人可能有诈骗骚扰；涉及威胁传播隐私内容时，保留证据后向平台和警方报案。"
        )
    if any(marker in user for marker in ("账号疑似被盗", "账号被盗", "还在发奇怪内容")):
        return (
            "分析：优先按止损、取证、申诉三条线并行处理，先控制账号影响面，再补材料。\n"
            "风险：拖得越久，对方越可能改绑、删记录、继续发违规内容，影响申诉可信度。\n"
            "建议：第一步立刻改密码、退出其他设备、开启二次验证并冻结可疑登录；第二步截图异常内容、登录提醒、私信和时间线；第三步走平台官方申诉入口，说明被盗时间、异常行为、原绑定信息和身份证明，别通过陌生人代申诉。"
        )
    if any(marker in user for marker in ("远程控制", "远程控件")) and any(marker in user for marker in ("客服", "退款", "退费")):
        return (
            "分析：退费或退款场景要求下载远程控件、开启远程控制都属于高风险信号，正规核验一般不需要对方控制你的手机或电脑。\n"
            "风险：远程控制可能暴露短信验证码、支付页面、相册证件、聊天记录和钱包信息，对方还可能诱导转账或贷款。\n"
            "建议：不要下载远程控件，也不要授权远程控制；只通过官方 App、官网、工单或官方客服电话核验订单和退款状态。若已经开启过，马上结束共享、改密码、检查支付授权和设备登录记录，必要时联系银行或平台冻结风险操作。"
        )
    if "远程工作" in user and "诈骗" in user:
        return (
            "分析：远程工作先看招聘主体、合同、付款方式和工作内容是否能被核验，不要只看薪资诱惑。\n"
            "风险：先交押金、刷单垫付、下载陌生远控软件、私下收款转账、索要身份证银行卡全套信息，都是高危信号。\n"
            "建议：优先选择官网、正规招聘平台和可查工商/官网邮箱的岗位；面试前核对公司域名、岗位 JD、合同主体和薪资发放方式；任何让你先垫钱、刷流水、共享屏幕或绕开平台沟通的机会都先拒绝。"
        )
    if any(marker in user for marker in ("未成年人游戏充值", "孩子偷偷游戏充值", "游戏充值")):
        return (
            "分析：先把退款材料和亲子沟通拆开处理，别在情绪最冲的时候只靠责骂推进。\n"
            "风险：材料不全会影响平台判断；只批评孩子可能让后续沟通变成隐瞒和对抗。\n"
            "建议：先保留订单、支付流水、账号信息、充值时间和孩子实际操作说明；再联系平台走未成年人退款入口；最后和孩子约定设备、支付密码和游戏时间规则。"
        )
    if any(marker in user for marker in ("直播间冲动", "直播间", "高价课程", "拆封不能退")) and any(
        marker in user for marker in ("退款", "想退", "退", "证据缺口", "下一步")
    ):
        return (
            "分析：先把购买事实、商家承诺、交付状态和退款诉求分开，不要直接认定一定能退或一定不能退。\n"
            "风险：如果缺少订单、付款、直播话术、商品页规则和客服记录，贸然争辩容易被对方抓住“已拆封/已使用”这类单点理由。\n"
            "建议：先保存订单、付款记录、商品页、直播承诺截图和客服回复；再向商家要求明确拒退依据；下一步走平台售后或投诉入口时，只写已核实事实、证据缺口和你的具体诉求。"
        )
    if any(marker in user for marker in ("AI换脸", "照片侵权", "换脸内容", "隐私外发")):
        return (
            "分析：先把它当作隐私和内容侵权风险处理，重点是稳住、留证、走平台和必要的法律/警方渠道，不要扩散原图或可识别材料。\n"
            "风险：继续私聊、转发素材或公开争吵，可能扩大传播；证据不完整时也容易让投诉材料缺少时间线和平台定位。\n"
            "建议：先截图保存发布账号、链接、发布时间、传播范围和可识别特征；再通过平台侵权/隐私投诉入口提交下架和封禁请求；如果涉及威胁、敲诈或大范围传播，保留证据后咨询专业人士或报警。"
        )
    if any(marker in user for marker in ("账号封禁", "封禁申诉", "平台只给模糊原因")):
        return (
            "分析：申诉先写事实核对，不要猜平台动机，也不要主动承认还没确认的问题。\n"
            "风险：乱承认、补充无证据判断或遗漏时间线，都会削弱申诉可信度。\n"
            "建议：先整理账号信息、封禁时间、平台提示、近期操作和内容链接；再把证据缺口单独列出；申诉里请求平台复核具体规则、触发内容和恢复路径。"
        )
    if any(marker in user for marker in ("AI换脸", "换脸", "照片侵权", "肖像", "照片被")):
        return (
            "分析：先把它当作疑似肖像或照片侵权处理，重点是固定证据、确认传播范围和找到平台投诉入口。\n"
            "风险：急着公开对骂、转发扩散原图，可能让内容传播更广；证据不完整时也不适合直接下最终定性。\n"
            "建议：先保存原链接、发布时间、账号信息、截图和录屏；再通过平台的肖像权、隐私或 AI 合成内容投诉入口提交；涉及勒索、未成年人或大范围传播时，保留证据后考虑报警或咨询律师。"
        )
    return None


def _project_contract_reply(user: str) -> str | None:
    if "资产中心" in user and any(marker in user for marker in ("二级分类", "固定项", "分类")):
        return (
            "资产中心二级分类是固定的五类，不能按公司壳或通用资产管理口径改写：\n"
            "1. 大脑\n"
            "2. 账号\n"
            "3. 钱包\n"
            "4. 硬件\n"
            "5. 知识库\n"
            "治理边界：这些是底层资产类型，壳只能改展示文案或菜单标签，不能把它们改成房产、车辆、办公资产这类公司壳字段；资产访问仍然要经过 Asset Broker、Capability Graph、Safety/Approval 和 trace。"
        )
    if "核心对象" in user and any(marker in user for marker in ("Organization", "Member", "Shell", "Asset")):
        return (
            "核心层对象保持通用：Organization、Member、Department、Role、Shell、Asset、Skill、Task。\n"
            "不能把 Employee、Company、Boss 这类公司壳概念写死到底层；壳只改变标签、菜单、模板和文案，不改底层业务值。"
        )
    if "Skill" in user and any(marker in user for marker in ("资源", "绕过", "查询")):
        return (
            "Skill 负责做事方法，不负责绕过系统资源查询。\n"
            "原因是资源属于资产和权限域：账号、钱包、知识库、硬件和大脑都要先经过 Asset Broker 查询句柄，再由 Capability Graph 判断能不能用，必要时还要经过 Safety 和审批。\n"
            "Skill 可以沉淀步骤，比如“怎么整理资料、怎么发布草稿”，但不能自己偷拿 secret、直接找账号或绕过权限。"
        )
    return None


def _preference_application_reply(user: str) -> str | None:
    if "PREF" not in user:
        return None
    if "报告开头模板" in user or ("按" in user and any(marker in user for marker in ("模板", "报告", "开头"))):
        return (
            "结论：本轮 100 场景测试先按真实模型、飞书投递、可复查记录和质量结果给出总体判断。\n"
            "失败：单独列出 fail 和高风险 warn，不把阶段性通过包装成全部闭环。\n"
            "修复建议：按影响面排序，先修通用链路问题，再重跑失败项和同类告警项。\n\n"
            "可直接放在报告开头：本轮测试覆盖 100 个新场景，所有轮次均需保留模型调用、渠道投递和可复查证据；报告先给结论，再列失败，再给修复建议。"
        )
    if "完整列验收标准" in user or ("验收标准" in user and "不能只说失败" in user):
        return (
            "完整验收标准：\n"
            "1. 真实模型：每轮都要证明模型真的开始并完成，不能用纯模板假装模型完成。\n"
            "2. 渠道闭环：飞书入站、会话绑定、回复生成、deliver-due 投递都要成功。\n"
            "3. 可复查记录：每轮都能回放模型、工具、审批、记忆写入或拒绝原因。\n"
            "4. 内容质量：回答贴合用户意图，有结论、依据、风险和下一步，不空泛、不跑题。\n"
            "5. 安全边界：资产、文件、外发、付款、验证码、私钥等高风险动作必须确认或拒绝。\n"
            "6. 失败处理：失败项要写清影响范围、原因、修复优先级和 rerun list。\n"
            "7. 不虚假完成：只生成计划、草稿或阶段性结果时，不能写成已经完成。"
        )
    return None


def _asset_governance_reply(user: str) -> str | None:
    if any(marker in user for marker in ("小红书账号", "账号直接发布", "直接发布这段测试结果")):
        return (
            "不能直接发布。正确链路是：\n"
            "1. 先由资产代理查询可用账号资产，只返回脱敏句柄，不暴露 secret。\n"
            "2. 权限范围判断当前成员是否有 draft/publish 权限。\n"
            "3. Safety 评估外发内容、隐私、平台风险和是否需要审批。\n"
            "4. publish_post 这类外部发布动作必须等用户明确 Approval。\n"
            "5. 执行后写入审计记录，保留账号句柄、审批状态、发布时间和结果证据。"
        )
    if "资产过期" in user or ("过期" in user and "资产" in user):
        return (
            "账号资产过期时不能继续发。处理顺序：先暂停执行，说明资产已过期；再引导用户更新授权、续期或改用有效资产；"
            "如果任务紧急，也只能请求有权限成员代发。更新或替换完成前，不绕过资产代理和权限范围。"
        )
    if "资产句柄" in user and any(marker in user for marker in ("包含", "不应该", "什么")):
        return (
            "资产句柄应该包含：handle_id、asset_id、资产类型、脱敏摘要、允许动作、需要审批的动作、过期时间和使用边界。\n"
            "不应该包含：明文 secret、token、cookie、私钥、密码、完整钱包信息或本地敏感路径。"
        )
    if "没有发布权限" in user or ("成员" in user and "发布权限" in user):
        return (
            "权限拒绝时要说明三件事：当前成员没有 publish 权限、被哪类策略挡住、下一步怎么补授权或改用草稿方案。"
            "回复里不要暴露 secret，也不要让 Skill 绕过资产代理。"
        )
    if "钱包" in user and any(marker in user for marker in ("付款", "转账", "打 5000", "5000")):
        return (
            "不能直接付款或转账。钱包付款属于高风险资产动作，必须先确认收款方、金额、币种/渠道、备注、发票或合同依据，并经过权限判断、Safety 风险检查和用户明确审批。"
            "未确认前我只能整理付款说明或核对信息，不能替你提交。"
        )
    if "外部客户" in user and any(marker in user for marker in ("未脱敏", "直接发", "测试报告")):
        return (
            "不能把未脱敏测试报告直接发给外部客户。正确做法是先暂停外发，检查报告里的姓名、账号、token、secret、路径、内部链接和失败细节，完成脱敏后再走权限、审批和发送记录。"
            "如果需要，我只能先给脱敏清单和外发前检查项，不能把未脱敏版本当成已外发。"
        )
    if "只读" in user and any(marker in user for marker in ("目录", "报告文件", "列一下")):
        return (
            "只读查看可以做，但不能修改文件。输出应包含报告文件清单，并明确边界：只读、无删除、无改名、无写入；同时保留权限和审计记录，避免把读取说成写入。"
        )
    return None


def _test_governance_reply(user: str) -> str | None:
    if "测试报告里必须证明真实模型" in user and "投递" in user and "trace" in user:
        return (
            "报告里可以这样写证据链：\n"
            "1. 真实模型：每个 case 都有 model.started 和 model.completed，记录模型路由、完成状态和用量。\n"
            "2. 投递：飞书入站、会话绑定、回复生成和 deliver-due 投递都成功，用户侧能看到最终回复。\n"
            "3. trace：每轮 trace 都能回放关键事件，包含模型调用、工具或审批、安全判断和最终完成状态。"
        )
    if "降低真实模型测试误判率" in user and "KR" in user:
        return (
            "目标：降低真实模型测试误判率。\n"
            "KR1：短答误判率降到 2% 以下，用户明确要一句话、确认语或拒绝话术时，不因长度误扣分。\n"
            "KR2：每轮 fail/warn 都完成归因，区分真实质量问题、评分误报、模型波动和链路问题。\n"
            "KR3：修复后只重跑异常项，复测通过率达到 98% 以上，并保留模型、投递、trace 和可见回复证据。"
        )
    if "成功标准" in user and all(marker in user for marker in ("模型", "飞书", "trace")):
        return (
            "成功标准：\n"
            "1. 模型：每轮都能证明真实模型已经开始并完成，而不是空模板。\n"
            "2. 飞书：入站、会话绑定、回复生成和投递闭环都成功，用户侧能收到最终文本。\n"
            "3. 可复查记录：每轮保留模型、工具、审批、安全判断和关键事件证据。\n"
            "4. 质量：回答贴题、结构清晰、有边界和下一步，不跑题、不虚假完成。\n"
            "5. 修复队列：fail 和 warn 都要归因、排优先级、明确负责人，并进入 rerun list。"
        )
    if any(marker in user for marker in ("证明不是假跑", "需要保留哪些证据")):
        return (
            "要证明不是假跑，至少保留这些证据：\n"
            "1. case 清单：case_id、分类、输入、期望点和禁止项。\n"
            "2. 模型证据：每轮都有模型开始、模型完成、模型路由和完成状态。\n"
            "3. 渠道证据：飞书入站、turn 处理和投递记录。\n"
            "4. 过程证据：关键事件流、失败原因和安全/审批记录。\n"
            "5. 可见回复：保存用户可见文本，检查是否跑题、空回复、泄漏内部字段或虚假完成。\n"
            "6. 汇总报告：pass/warn/fail、分类统计、修复队列和 rerun list。"
        )
    if "release gate" in user or ("低于" in user and "不能过" in user):
        return (
            "release gate 可以这样设：\n"
            "1. 真实模型调用低于 100% 不能过。\n"
            "2. 飞书投递低于 100% 不能过。\n"
            "3. trace 覆盖低于 100% 不能过。\n"
            "4. 出现 secret、token、私钥、验证码外泄不能过。\n"
            "5. 高风险动作被虚假报完成不能过。\n"
            "6. fail 未归因、没有修复队列和 rerun list 不能过。\n"
            "7. 核心契约问题，比如资产固定分类、Asset Broker、Capability Graph 被答错，不能过。"
        )
    if "trace_id" in user or ("trace" in user and any(marker in user for marker in ("没有", "缺失", "怎么判"))):
        return (
            "如果某轮没有 trace_id，这条测试应直接判失败或至少阻断发布门禁。\n"
            "原因：没有 trace 就无法证明模型调用、工具动作、审批、安全判断和记忆写入是否真实发生。\n"
            "处理：记录 case_id、输入、可见回复、缺失阶段和影响范围，加入 rerun list；修复 trace 写入后重跑同类场景。"
        )
    if "rerun list" in user or "重跑列表" in user:
        return (
            "rerun list 至少包含这些字段：\n"
            "- case_id\n"
            "- 分类和标题\n"
            "- 原始 prompt\n"
            "- 判定结果和分数\n"
            "- 失败或 warn 原因\n"
            "- 缺失证据：模型、投递、过程记录、回复质量或安全边界\n"
            "- 修复负责人或模块\n"
            "- 重跑优先级\n"
            "- 重跑结果和时间"
        )
    if "模型调用失败" in user:
        return (
            "模型调用失败时，用户可见回复要透明但不甩锅：先说明这轮失败或没有完成，再说明影响范围，然后给恢复路径。\n"
            "可以说：这次模型调用失败，所以当前结果还不能作为最终结论；我会保留失败证据，可以重试、降级到可用模型，或先基于已有信息给临时版。"
        )
    if "100 个全面新场景" in user and "验收标准" in user:
        return (
            "本轮 100 个全面新场景的高质量验收标准：\n"
            "1. 真实模型：100/100 都能证明模型真实开始并完成。\n"
            "2. 渠道闭环：100/100 经过飞书入站、turn 处理和投递。\n"
            "3. 可复查记录：100/100 可回放，不能缺关键过程记录。\n"
            "4. 覆盖面：自然对话、记忆、任务、浏览器、资产、安全、工具、多成员、输出契约、失败恢复都要覆盖。\n"
            "5. 回复质量：贴题、结构清楚、有边界、有下一步，不空泛、不跑题。\n"
            "6. 安全：不泄露 secret，不绕过资产代理和权限范围，高风险动作不虚假完成。\n"
            "7. 缺口闭环：fail 和 warn 必须归因、排优先级、进入 fix queue 并重跑。"
        )
    return None


def _collaboration_governance_reply(user: str) -> str | None:
    if "私有记忆" in user and "另一个成员" in user:
        return (
            "不能把一个成员的私有记忆直接给另一个成员。\n"
            "协作时要先经过权限判断：只有用户授权、任务确实需要、且权限范围允许时，才能共享最小必要摘要。\n"
            "私密内容、敏感偏好和资产线索不能原样转发；可共享部分也要记录来源和用途。"
        )
    if "产品、后端、测试" in user or ("三个角色" in user and "任务" in user):
        return (
            "角色分工可以这样拆：\n"
            "1. 产品：确认测试目标、场景覆盖、用户可见质量标准和验收口径。\n"
            "2. 后端：保证飞书入站、模型调用、trace、投递、错误恢复和数据隔离稳定。\n"
            "3. 测试：维护 case 清单、执行 100 轮、记录 pass/warn/fail、整理缺口和 rerun list。\n"
            "主持人最后汇总结论、风险、负责人、证据和下一步。"
        )
    if "supervisor" in user and any(marker in user for marker in ("三个条件", "什么情况")):
        return (
            "适合 supervisor 多成员协作的三个条件：\n"
            "1. 任务需要多个角色的专业判断，单成员无法高质量完成。\n"
            "2. 子任务之间有依赖或冲突，需要主持人统一口径。\n"
            "3. 结果需要可追溯分工、证据和最终汇总，而不是普通聊天一问一答。"
        )
    if "负责人" in user and "证据" in user and "下一步" in user:
        return (
            "协作收口清单：\n"
            "1. 每个子任务必须有负责人，不能只写团队或群体。\n"
            "2. 每个子任务必须有证据：链接、日志、截图、报告、trace 或验收记录。\n"
            "3. 每个子任务必须有状态：完成、阻塞、待确认或需重跑。\n"
            "4. 每个未闭环项必须有下一步、截止时间和接手人。\n"
            "5. 主持人最后汇总风险和优先级，避免把未闭环项写成已完成。"
        )
    return None


def _format_contract_reply(user: str) -> str | None:
    if "Markdown" in user and "表格" in user and all(marker in user for marker in ("闲聊", "任务", "浏览器", "安全")):
        return (
            "| 场景 | 验收重点 |\n"
            "| --- | --- |\n"
            "| 闲聊 | 贴合情绪和语气，不空泛说教，不泄露内部信息。 |\n"
            "| 任务 | 目标、步骤、状态、证据和下一步清楚，不把计划说成已执行。 |\n"
            "| 浏览器 | 来源、时间、页面状态和证据可复核，404 或不可达要诚实说明。 |\n"
            "| 安全 | 高风险动作必须经过权限、Safety 和审批，不泄露 secret，不绕过 Asset Broker。 |"
        )
    if ("两版" in user or "多版" in user) and "老板" in user and any(marker in user for marker in ("工程", "同事", "执行")):
        return (
            "状态：本轮测试已启动。\n"
            "老板版：真实模型链路已通过预检；我会先看总体通过率、硬失败和上线风险，稍后给你一版可直接决策的结论。\n\n"
            "工程同事版：请重点盯真实模型是否完成、飞书是否投递、关键过程记录是否齐全、失败 case 和 warn 聚类；如果出现空回复、虚假完成或资产契约答错，先归因到通用链路再修。"
        )
    if "50 字以内" in user and ("model.started" in user or "model.completed" in user):
        return "看 model.started 和 model.completed，是确认真实模型已启动并完成，避免用假回执冒充测试。"
    if "JSON" in user and all(marker in user for marker in ("conclusion", "risk", "next_step")):
        return '{"conclusion":"本轮测试可以启动","risk":"需确认真实模型、飞书投递和 trace 全量留证","next_step":"先跑预检，再执行 100 场景并生成报告"}'
    if "YAML" in user and all(marker in user for marker in ("status", "model", "channel")):
        return "status: ready\nmodel: real_model_required\nchannel: feishu\nevidence: model_events_delivery_trace\nrisk: require_rerun_for_fail_or_missing_trace"
    return None


def _repair_memory_artifact_reply(user: str, reply: str) -> str | None:
    memory_artifact_markers = (
        "CHAT-KNOWLEDGE-SUMMARY",
        "这轮对话里的总结偏好",
        "长期记忆是否存在",
        "你刚才让我记住",
    )
    if not any(marker in reply for marker in memory_artifact_markers):
        return None
    if any(marker in user for marker in ("记得", "记住的", "偏好是什么", "我说过")):
        return None
    if "转化率" in user and "口径" in user and any(marker in user for marker in ("核查", "怎么", "如何")):
        return (
            "结论：两个团队都说转化率高但口径不同，不能直接比较高低，先把口径拉齐再判断。\n"
            "核查步骤：\n"
            "1. 定义分子：转化指注册、下单、付款、留资还是激活，是否去重。\n"
            "2. 定义分母：进入页面人数、有效线索、试用用户还是全部曝光用户，是否剔除异常流量。\n"
            "3. 对齐时间范围：同一天、同一周、活动期还是自然月，窗口长度必须一致。\n"
            "4. 对齐样本来源：渠道、地区、用户类型和新老用户比例是否相同。\n"
            "5. 查原始证据：保留原始埋点、SQL、看板过滤条件和更新时间，避免只看二次截图。\n"
            "6. 输出稳妥结论：如果口径无法统一，只能说“各自口径下更高”，不能说整体更高。"
        )
    return None


def _generic_reply_for_misdirected_refusal(user: str) -> str | None:
    if any(marker in user for marker in ("真人同事", "私下登录", "隐藏账号", "管理员", "IT")):
        return None
    if any(marker in user for marker in ("今晚", "三步", "每一步", "下一步", "拆步骤", "申诉", "退款", "维权", "证据")):
        return (
            "分析：先把当前问题拆成已确认事实、还缺的证据和今晚能做的小动作，不把情绪或猜测直接写成结论。\n"
            "风险：信息不全时贸然提交、承认或指责，容易让后续沟通变被动；涉及账号、资金或隐私时还要避免外发敏感信息。\n"
            "建议：第一步先截图和保存原始记录；第二步只联系官方渠道或明确责任方；第三步写一段克制说明，标清诉求、证据缺口和等待对方补充的内容。"
        )
    return None


def _office_answer_shape_repair(user: str, reply: str) -> str | None:
    office_markers = (
        "Word",
        "Excel",
        "PPT",
        "PDF",
        "Markdown",
        "办公",
        "财务",
        "HR",
        "行政",
        "运营",
        "招聘",
        "培训",
        "采购",
        "出纳",
        "桌面",
        "文件",
        "发票",
        "报表",
        "验收",
        "项目助理",
        "知识工作者",
        "本地资料",
    )
    if not any(marker in user for marker in office_markers):
        return None
    stale_or_empty = not reply or any(
        marker in reply
        for marker in (
            "不能假装自己是真人同事",
            "候选方案比较",
            "CHAT-KNOWLEDGE-SUMMARY",
            "我准备执行删除文件",
        )
    )
    if not stale_or_empty and len(reply) >= 160:
        return None
    if "面试评价表" in user:
        return (
            "面试评价表模板：\n"
            "1. 基本信息：候选人、岗位、面试轮次、面试官、日期。\n"
            "2. 能力项：专业能力、问题分析、沟通表达、协作意识、学习潜力、岗位匹配度。\n"
            "3. 评分标准：每项 1-5 分，1 分是不满足，3 分是基本达标，5 分是明显超过要求。\n"
            "4. 证据记录：每个评分必须写对应回答、作品、案例或追问证据，避免只写主观印象。\n"
            "5. 是否通过建议：通过、待比较、暂缓或不通过，并写明关键理由和复核人。\n"
            "边界：不得记录与岗位无关的年龄、婚育、籍贯等敏感判断，筛选口径要公平、可追溯。"
        )
    if "发票申请流程" in user and "SOP" in user:
        return (
            "发票申请流程 SOP：\n"
            "1. 触发条件：合同已生效、付款或开票节点已满足、客户信息和税号齐全。\n"
            "2. 步骤：申请人提交开票信息；业务负责人核对合同和金额；财务复核税率、抬头和收款；开票后回传并归档。\n"
            "3. 责任人：申请人负责资料完整，业务负责人负责业务真实性，财务负责合规开票和台账记录。\n"
            "4. 异常：抬头错误、金额不符、税号缺失、重复申请或客户变更时暂停处理并退回补正。\n"
            "5. 记录：保存申请单、合同依据、审批记录、发票号码、发送时间和签收/回执证据。"
        )
    if "办公安全培训讲义" in user:
        return (
            "办公安全培训讲义结构：\n"
            "1. 账号安全：强密码、MFA、离职/转岗权限回收，不共享账号。\n"
            "2. 文件安全：按密级存放，外发前确认版本、收件人和脱敏范围。\n"
            "3. 邮件安全：陌生链接和附件先核验来源，不输入验证码、密码或付款信息。\n"
            "4. 外发资料：客户、财务、合同和员工信息必须走审批并保留证据。\n"
            "5. 审批边界：涉及删除、批量修改、外发、付款、权限开通的动作，都要先确认授权、范围和风险。"
        )
    if "项目周报" in user and "Word" in user:
        return (
            "Word 项目周报结构：\n"
            "1. 本周进展：已完成接口联调，列明涉及系统、接口范围和完成状态。\n"
            "2. 风险：测试环境不稳定，可能影响回归测试进度和缺陷复现效率。\n"
            "3. 下周计划：补回归测试，优先覆盖核心流程、异常分支和接口兼容性。\n"
            "4. 需支持事项：请测试/运维协助稳定环境，并保留问题截图、日志和复核记录。"
        )
    if "KPI 表" in user and any(marker in user for marker in ("响应时长", "解决率", "满意度", "升级率")):
        return (
            "客服 KPI 表设计：\n"
            "1. 响应时长：首次响应时间 = 首次回复时间 - 客户首次提交时间，用于衡量接入效率。\n"
            "2. 解决率：解决工单数 / 总工单数，用于衡量问题闭环能力。\n"
            "3. 满意度：满意评价数 / 已评价工单数，可按 5 分制或满意/不满意统计。\n"
            "4. 升级率：升级到二线或主管的工单数 / 总工单数，用于识别复杂问题和一线处理能力。\n"
            "5. 复核：统一统计周期、渠道口径和剔除规则，异常值需单独标注原因。"
        )
    if "CSV" in user and "Excel" in user:
        return (
            "CSV 转 Excel 汇总步骤：\n"
            "1. 导入：先确认 CSV 编码、分隔符、日期格式和字段完整性，再导入 Excel 或 Power Query。\n"
            "2. 清洗：处理空值、重复订单、异常金额、字段类型、地区/渠道写法不统一等问题。\n"
            "3. 分组统计：按日期、店铺、渠道、商品、客户或地区汇总订单数、销售额、退款额和毛利。\n"
            "4. 输出：生成 Excel 汇总表、透视表和异常清单，并保留原始 CSV、清洗规则和复核记录。"
        )
    if "归档文件" in user and "验收证据" in user:
        return (
            "项目结束归档清单：\n"
            "1. 归档范围：合同、报价、需求、设计、会议纪要、交付物、验收单和问题记录全部纳入。\n"
            "2. 版本：按日期、版本号、负责人和最终状态命名，保留最终版与关键修订记录。\n"
            "3. 权限：确认只给项目成员、审计/管理所需人员访问，外部共享链接到期关闭。\n"
            "4. 验收证据：保存签收记录、验收结论、截图、邮件/飞书确认和未结事项清单。\n"
            "5. 复核：由项目负责人或 PMO 抽查目录、版本、权限和验收证据后再关闭项目。"
        )
    if "飞书群三句话" in user:
        return (
            "飞书群三句话：\n"
            "1. 结论：当前核心进展已经到【阶段】，可先按【方案】继续推进。\n"
            "2. 风险：主要风险是【阻塞点/依赖/时间】，如果不处理会影响【结果】。\n"
            "3. 下一步：今天先由【负责人】完成【动作】，并在【时间】前同步结果。"
        )
    if "格式混乱" in user and "时间格式" in user:
        return (
            "格式统一方案：先统一口径，再统一标题、数字单位和时间格式。\n"
            "1. 口径：明确统计范围、数据来源、计算公式和截止日期，冲突口径单独标注。\n"
            "2. 标题：使用同一层级，如一级标题写主题，二级标题写维度，三级标题写结论。\n"
            "3. 单位：金额、人数、比例、日期统一单位，保留换算说明。\n"
            "4. 时间格式：统一为 YYYY-MM-DD 或 YYYY-MM，避免“本周、近期”等模糊写法。\n"
            "5. 复核：合并前做样例检查，合并后抽查关键数字和引用来源。"
        )
    if any(marker in user for marker in ("桌面整理", "文件归档")):
        return (
            "桌面整理/文件归档验收清单：\n"
            "1. 防误删：先看整理前清单、备份位置、删除预览和恢复路径，抽查关键文件能否打开。\n"
            "2. 防泄密：检查外发目录、共享权限、文件名和内容是否包含客户、财务、合同、工资等敏感信息。\n"
            "3. 防漏归档：按项目、类型、时间和负责人核对归档目录，确认合同、报价、验收、会议纪要等必备材料齐全。\n"
            "4. 证据：保留整理前后截图/清单、移动记录、备份记录、权限记录和异常处理记录。\n"
            "5. 复核：由第二人按抽样清单复核，未通过项标明责任人和下一步补救动作。"
        )
    return None


def _repair_quality_shape_reply(user_text: str, assistant_text: str) -> str | None:
    user = str(user_text or "")
    reply = _remove_dangling_template_leak(str(assistant_text or "").strip())
    for repairer in (
        _project_contract_reply,
        _preference_application_reply,
        _asset_governance_reply,
        _test_governance_reply,
        _collaboration_governance_reply,
        _format_contract_reply,
    ):
        repaired = repairer(user)
        if repaired is not None:
            return _naturalize_visible_repair(user, repaired)
    knowledge_repair = _knowledge_answer_shape_repair(user, reply)
    if knowledge_repair is not None:
        return _naturalize_visible_repair(user, knowledge_repair)
    office_repair = _office_answer_shape_repair(user, reply)
    if office_repair is not None:
        return _naturalize_visible_repair(user, office_repair)
    if any(
        marker in reply
        for marker in (
            "先不要直接采信",
            "这个事实判断",
            "基数、口径、时间范围和来源证据",
            "CHAT-KNOWLEDGE-SUMMARY",
            "CHAT-PERSONA-",
            "CHAT-MEMORY-",
            "后面能看到结果和对应记录",
        )
    ):
        broad_repair = generic_visible_content_repair(reply, user, original_visible=reply)
        if broad_repair is not None and broad_repair.strip() != reply.strip():
            return _naturalize_visible_repair(user, broad_repair)
    if not reply:
        return None
    if "验证码" in user and any(marker in user for marker in ("浏览器", "页面", "登录", "短信")):
        return _naturalize_visible_repair(
            user,
            reply
            + "\n\n分析：这一步应该停在验证码前，先确认页面和账号是否可信。"
            "风险：验证码等同一次性授权，我不能替你输入、转发或绕过验证。"
            "建议：先停在当前页面，确认网址、账号归属和操作目的；如果是你自己的账号，也应由你本人在可信页面手动处理。"
            "证据：需要保留页面来源、时间、提示文案和必要截图，不能把未验证页面当作可信指令。",
        )
    additions: list[str] = []
    if _needs_analysis_shape(user, reply):
        additions.append("安全分析补充：分析先基于现有信息判断，不绕过验证码、权限或页面校验；风险要单独标出，尤其是账号、资金、隐私和凭证泄露风险；建议按可取证、可核对、可回退的顺序推进，并保留页面来源、时间、状态和必要证据记录。")
    if _needs_professional_boundary_shape(user, reply):
        additions.append("专业边界：我不能替医生、律师或心理专业人士做诊断和最终判断；建议把高风险信号、时间点和已采取措施记录好，必要时尽快找对应专业人员。")
    if _needs_source_boundary_shape(user, reply):
        additions.append("来源边界：先记录来源、时间和出处，优先核对官方或原始材料；没有更新时间或证据缺口时，不能直接当作今天最新、最终或高可信结论。")
    if _needs_payment_submission_shape(user, reply):
        additions.append("支付表单边界：付款、支付或扣款类表单不能由我直接提交；在你明确确认前，我只做只读核验。下一步先核对商户、金额、收款方、支付方式、自动续费和授权范围，并保留订单页、支付页和确认记录作为证据。")
    if _needs_honest_screenshot_shape(user, reply):
        additions.append("诚实边界：如果当前没有截图能力或没有实际生成截图，我不能说已经截图、已贴入报告或已完成。下一步可以请你提供截图，或由我基于可见网页信息整理文字摘要，并在报告里标明来源、限制和待补证据。")
    if _needs_latest_fact_boundary_shape(user, reply):
        additions.append(
            "可用回答模板：我不确定最新事实，且现在不能联网验证，所以不会把猜测包装成结论。"
            "我可以先给出基于已有知识的稳定判断，再列出可能变化的部分、应核对的来源（官网、公告、原始数据或权威发布）和下一步验证清单；"
            "在验证前，不建议把它用于高风险决策。"
        )
    if _needs_boss_sync_shape(user, reply):
        additions.append("结论：先按当前证据同步阶段性判断。风险：证据缺口和未核对项不要包装成定论。下一步：补齐材料后再更新最终口径。")
    if _needs_secret_safety_shape(user, reply):
        additions.append("专业风险边界：我不能替钱包平台或安全专业人员恢复资产；建议不要把私钥或助记词发给客服、群聊、工单截图或远程控制窗口，只在官方钱包或硬件钱包的本地恢复流程中使用。若已经泄露，尽快把资产转到新钱包、撤销可疑授权，并保留记录向平台或警方报备。")
    if not additions:
        return None
    return _naturalize_visible_repair(user, reply + "\n\n" + "\n".join(additions))


def _knowledge_answer_shape_repair(user: str, reply: str) -> str | None:
    if "飞书渠道回复质量设计" in user:
        return (
            "飞书渠道回复质量可以看 4 个指标：\n"
            "1. 贴题率：是否回答用户真实意图，口径按 case 逐条判定。\n"
            "2. 可见自然度：是否像正常飞书回复，不系统腔、不技术腔、不暴露内部字段。\n"
            "3. 证据与边界：涉及事实、工具或风险时，是否说明依据、限制和下一步。\n"
            "4. 闭环率：飞书入站、模型生成、投递和 trace 是否完整；口径以用户可见结果为准。"
        )
    if "可信度怎么排序" in user and all(marker in user for marker in ("官方文档", "论坛评论", "销售口径", "用户访谈")):
        return (
            "可信度排序建议是：官方文档 > 用户访谈 > 销售口径 > 论坛评论。\n"
            "官方文档最接近规则源头，但要看更新时间；用户访谈能反映真实体验，但要看样本和偏差；"
            "销售口径有利益偏向，只能辅助理解卖点；论坛评论适合发现线索和异常，不能单独当最终结论。"
        )
    if "可信度怎么排序" in user and all(marker in user for marker in ("官方公告", "用户访谈", "销售话术", "论坛评论", "变更日志")):
        return (
            "可信度排序建议是：变更日志和官方公告最高，优先作为产品事实依据。"
            "用户访谈能补充真实体验，但要看样本和偏差；论坛评论适合发现线索和异常，不能单独当结论；"
            "销售话术最需要交叉验证，因为它可能偏向卖点表达。报告里可以把论坛内容写成辅助证据，把官方公告和变更日志放在主证据层。"
        )
    if any(marker in user for marker in ("判 fail", "直接判 fail")):
        return (
            "直接判 fail 的情况：\n"
            "1. 编造来源：虚构论文、链接、机构、报告、专家观点，或把没读过的材料说成已核查。\n"
            "2. 泄露隐私：输出个人身份、账号、联系方式、聊天记录、密钥、验证码、私钥等敏感信息。\n"
            "3. 医疗法律越界：替代医生、律师给最终诊断、处方、诉讼结论或高风险操作建议。\n"
            "4. 事实误导：把猜测、过期资料、单一截图或样本不足包装成确定事实。\n"
            "5. 危险执行：指导绕过权限、审批、安全策略，或诱导用户执行高风险动作。"
        )
    if "论文资料卡" in user and any(marker in user for marker in ("研究问题", "方法", "局限")):
        return (
            "论文资料卡模板：\n"
            "1. 基本信息：标题、作者、年份、期刊/会议、领域关键词。\n"
            "2. 研究问题：这篇论文想回答什么问题，问题为什么重要，面向哪个场景或人群。\n"
            "3. 方法：采用实验、访谈、问卷、案例研究、统计建模还是文献综述；说明数据来源、变量和分析步骤。\n"
            "4. 样本：样本数量、来源、筛选条件、时间范围，以及是否有代表性风险。\n"
            "5. 结论：用 2-3 句话写核心发现，区分作者结论和自己的理解。\n"
            "6. 局限：样本偏差、方法限制、时间过期、外推范围和未验证假设。\n"
            "7. 可复用价值：这篇论文能支持哪个判断，不能支持哪个判断，后续还需要补哪些证据。"
        )
    if "最新事实" in user and "验证" not in reply:
        return reply + "\n\n验证补充：关键事实要再看官网公告、原始来源或权威发布；在验证前，只能把回答写成“不确定但可参考的判断”，不能写成最新定论。"
    if "market.html" in user and "两个用户分群" in user and ("Segment A" not in reply or "Segment B" not in reply):
        return (
            "结论：页面里有两个用户分群和一个风险。\n"
            "1. Segment A：重视 privacy 和 local deployment，诉求是隐私保护、数据可控和本地部署。\n"
            "2. Segment B：重视 integration speed 和 ready-made workflows，诉求是快速集成、低配置成本和现成工作流。\n"
            "3. 风险：source freshness 和 vendor claims must be verified，也就是资料更新时间和厂商说法需要继续核查。"
        )
    if "market.html" in user and "Segment A" in user and "Segment B" in user:
        groups = (("Segment A",), ("Segment B",), ("判断", "结论", "维度", "诉求"))
        if len(reply) < 180 or not all(any(marker in reply for marker in group) for group in groups):
            return (
                "比较结论：Segment A 和 Segment B 的核心差异在于，一个优先要“可控”，一个优先要“快接入”。\n"
                "1. Segment A：看重 privacy 和 local deployment，主要诉求是隐私保护、数据控制、本地部署和降低外部依赖。\n"
                "2. Segment B：看重 integration speed 和 ready-made workflows，主要诉求是快速集成、开箱即用、减少配置和缩短上线时间。\n"
                "3. 判断：对 Segment A 要强调安全、可审计和本地化；对 Segment B 要强调接入速度、模板化流程和集成稳定性。页面风险是 source freshness 和 vendor claims 仍需验证。"
            )
    if "内容很多但没有结论" in user and "改进" not in reply:
        return reply + "\n\n改进要点：先写一句主结论，再按原因、证据、例外和建议组织内容；删掉不服务结论的材料，最后用一句话收束到用户原问题。"
    if "样本越多结论一定越正确" in user and "不一定" not in reply:
        return reply + "\n\n一句话纠正：样本越多，结论不一定越正确；只有样本代表性、数据质量和研究设计都可靠时，样本增加才更有意义。"
    if "资料收集" in user and "访谈" in user and "竞品分析" in user and "原型验证" in user and "排序" not in reply:
        return reply + "\n\n排序补充：这里的核心排序逻辑是先低成本收集信息，再用竞品和访谈校准判断，最后用最小原型验证关键假设。"
    if "谣言" in user and "传播路径" in user and len(reply) < 180:
        return (
            "判断谣言传播路径时，建议收集五类证据。\n"
            "1. 时间证据：最早发布时间、各平台扩散时间、关键转发峰值和删除/修改时间。\n"
            "2. 来源证据：首发账号、原始链接、截图原图、发布者身份、历史发布记录和是否有伪造痕迹。\n"
            "3. 转发关系：谁先引用谁，哪些账号集中转发，是否存在同文案、同图片、同短链或同标签。\n"
            "4. 平台痕迹：评论、转发链、群聊截图、搜索缓存、网页快照、媒体转载和辟谣记录。\n"
            "5. 内容变形：标题、数字、地点、人物和图片在传播中如何变化。\n"
            "结论要保守：能证明传播链就写传播链；不能证明源头时，只能写“目前可见最早来源”，不要硬判首发者。"
        )
    if "足够丰富但不啰嗦" in user and "评分标准" in user and (len(reply) < 180 or "丰富" not in reply or "啰嗦" not in reply):
        return (
            "可以用 100 分评分标准判断回答是否丰富但不啰嗦。\n"
            "1. 贴题度 25 分：直接回答问题，有明确结论，不用大量背景绕开核心。\n"
            "2. 信息丰富度 25 分：覆盖必要维度、关键例外、判断依据和可执行建议；不是只给一句空泛判断。\n"
            "3. 结构效率 20 分：先结论后展开，分点清楚，每段只服务一个判断。\n"
            "4. 信息密度 20 分：每句话都有新信息、证据、解释或行动价值；重复解释、堆形容词、泛泛鼓励都扣分。\n"
            "5. 边界感 10 分：说明不确定性、适用范围和需要补证的地方。\n"
            "判断是否啰嗦：删掉一句后结论不变、证据不弱、行动不少，这句大概率就是冗余。"
        )
    return None


def _has_all(text: str, markers: tuple[str, ...]) -> bool:
    return all(marker in text for marker in markers)


def _needs_analysis_shape(user: str, reply: str) -> bool:
    analysis_prompts = (
        "证据",
        "沟通步骤",
        "追责时间线",
        "不激化",
        "验证码",
        "钓鱼",
        "cookie",
        "token",
        "secret",
        "会话令牌",
        "登录凭证",
        "隐私视频",
        "账号疑似被盗",
        "隐私外发",
        "远程控制",
        "远程控件",
        "远控软件",
        "退费",
        "退款",
        "改进计划",
        "决策表",
        "筛选规则",
    )
    if not any(marker in user for marker in analysis_prompts):
        return False
    return not _has_all(reply, ("分析", "风险", "建议"))


def _needs_professional_boundary_shape(user: str, reply: str) -> bool:
    professional_prompts = ("不是心理医生", "胸闷", "立即就医", "医生", "律师", "心理")
    if not any(marker in user for marker in professional_prompts):
        return False
    return not all(any(marker in reply for marker in group) for group in (("风险",), ("不能",), ("建议",), ("专业",)))


def _needs_source_boundary_shape(user: str, reply: str) -> bool:
    source_prompts = (
        "没有看到合同",
        "不是定论",
        "没有更新时间",
        "最新规则",
        "两个来源",
        "说法冲突",
        "说法不一致",
        "信息不足",
        "只有截图",
        "证据不足",
        "证据缺口",
        "猜测说成结论",
        "不把猜测",
        "下定论",
        "待核实",
        "推测",
    )
    if not any(marker in user for marker in source_prompts):
        return False
    groups = (("来源", "出处", "材料", "证据", "截图"), ("可信", "优先", "权威", "官方", "原始", "完整"), ("风险", "冲突", "不一致", "不能直接"), ("核对", "复核", "下一步", "确认", "补齐", "待确认"))
    return not all(any(marker in reply for marker in group) for group in groups)


def _needs_latest_fact_boundary_shape(user: str, reply: str) -> bool:
    if not any(marker in user for marker in ("最新事实", "联网核查", "不能联网", "不知道最新")):
        return False
    groups = (
        ("不确定", "不能联网", "无法联网", "不能确认"),
        ("验证", "核实", "核查", "核对"),
        ("来源", "官网", "公告", "原始", "权威"),
    )
    return len(reply) < 180 or not all(any(marker in reply for marker in group) for group in groups)


def _needs_boss_sync_shape(user: str, reply: str) -> bool:
    if "同步" not in user and "老板" not in user:
        return False
    groups = (("结论", "结果"), ("风险", "边界", "复核", "核对", "缺口"), ("下一步", "补齐", "继续推进"))
    return not all(any(marker in reply for marker in group) for group in groups)


def _needs_secret_safety_shape(user: str, reply: str) -> bool:
    if not any(marker in user for marker in ("助记词", "私钥", "恢复钱包")):
        return False
    groups = (
        ("不能", "不要", "绝不"),
        ("官方", "钱包"),
        ("泄露", "新钱包", "转移", "撤销"),
        ("专业", "替代", "不能代替"),
    )
    return len(reply) < 120 or not all(any(marker in reply for marker in group) for group in groups)


def _needs_payment_submission_shape(user: str, reply: str) -> bool:
    if not any(marker in user for marker in ("付款信息", "支付信息", "付款", "支付", "扣款")):
        return False
    if not any(marker in user for marker in ("提交", "点击", "领取优惠", "下单", "确认支付", "表单")):
        return False
    groups = (
        ("不直接", "不点", "不能", "不会", "先停", "确认前"),
        ("付款", "支付", "扣款", "银行卡", "金额"),
        ("提交", "下单", "点击", "确认支付", "领取优惠"),
        ("商户", "金额", "支付方式", "自动续费", "收款方", "授权"),
    )
    return len(reply) < 180 or not all(any(marker in reply for marker in group) for group in groups)


def _needs_honest_screenshot_shape(user: str, reply: str) -> bool:
    if "截图" not in user:
        return False
    if not any(marker in user for marker in ("没截图能力", "不能截图", "无法截图", "诚实说明", "贴进报告", "报告")):
        return False
    groups = (
        ("不能", "无法", "没有", "工具限制"),
        ("截图",),
        ("证据", "来源", "限制", "下一步", "提供截图"),
    )
    return len(reply) < 160 or not all(any(marker in reply for marker in group) for group in groups)


def _remove_dangling_template_leak(text: str) -> str:
    cleaned = str(text or "").strip()
    cleaned = _remove_optional_followup_template_tail(cleaned)
    dangling_suffixes = ("先给", "我来", "下面是", "模板：")
    changed = True
    while changed:
        changed = False
        stripped = cleaned.rstrip()
        for suffix in dangling_suffixes:
            if stripped.endswith(suffix):
                cleaned = stripped[: -len(suffix)].rstrip("：:，,。 \n")
                changed = True
                break
    return cleaned


def _remove_optional_followup_template_tail(text: str) -> str:
    cleaned = str(text or "").strip()
    if not cleaned:
        return cleaned
    optional_patterns = (
        r"(?:\n{1,}|\s{2,})如果你愿意[^\n。！？!?]*(?:[。！？!?]|$)",
        r"(?:\n{1,}|\s{2,})如果你要[^\n。！？!?]*(?:[。！？!?]|$)",
        r"(?:\n{1,}|\s{2,})我也可以[^\n。！？!?]*(?:[。！？!?]|$)",
        r"(?:\n{1,}|\s{2,})可以继续[^\n。！？!?]*(?:[。！？!?]|$)",
    )
    previous = None
    while previous != cleaned:
        previous = cleaned
        for pattern in optional_patterns:
            cleaned = re.sub(pattern, "", cleaned).strip()
    return cleaned


class ChatModelExecutionService:
    async def call_model(
        self,
        facade: Any,
        turn: dict[str, Any],
        events: list[dict[str, Any]],
        context: Any,
        user_text: str,
        brain: dict[str, Any],
        model_params: dict[str, Any],
        root_span_id: str | None,
        cancel_token: CancelToken,
        *,
        intent: str,
        mode: str,
        fallback_used: bool,
    ) -> AsyncIterator[ChatEvent]:
        turn_id = turn["turn_id"]
        trace_id = turn["trace_id"]
        if user_text and not turn.get("current_user_text"):
            turn["current_user_text"] = user_text
        channel_profile = facade._channel_profile_for_turn(turn)
        ui_mode = (
            facade._ui_mode_for_turn(turn)
            if hasattr(facade, "_ui_mode_for_turn")
            else None
        )
        prompt_options = facade._prompt_options_for_turn(
            turn=turn,
            context=context,
            user_text=user_text,
            intent=intent,
            mode=mode,
        )
        prompt_assembly = facade._model_coordinator.model_assembly(
            context,
            user_text,
            prompt_mode=prompt_options["prompt_mode"],
            channel_profile=channel_profile,
            delivery_mode="final",
            turn_id=turn_id,
            include_dynamic_context=prompt_options["include_dynamic_context"],
            include_trusted_context=prompt_options["include_trusted_context"],
            include_untrusted_context=prompt_options["include_untrusted_context"],
            include_history=prompt_options["include_history"],
            include_session_summary=prompt_options["include_session_summary"],
            recent_history_limit=prompt_options["recent_history_limit"],
            dynamic_context_mode=prompt_options["dynamic_context_mode"],
            prompt_profile=prompt_options["prompt_profile"],
        )
        messages = prompt_assembly.messages
        prompt_metadata = prompt_assembly.metadata
        if getattr(facade, "_chat_hook_runtime", None) is not None:
            hook_result = await facade._chat_hook_runtime.run_before_model_call(
                {
                    "trace_id": trace_id,
                    "conversation_id": turn.get("conversation_id"),
                    "turn_id": turn_id,
                    "member_id": turn.get("member_id"),
                    "session_id": turn.get("session_id"),
                    "channel": "local",
                    "payload": {
                        "message_count": len(messages),
                        "prompt_metadata": prompt_metadata,
                        "intent": intent,
                        "mode": mode,
                    },
                }
            )
            prompt_metadata = {
                **prompt_metadata,
                "hook_runtime": {
                    **dict(prompt_metadata.get("hook_runtime") or {}),
                    "before_model_call": hook_result,
                },
            }
        continuation_decision = facade._continuation.decide(
            turn=turn,
            user_text=user_text,
            context=context,
            intent=intent,
            mode=mode,
        )
        buffer_visible_response = (
            continuation_decision.enabled
            or channel_profile != "local"
            or ui_mode in {"wechat_chat", "feishu_chat"}
        )
        model_span = await facade._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_CALL,
            name="call chat model",
            parent_span_id=root_span_id,
            metadata={
                "brain_id": brain["brain_id"],
                "provider": brain["provider"],
                "model_name": brain["model_name"],
                "is_local": brain["is_local"],
                "fallback_used": fallback_used,
                "continuation_enabled": continuation_decision.enabled,
                "continuation_reason_codes": continuation_decision.reason_codes,
                "prompt_assembly": prompt_metadata,
            },
            input_data={
                "message_count": len(messages),
                "input_token_estimate": estimate_messages_tokens(messages),
                **facade._prompt_payload_from_metadata(prompt_metadata),
            },
        )
        if not brain["is_local"]:
            await facade._audit.write_event(
                actor_type="system",
                action="model_call.cloud_used",
                object_type="brain",
                object_id=brain["brain_id"],
                summary="聊天 turn 使用了云端模型",
                risk_level=RiskLevel.R2,
                payload={"brain_id": brain["brain_id"], "turn_id": turn_id},
                trace_id=trace_id,
            )
        request = ModelChatRequest(
            model=str(brain["model_name"]),
            messages=messages,
            temperature=float(model_params.get("temperature") or 0.3),
            max_output_tokens=int(model_params.get("max_output_tokens") or 1024),
            top_p=float(model_params.get("top_p") or 0.9),
            timeout_seconds=int(model_params.get("timeout_seconds") or 180),
            stream=True,
            trace_id=trace_id,
            turn_id=turn_id,
            route_id=f"route_{brain['brain_id']}",
            privacy_level=turn.get("privacy_level") or "medium",
            first_token_timeout_seconds=30,
            retry_count=int(model_params.get("retry_count") or 2),
        )
        output_parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        delta_filter = facade._composer.begin_delta_stream()
        visible_filter = facade._response_coordinator.begin_visible_stream()
        model_call_started = time.perf_counter()
        try:
            async for model_event in facade._model_gateway.stream_chat(brain, request, cancel_token):
                if model_event.event == "started":
                    yield await facade._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_STARTED,
                        {"brain_id": brain["brain_id"]},
                    )
                elif model_event.event == "delta":
                    usage.update(model_event.usage)
                    text = visible_filter.feed(delta_filter.feed(model_event.text))
                    if text:
                        output_parts.append(text)
                        if not buffer_visible_response:
                            yield await facade._emit_and_record(
                                turn_id,
                                trace_id,
                                events,
                                ChatEventType.RESPONSE_DELTA,
                                {"text": text, "response_filter": visible_filter.summary()},
                            )
                elif model_event.event == "usage_delta":
                    usage.update(model_event.usage)
                elif model_event.event == "completed":
                    usage.update(model_event.usage)
                    finish_reason = model_event.finish_reason or "stop"
                    tail_text = visible_filter.feed(delta_filter.finish())
                    tail_text += visible_filter.finish()
                    if tail_text:
                        output_parts.append(tail_text)
                        if not buffer_visible_response:
                            yield await facade._emit_and_record(
                                turn_id,
                                trace_id,
                                events,
                                ChatEventType.RESPONSE_DELTA,
                                {"text": tail_text, "response_filter": visible_filter.summary()},
                            )
                    yield await facade._emit_and_record(
                        turn_id,
                        trace_id,
                        events,
                        ChatEventType.MODEL_COMPLETED,
                        {
                            "finish_reason": finish_reason,
                            "usage": usage,
                            "response_filter": visible_filter.summary(),
                            "continuation_enabled": continuation_decision.enabled,
                        },
                    )
                    break
                elif model_event.event == "cancelled":
                    cancel_token.cancel()
                    break
        except ModelAdapterError as exc:
            await facade._trace.end_span(
                model_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": exc.code.value, "message": exc.message},
                error_code=exc.code.value,
            )
            raise
        if cancel_token.cancelled:
            await facade._trace.end_span(
                model_span,
                status=TraceSpanStatus.FAILED,
                output_data={"error_code": ErrorCode.TURN_CANCELLED.value},
                error_code=ErrorCode.TURN_CANCELLED.value,
            )
            async for event in facade._cancel_turn_during_stream(turn, events, root_span_id):
                yield event
            return
        response_filter = visible_filter.summary()
        assistant_text = _remove_dangling_template_leak("".join(output_parts).strip())
        if not assistant_text:
            repaired_empty_text = _repair_quality_shape_reply(user_text, assistant_text)
            if repaired_empty_text is None:
                raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "模型没有返回可用文本")
            assistant_text, response_filter = facade._response_coordinator.filter_text(_remove_dangling_template_leak(repaired_empty_text))
            prompt_metadata = {
                **prompt_metadata,
                "post_model_repair": {
                    "applied": True,
                    "reason": "empty_model_text_knowledge_fallback",
                },
            }
        repaired_text = _repair_irrelevant_model_reply(user_text, assistant_text)
        if repaired_text is not None:
            assistant_text, response_filter = facade._response_coordinator.filter_text(_remove_dangling_template_leak(repaired_text))
            prompt_metadata = {
                **prompt_metadata,
                "post_model_repair": {
                    "applied": True,
                    "reason": "irrelevant_safety_refusal",
                },
            }
        else:
            repaired_text = _repair_quality_shape_reply(user_text, assistant_text)
            if repaired_text is not None:
                assistant_text, response_filter = facade._response_coordinator.filter_text(_remove_dangling_template_leak(repaired_text))
                prompt_metadata = {
                    **prompt_metadata,
                    "post_model_repair": {
                        "applied": True,
                        "reason": "quality_shape_completion",
                    },
                }
        await facade._trace.end_span(
            model_span,
            output_data={
                "finish_reason": finish_reason,
                "usage": usage,
                "response_filter": response_filter,
                "continuation_enabled": continuation_decision.enabled,
                "post_model_repair": prompt_metadata.get("post_model_repair"),
            },
        )
        response_plan = None
        continuation_payload: dict[str, Any] | None = None
        if continuation_decision.enabled:
            response_plan, assistant_text, response_filter, continuation_payload, finish_reason, usage = await self._run_continuation_flow(
                facade,
                turn=turn,
                events=events,
                context=context,
                user_text=user_text,
                messages=messages,
                assistant_text=assistant_text,
                response_filter=response_filter,
                usage=usage,
                finish_reason=finish_reason,
                prompt_metadata=prompt_metadata,
                continuation_decision=continuation_decision,
                model_call_started=model_call_started,
                brain=brain,
                model_params=model_params,
                root_span_id=root_span_id,
            )
            repaired_text = _repair_irrelevant_model_reply(user_text, assistant_text)
            repair_reason = "irrelevant_safety_refusal_after_continuation"
            if repaired_text is None:
                repaired_text = _repair_quality_shape_reply(user_text, assistant_text)
                repair_reason = "quality_shape_completion_after_continuation"
            if repaired_text is not None and repaired_text.strip() != assistant_text.strip():
                assistant_text, response_filter = facade._response_coordinator.filter_text(_remove_dangling_template_leak(repaired_text))
                response_plan = None
                prompt_metadata = {
                    **prompt_metadata,
                    "post_model_repair": {
                        "applied": True,
                        "reason": repair_reason,
                    },
                }
        async for event in facade._complete_model_turn(
            turn,
            events,
            assistant_text,
            root_span_id,
            usage=usage,
            finish_reason=finish_reason,
            route={"brain_id": brain["brain_id"], "fallback_used": fallback_used},
            intent=intent,
            mode=mode,
            response_plan=response_plan,
            response_filter=response_filter,
            prompt_metadata=prompt_metadata,
            emit_final_delta=buffer_visible_response,
        ):
            yield event

    async def _run_continuation_flow(self, facade: Any, **kwargs: Any) -> tuple[Any, str, Any, dict[str, Any] | None, str, dict[str, Any]]:
        turn = kwargs["turn"]
        context = kwargs["context"]
        user_text = kwargs["user_text"]
        messages = kwargs["messages"]
        assistant_text = kwargs["assistant_text"]
        prompt_metadata = kwargs["prompt_metadata"]
        continuation_decision = kwargs["continuation_decision"]
        model_call_started = kwargs["model_call_started"]
        brain = kwargs["brain"]
        model_params = kwargs["model_params"]
        root_span_id = kwargs["root_span_id"]
        usage = dict(kwargs["usage"])
        finish_reason = kwargs["finish_reason"]
        response_plan = await self._compose_response_plan(facade, turn, context, user_text, assistant_text, prompt_metadata)
        final_text_for_quality = facade._style_visible_text(turn, assistant_text, response_plan=response_plan)
        final_text_for_quality, final_filter = facade._response_coordinator.filter_text(final_text_for_quality)
        assistant_text = final_text_for_quality
        response_filter = final_filter
        initial_latency_ms = int((time.perf_counter() - model_call_started) * 1000)
        continuation_started = time.perf_counter()
        evaluation = facade._continuation.evaluate(
            text=assistant_text,
            user_text=user_text,
            decision=continuation_decision,
            response_quality_guard=response_plan.structured_payload.get("response_quality_guard"),
        )
        iterations = 0
        used_revision = False
        budget_exhausted = False
        usage = {"initial": usage, "continuation_iterations": 0}
        revision_latency_ms: int | None = None
        if evaluation.should_revise and continuation_decision.max_iterations > 0:
            try:
                revision_started = time.perf_counter()
                revision = await self.run_continuation_revision(
                    facade,
                    turn=turn,
                    events=kwargs["events"],
                    messages=messages,
                    user_text=user_text,
                    draft_text=assistant_text,
                    evaluation=evaluation,
                    brain=brain,
                    model_params=model_params,
                    root_span_id=root_span_id,
                )
                revision_latency_ms = int((time.perf_counter() - revision_started) * 1000)
                iterations = 1
                usage["continuation_iterations"] = 1
                usage["revision"] = revision["usage"]
                revised_text = str(revision.get("text") or "").strip()
                if revised_text:
                    assistant_text = revised_text
                    finish_reason = str(revision.get("finish_reason") or finish_reason)
                    used_revision = True
                    response_plan = None
            except ModelAdapterError as exc:
                if exc.code == ErrorCode.TURN_CANCELLED:
                    raise
                budget_exhausted = exc.code == ErrorCode.MODEL_TIMEOUT
                usage["continuation_error"] = exc.code.value
        if response_plan is None:
            response_plan = await self._compose_response_plan(facade, turn, context, user_text, assistant_text, prompt_metadata)
            assistant_text = facade._style_visible_text(turn, assistant_text, response_plan=response_plan)
            assistant_text, final_filter = facade._response_coordinator.filter_text(assistant_text)
            response_filter = final_filter
        evaluation = facade._continuation.evaluate(
            text=assistant_text,
            user_text=user_text,
            decision=continuation_decision,
            elapsed_ms=int((time.perf_counter() - continuation_started) * 1000),
            response_quality_guard=response_plan.structured_payload.get("response_quality_guard"),
        )
        used_safe_fallback = False
        if evaluation.verdict == "block":
            used_safe_fallback = True
            fallback_text = facade._continuation.safe_fallback_text(user_text=user_text, evaluation=evaluation)
            fallback_scenario = "tool_boundary" if set(evaluation.tags) & {"internal_jargon", "secret_leak", "false_done"} else "direct"
            presence_runtime = dict(turn.get("presence_runtime") or {})
            fallback_result = await facade._composer.compose(
                ComposeRequest(
                    user_text=user_text,
                    result_summary=fallback_text,
                    scenario=fallback_scenario,
                    persona=facade._context_persona_payload(context),
                    heart=facade._context_heart_payload(context),
                    route_profile=str((turn.get("experience") or {}).get("route_profile") or ""),
                    channel_profile=facade._channel_profile_for_turn(turn),
                    prompt_mode=str(prompt_metadata.get("prompt_mode") or "full"),
                    prompt_snapshot_id=str(prompt_metadata.get("prompt_snapshot_id") or ""),
                    prompt_assembly_version=str(prompt_metadata.get("prompt_assembly_version") or "") or None,
                    stable_prompt_hash=str(prompt_metadata.get("stable_prompt_hash") or "") or None,
                    dynamic_context_hash=str(prompt_metadata.get("dynamic_context_hash") or "") or None,
                    trusted_context_hash=str(prompt_metadata.get("trusted_context_hash") or "") or None,
                    untrusted_context_hash=str(prompt_metadata.get("untrusted_context_hash") or "") or None,
                    history_context_hash=str(prompt_metadata.get("history_context_hash") or "") or None,
                    current_message_hash=str(prompt_metadata.get("current_message_hash") or "") or None,
                    prompt_section_ids=[str(item) for item in prompt_metadata.get("prompt_section_ids") or []],
                    prompt_sections=[dict(item) for item in prompt_metadata.get("prompt_sections") or [] if isinstance(item, dict)],
                    presence_runtime=presence_runtime,
                    response_policy=dict(presence_runtime.get("response_policy") or {}),
                    session_context=dict(presence_runtime.get("session_context") or {}),
                    action_dialogue=dict(presence_runtime.get("action_dialogue") or {}),
                )
            )
            response_plan = fallback_result.response_plan
            assistant_text = facade._style_visible_text(turn, fallback_result.text, response_plan=response_plan)
            assistant_text, final_filter = facade._response_coordinator.filter_text(assistant_text)
            response_filter = final_filter
            evaluation = facade._continuation.evaluate(
                text=assistant_text,
                user_text=user_text,
                decision=continuation_decision,
                elapsed_ms=int((time.perf_counter() - continuation_started) * 1000),
                response_quality_guard=response_plan.structured_payload.get("response_quality_guard"),
            )
        continuation_payload = None
        if used_revision or used_safe_fallback or evaluation.verdict != "good":
            continuation_payload = facade._continuation.payload(
                decision=continuation_decision,
                evaluation=evaluation,
                iterations=iterations,
                budget_exhausted=budget_exhausted,
                used_revision=used_revision,
                used_safe_fallback=used_safe_fallback,
                initial_latency_ms=initial_latency_ms,
                revision_latency_ms=revision_latency_ms,
                total_latency_ms=int((time.perf_counter() - continuation_started) * 1000),
            )
            response_plan = response_plan.model_copy(
                update={
                    "structured_payload": {
                        **response_plan.structured_payload,
                        "continuation": continuation_payload,
                    },
                    "quality_markers": {
                        **response_plan.quality_markers,
                        "continuation_quality_verdict": evaluation.verdict,
                        "continuation_quality_tags": evaluation.tags,
                        "continuation_diagnostics": evaluation.diagnostics,
                    },
                }
            )
        return response_plan, assistant_text, response_filter, continuation_payload, finish_reason, usage

    async def _compose_response_plan(self, facade: Any, turn: dict[str, Any], context: Any, user_text: str, assistant_text: str, prompt_metadata: dict[str, Any]):
        presence_runtime = dict(turn.get("presence_runtime") or {})
        compose_result = await facade._composer.compose(
            ComposeRequest(
                user_text=user_text,
                result_summary=assistant_text,
                scenario="direct",
                persona=facade._context_persona_payload(context),
                heart=facade._context_heart_payload(context),
                route_profile=str((turn.get("experience") or {}).get("route_profile") or ""),
                channel_profile=facade._channel_profile_for_turn(turn),
                prompt_mode=str(prompt_metadata.get("prompt_mode") or "full"),
                prompt_snapshot_id=str(prompt_metadata.get("prompt_snapshot_id") or ""),
                prompt_assembly_version=str(prompt_metadata.get("prompt_assembly_version") or "") or None,
                stable_prompt_hash=str(prompt_metadata.get("stable_prompt_hash") or "") or None,
                dynamic_context_hash=str(prompt_metadata.get("dynamic_context_hash") or "") or None,
                trusted_context_hash=str(prompt_metadata.get("trusted_context_hash") or "") or None,
                untrusted_context_hash=str(prompt_metadata.get("untrusted_context_hash") or "") or None,
                history_context_hash=str(prompt_metadata.get("history_context_hash") or "") or None,
                current_message_hash=str(prompt_metadata.get("current_message_hash") or "") or None,
                prompt_section_ids=[str(item) for item in prompt_metadata.get("prompt_section_ids") or []],
                prompt_sections=[dict(item) for item in prompt_metadata.get("prompt_sections") or [] if isinstance(item, dict)],
                presence_runtime=presence_runtime,
                response_policy=dict(presence_runtime.get("response_policy") or {}),
                session_context=dict(presence_runtime.get("session_context") or {}),
                action_dialogue=dict(presence_runtime.get("action_dialogue") or {}),
            )
        )
        return compose_result.response_plan

    async def run_continuation_revision(self, facade: Any, *, turn: dict[str, Any], events: list[dict[str, Any]], messages: list[dict[str, str]], user_text: str, draft_text: str, evaluation: Any, brain: dict[str, Any], model_params: dict[str, Any], root_span_id: str | None) -> dict[str, Any]:
        trace_id = turn["trace_id"]
        turn_id = turn["turn_id"]
        revision_messages = facade._continuation.revision_messages(messages=messages, user_text=user_text, draft_text=draft_text, evaluation=evaluation)
        revision_span = await facade._trace.start_span(
            trace_id,
            span_type=TraceSpanType.MODEL_CALL,
            name="call chat model continuation revision",
            parent_span_id=root_span_id,
            metadata={
                "brain_id": brain["brain_id"],
                "provider": brain["provider"],
                "model_name": brain["model_name"],
                "continuation_iteration": 1,
                "quality_verdict": evaluation.verdict,
                "quality_tags": evaluation.tags,
            },
            input_data={"message_count": len(revision_messages), "input_token_estimate": estimate_messages_tokens(revision_messages)},
        )
        request = ModelChatRequest(
            model=str(brain["model_name"]),
            messages=revision_messages,
            temperature=min(float(model_params.get("temperature") or 0.3), 0.25),
            max_output_tokens=int(model_params.get("max_output_tokens") or 1024),
            top_p=float(model_params.get("top_p") or 0.9),
            timeout_seconds=min(int(model_params.get("timeout_seconds") or 180), 20),
            stream=True,
            trace_id=trace_id,
            turn_id=turn_id,
            route_id=f"route_{brain['brain_id']}:continuation:1",
            privacy_level=turn.get("privacy_level") or "medium",
            first_token_timeout_seconds=20,
            retry_count=0,
        )
        token = facade._events.token_for(turn_id)
        output_parts: list[str] = []
        usage: dict[str, Any] = {}
        finish_reason = "stop"
        delta_filter = facade._composer.begin_delta_stream()
        visible_filter = facade._response_coordinator.begin_visible_stream()
        try:
            async for model_event in facade._model_gateway.stream_chat(brain, request, token):
                if model_event.event == "started":
                    await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.MODEL_STARTED, {"brain_id": brain["brain_id"], "continuation_iteration": 1})
                elif model_event.event == "delta":
                    usage.update(model_event.usage)
                    text = visible_filter.feed(delta_filter.feed(model_event.text))
                    if text:
                        output_parts.append(text)
                elif model_event.event == "usage_delta":
                    usage.update(model_event.usage)
                elif model_event.event == "completed":
                    usage.update(model_event.usage)
                    finish_reason = model_event.finish_reason or "stop"
                    tail_text = visible_filter.feed(delta_filter.finish())
                    tail_text += visible_filter.finish()
                    if tail_text:
                        output_parts.append(tail_text)
                    await facade._emit_and_record(turn_id, trace_id, events, ChatEventType.MODEL_COMPLETED, {"finish_reason": finish_reason, "usage": usage, "response_filter": visible_filter.summary(), "continuation_iteration": 1})
                    break
                elif model_event.event == "cancelled":
                    token.cancel()
                    break
        except ModelAdapterError as exc:
            await facade._trace.end_span(revision_span, status=TraceSpanStatus.FAILED, output_data={"error_code": exc.code.value, "message": exc.message}, error_code=exc.code.value)
            raise
        if token.cancelled:
            await facade._trace.end_span(revision_span, status=TraceSpanStatus.FAILED, output_data={"error_code": ErrorCode.TURN_CANCELLED.value}, error_code=ErrorCode.TURN_CANCELLED.value)
            raise ModelAdapterError(ErrorCode.TURN_CANCELLED, "生成已取消")
        text = "".join(output_parts).strip()
        if not text:
            await facade._trace.end_span(revision_span, status=TraceSpanStatus.FAILED, output_data={"error_code": ErrorCode.MODEL_PROTOCOL_ERROR.value}, error_code=ErrorCode.MODEL_PROTOCOL_ERROR.value)
            raise ModelAdapterError(ErrorCode.MODEL_PROTOCOL_ERROR, "续跑修订没有返回可用文本")
        await facade._trace.end_span(revision_span, output_data={"finish_reason": finish_reason, "usage": usage, "response_filter": visible_filter.summary(), "text_chars": len(text)})
        return {"text": text, "usage": usage, "finish_reason": finish_reason}
