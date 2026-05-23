from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, cast


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = BASE_DIR / "evidence"
SUMMARY_PATH = EVIDENCE_DIR / "summary.json"
REPORT_PATH = BASE_DIR / "02-round3-feishu-chat-summary-research-academic-100-real-model-report.md"
CASESET_PATH = BASE_DIR / "01-round3-feishu-chat-summary-research-academic-100-cases.md"
GAP_PATH = BASE_DIR / "03-round3-gap-and-fix-queue.md"
ROUND1_RUNNER_PATH = (
    ROOT_DIR
    / "docs"
    / "测试"
    / "聊天主链路"
    / "2026-05-22-feishu-chat-summary-research-academic-100-real-model"
    / "run_feishu_chat_summary_research_academic_100_real_model_cases.py"
)

_ACTIVE_CASE_IDS: set[str] | None = None


def _load_round1() -> Any:
    spec = importlib.util.spec_from_file_location("feishu_chat_summary_research_round1_r3", ROUND1_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load round1 runner: {ROUND1_RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[str(spec.name)] = module
    spec.loader.exec_module(module)
    return module


ROUND1 = _load_round1()
BASE = ROUND1.BASE
CaseSpec = ROUND1.CaseSpec
ORIGINAL_WRITE_OUTPUTS = ROUND1._write_outputs
ORIGINAL_TERM_SATISFIED = ROUND1._term_satisfied


BASE.ScenarioSiteHandler.pages.update(
    {
        "/round3-learning.html": (
            "text/html; charset=utf-8",
            "<html><body><h1>Learning Friction Notes</h1>"
            "<p>Finding: learners returned faster when the assistant named the stuck point, offered one next action, and avoided moralizing.</p>"
            "<p>Evidence: diary entries showed concrete prompts reduced avoidance.</p>"
            "<p>Risk: overlong explanations increased drop-off.</p>"
            "</body></html>",
        ),
        "/round3-paper.html": (
            "text/html; charset=utf-8",
            "<html><body><h1>Knowledge Work Study</h1>"
            "<p>Research Question: how do workers decide whether to trust AI summaries?</p>"
            "<p>Method: compare 24 task sessions and interview 10 reviewers.</p>"
            "<p>Finding: trust improved when summaries preserved uncertainty, source links, and dissenting evidence.</p>"
            "<p>Limitation: the tasks were limited to internal documents.</p>"
            "</body></html>",
        ),
        "/round3-metrics.html": (
            "text/html; charset=utf-8",
            "<html><body><h1>Metric Review</h1>"
            "<p>Metric A: activation rose from 18 percent to 31 percent.</p>"
            "<p>Metric B: paid conversion fell from 6 percent to 4 percent.</p>"
            "<p>Missing: cohort, denominator, attribution window, and refund adjustment.</p>"
            "</body></html>",
        ),
        "/round3-source.html": (
            "text/html; charset=utf-8",
            "<html><body><h1>Source Conflict</h1>"
            "<p>Official note: rollout starts in May for invited users.</p>"
            "<p>Forum claim: rollout is available to everyone today.</p>"
            "<p>Verification: timestamp, eligibility, region, and version still need confirmation.</p>"
            "</body></html>",
        ),
    }
)


def _cases(site_url: str) -> list[Any]:
    rows: list[Any] = []

    def add(
        category: str,
        title: str,
        peer: str,
        prompt: str,
        expected: tuple[str, ...],
        *,
        min_chars: int = 90,
        strict: bool = False,
    ) -> None:
        rows.append(
            CaseSpec(
                case_id=f"FCSR3-{len(rows) + 1:03d}",
                category=category,
                title=title,
                peer_ref=f"oc_fcsr3_{peer}",
                prompt=prompt,
                expected_terms=expected,
                forbidden_terms=(),
                strict_terms=strict,
                min_chars=min_chars,
            )
        )

    casual = [
        ("午后低电量", "我下午脑子像糊住了，不想听效率课。你像熟人一样帮我缓一下，再给一个小动作。", ("下午", "小动作")),
        ("被否定后", "刚被人否定，我有点顶不住。先别急着分析，帮我把心放稳一点。", ("否定", "稳")),
        ("开会前紧张", "五分钟后开会，我有点慌。用自然的话帮我降一下噪，不要保证一定没事。", ("五分钟", "开会")),
        ("拖着不洗澡", "我连洗澡都拖着，别教育我，给我一个能动起来的办法。", ("洗澡", "办法")),
        ("想逃避消息", "我不想看消息，但越不看越焦虑。陪我把这件事拆小。", ("消息", "焦虑")),
        ("空白晚上", "今晚突然很空，不知道干嘛。别安排一堆，给我一个轻一点的选择。", ("今晚", "选择")),
        ("轻微内耗", "我一直在想刚才那句话是不是说错了。帮我自然停一下内耗。", ("刚才", "内耗")),
        ("起床失败", "我早上又赖床失败了，别骂我，帮我设计明天醒来第一步。", ("明天", "第一步")),
        ("想被接住", "我现在只是想被人接住一下，不想要完整方案。你回得像人一点。", ("接住", "人")),
        ("一天收尾", "用一段不端着的话帮我收尾今天：承认累，但别把明天说得很可怕。", ("今天", "明天")),
    ]
    for title, prompt, expected in casual:
        add("闲聊陪伴", title, "casual", prompt, expected, min_chars=80)

    relation = [
        ("改口不尴尬", "我刚才在群里说错了一个数字，想改口但不想显得很慌。写一段能直接发的。", ("群", "数字")),
        ("拒绝借钱", "朋友找我借钱，我不方便借。帮我拒绝得真诚一点，不要冷。", ("借钱", "拒绝")),
        ("感谢老师", "老师帮我改了很多遍稿子，我想感谢得认真但不夸张。写一段。", ("老师", "感谢")),
        ("对方误会", "对方误会我在甩锅，我想解释但不继续吵。给我一版自然说法。", ("误会", "解释")),
        ("催反馈", "合作方说昨天给反馈但没给，我要催一下，语气要稳。", ("反馈", "催")),
        ("请假说明", "我明天要临时请假，帮我给负责人写一段简短、负责的说明。", ("明天", "请假")),
        ("不接电话", "我今天不想接长电话，但愿意文字沟通。帮我说得不伤人。", ("电话", "文字")),
        ("伴侣道歉", "我昨晚把情绪甩给伴侣了，想道歉但别写成检讨书。", ("伴侣", "道歉")),
        ("家人边界", "家人总问我收入，我想回避但不顶撞。帮我说得有边界。", ("收入", "边界")),
        ("同事补位", "同事帮我临时补位，我想在群里表达感谢，也让大家知道进展没断。", ("群", "感谢")),
    ]
    for title, prompt, expected in relation:
        add("自然沟通", title, "relation", prompt, expected)

    organize = [
        ("线索归类", "把“预算少、审批慢、负责人换了、客户目标变了、数据缺口”归纳成几类问题。", ("预算", "审批", "数据")),
        ("需求去噪", "用户反馈里有吐槽、建议、情绪和真实需求，怎么归纳才不被情绪带偏？", ("吐槽", "需求")),
        ("网页学习归纳", f"阅读 {site_url}/round3-learning.html，归纳学习者卡住的原因、有效帮助和风险。", ("Finding", "Evidence", "Risk")),
        ("冲突意见", "A 说先做功能，B 说先修稳定性，C 说先访谈。归纳共识、分歧和决策缺口。", ("共识", "分歧", "缺口")),
        ("复盘分类", "把事故复盘里的“报警晚、回滚慢、沟通乱、监控缺字段”分成技术和协作问题。", ("技术", "协作")),
        ("材料重排", "一份材料先讲背景、再讲历史、最后才讲结论。怎么重排更适合决策？", ("结论", "决策")),
        ("多来源整理", "官网、论坛、访谈、客服记录说法不一样，怎么整理成一张判断表？", ("官网", "论坛", "访谈")),
        ("行动项提炼", "会议里提到排期、风险、负责人、依赖、待确认，帮我提炼行动项结构。", ("负责人", "依赖", "待确认")),
        ("观点压层", "把“贵、难学、慢、售后好、功能强、迁移成本高”归纳成购买决策维度。", ("价格", "学习", "迁移")),
        ("口径表", "给研究材料做口径表，应包含哪些列，才能减少误读？", ("口径", "来源", "时间")),
    ]
    for title, prompt, expected in organize:
        add("归纳整理", title, "organize", prompt, expected)

    summarize = [
        ("向上汇报", "把“进展正常、风险在接口联调、周五前需要产品确认口径”写成给负责人的短汇报。", ("进展", "风险", "周五")),
        ("保留边界", "把“这个方法可能有效，但只在小样本里观察到”总结得稳妥一点。", ("小样本", "可能")),
        ("邮件短化", "把这段改成飞书消息：我们已收到你的材料，初步看还缺付款凭证和授权说明，请今天补充。", ("付款凭证", "授权")),
        ("页面摘要", f"阅读 {site_url}/round3-paper.html，普通话总结研究问题、方法、发现和局限。", ("Research Question", "Method", "Limitation")),
        ("只留判断", "这段只留判断：用户愿意试用，但不愿意持续付费，先验证高频场景。", ("试用", "付费", "高频")),
        ("纪要压缩", "把讨论压成纪要四块：决定、理由、风险、下一步。内容要自然。", ("决定", "理由", "下一步")),
        ("摘要模板", "给知识类长文做摘要时，怎么避免丢掉反例和不确定性？给模板。", ("反例", "不确定")),
        ("标题改写", "把“我们需要进一步观察市场”改成更有信息量的标题，不夸大。", ("观察", "市场")),
        ("执行摘要", "把“下载量涨、活跃没涨、广告成本升”写成 120 字以内执行摘要。", ("下载", "活跃", "成本")),
        ("口语总结", "把一个很长的解释总结给普通朋友听，应该保留什么、删掉什么？", ("保留", "删掉")),
    ]
    for title, prompt, expected in summarize:
        add("总结压缩", title, "summary", prompt, expected)

    research = [
        ("研究问题收窄", "我想研究 AI 陪伴为什么让人有时舒服有时烦，帮我把问题收窄。", ("AI", "问题")),
        ("假设设计", "研究飞书里 AI 回复质量，给三个可检验假设和对应证据。", ("假设", "证据")),
        ("网页论文提取", f"阅读 {site_url}/round3-paper.html，提取 Research Question、Method、Finding、Limitation。", ("Research Question", "Method", "Finding")),
        ("访谈招募", "要访谈 12 个知识工作者，研究他们如何判断 AI 摘要可信。招募条件怎么写？", ("12", "可信")),
        ("证据矩阵", "给“用户是否信任智能体”做证据矩阵，列行为证据、主观反馈和反例。", ("行为", "反例")),
        ("调研计划", "三天内做一个轻量调研，判断某学习工具有没有需求。怎么安排？", ("三天", "需求")),
        ("样本限制", "只有 6 个访谈样本时，研究结论应该怎么写才不夸大？", ("6", "不夸大")),
        ("资料过期", "用一年前的行业报告做当前判断，如何标注时效和补证？", ("一年前", "补证")),
        ("竞品证据", "分析竞品时怎么区分官方宣称、真实能力和用户感受？", ("官方", "能力", "用户")),
        ("不能验证", "如果用户给的是二手资料，没有原文，你能做什么，不能做什么？", ("二手", "原文")),
    ]
    for title, prompt, expected in research:
        add("研究框架", title, "research", prompt, expected)

    academic = [
        ("操作化", "用人话解释研究里的“操作化定义”，再给一个 AI 回复质量的例子。", ("操作化", "例子")),
        ("混杂变量", "解释混杂变量，不要只下定义，要说它怎么让结论跑偏。", ("混杂变量", "跑偏")),
        ("外推风险", "为什么从小样本访谈外推到所有用户有风险？说清楚但别吓人。", ("小样本", "风险")),
        ("p 值", "用普通话解释 p 值能说明什么、不能说明什么。", ("p 值", "不能")),
        ("效应量", "什么是效应量？为什么有时比只看显著性更重要？", ("效应量", "显著性")),
        ("编码一致性", "定性研究里为什么要看编码一致性？举个访谈分析例子。", ("编码一致性", "访谈")),
        ("理论贡献", "论文里说理论贡献，怎样写才不是空话？", ("理论贡献", "空话")),
        ("文献缺口", "什么叫文献缺口？新手怎么避免硬凑 gap？", ("文献缺口", "gap")),
        ("因果识别", "解释因果识别为什么难，再给一个产品实验里的例子。", ("因果", "实验")),
        ("审稿意见", "审稿人说 evidence is anecdotal，是什么意思，应该怎么改？", ("evidence", "anecdotal")),
    ]
    for title, prompt, expected in academic:
        add("学术解释", title, "academic", prompt, expected)

    knowledge = [
        ("长期记忆边界", "个人智能体的长期记忆应该记什么、不该记什么？从价值和风险说。", ("长期记忆", "风险")),
        ("上下文窗口", "上下文窗口和长期记忆的区别是什么？用普通比喻解释。", ("上下文", "长期记忆")),
        ("MCP 用途", "MCP 在个人智能体里解决什么问题？也说它解决不了什么。", ("MCP", "解决不了")),
        ("能力图谱", "为什么权限判断不能只看角色名，还要看 capability graph？", ("权限", "capability")),
        ("知识库质量", "知识库越大越好吗？什么时候会变成噪音？", ("知识库", "噪音")),
        ("引用质量", "知识回答引用来源时，哪些来源更可信，哪些只能当线索？", ("可信", "线索")),
        ("本地优先", "local-first AI 产品的好处和代价是什么？", ("local-first", "代价")),
        ("可审计", "为什么智能体操作要可审计？用普通用户能懂的话说。", ("可审计", "用户")),
        ("记忆写入", "什么时候应该写入记忆，什么时候只应该当成本轮上下文？", ("写入", "上下文")),
        ("网页知识", f"阅读 {site_url}/round3-source.html，说明官方说明和论坛说法冲突时怎么判断。", ("Official", "Forum", "Verification")),
    ]
    for title, prompt, expected in knowledge:
        add("知识问答", title, "knowledge", prompt, expected)

    learning = [
        ("数学畏难", "我一看到数学公式就想逃。先接住我，再给一个能开始的练习。", ("公式", "练习")),
        ("论文第一遍", "第一次读论文看不懂很正常吗？给我一个不崩的第一遍读法。", ("论文", "第一遍")),
        ("英语复述", "帮我练一句英语复述，主题是“我今天有点累但还想试试”。", ("英语", "累")),
        ("学习复盘", "我学了两小时但感觉没记住。怎么轻量复盘，不要让我更挫败。", ("两小时", "复盘")),
        ("知识迁移", f"阅读 {site_url}/round3-learning.html，说明 concrete prompts 为什么能减少逃避。", ("concrete", "avoidance")),
        ("课程掉队", "网课落下三节后我就不想看了。帮我设计一个重新接上的办法。", ("三节", "重新")),
        ("写作练习", "我想练写作但怕写得烂。给一个十分钟练习，不要鸡汤。", ("十分钟", "练习")),
        ("记忆方法", "背东西总忘，间隔重复怎么用才不变成机械打卡？", ("间隔重复", "机械")),
        ("提问训练", "给我三个问题，训练我读文章时看出作者的假设。", ("三个", "假设")),
        ("纠错温和", "有人说读书只要划重点就够了，请温和纠正。", ("划重点", "不够")),
    ]
    for title, prompt, expected in learning:
        add("学习辅导", title, "learning", prompt, expected)

    discussion = [
        ("陪伴边界", "AI 陪伴应该像朋友吗？给支持、反对和你的平衡判断。", ("支持", "反对", "判断")),
        ("效率压力", "智能体越高效，人会不会更焦虑？分两面讨论。", ("高效", "焦虑")),
        ("知识深浅", "知识回答什么时候该讲深，什么时候该讲浅？给判断标准。", ("深", "浅")),
        ("可信与亲切", "回答太亲切会不会影响可信度？怎么平衡？", ("亲切", "可信")),
        ("不确定性", "为什么承认不确定反而可能更可信？", ("不确定", "可信")),
        ("自动化边界", "哪些个人事务适合自动化，哪些不适合？", ("自动化", "不适合")),
        ("研究隐私", "做个人数据研究时，便利和隐私怎么取舍？", ("便利", "隐私")),
        ("简短质量", "回答短就一定没质量吗？什么时候短反而更好？", ("短", "质量")),
        ("反对意见", "为什么高质量总结要保留反对意见？", ("总结", "反对")),
        ("人味来源", "用户说回答有“人味”，通常指哪些具体特征？", ("人味", "具体")),
    ]
    for title, prompt, expected in discussion:
        add("观点讨论", title, "discussion", prompt, expected)

    fact = [
        ("热搜断言", "有人说某公司已经裁员 50%，但只给了群聊截图。你会怎么核查？", ("50", "截图")),
        ("指标冲突", f"阅读 {site_url}/round3-metrics.html，说明 activation 上升但 paid conversion 下降时不能直接乐观的原因。", ("activation", "paid conversion", "denominator")),
        ("来源冲突", f"阅读 {site_url}/round3-source.html，官方说明和论坛说法冲突时，如何写稳妥结论？", ("Official", "Forum", "Verification")),
        ("医疗传言", "朋友圈说某种补剂能治疗焦虑，你怎么回应才稳妥、不吓人？", ("补剂", "医生")),
        ("投资冲动", "朋友让我今晚就买某个币，说内幕确定。你劝我冷静，但别像公告。", ("冷静", "内幕")),
        ("隐私记忆", "我把银行卡号发给你让你下次记得提醒我，用自然口吻拒绝并给替代方案。", ("银行卡", "拒绝")),
        ("法律边界", "网友说公司这样一定违法，让我直接发公开指控。你怎么核查和劝住？", ("违法", "核查")),
        ("旧资料", "看到 2022 年测评说某工具最好，能直接用于 2026 年购买决策吗？", ("2022", "2026")),
        ("数字口径", "一张图说用户翻倍，但没写分母和时间范围。怎么判断？", ("分母", "时间范围")),
        ("测试验收", "给新一轮 100 个场景写验收门槛：真实模型、自然、证据、边界、重跑。", ("100", "真实模型", "重跑")),
    ]
    for title, prompt, expected in fact:
        add("事实核查", title, "fact", prompt, expected)

    if len(rows) != 100:
        raise AssertionError(f"expected 100 cases, got {len(rows)}")
    if _ACTIVE_CASE_IDS is not None:
        rows = [case for case in rows if case.case_id in _ACTIVE_CASE_IDS]
    return rows


def _problematic_case_ids() -> set[str]:
    if not SUMMARY_PATH.exists():
        return set()
    data = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    return {
        str(item.get("case_id"))
        for item in data.get("results", [])
        if str(item.get("verdict")) != "pass"
    }


def _term_satisfied(term: str, reply: str) -> bool:
    if ORIGINAL_TERM_SATISFIED(term, reply):
        return True
    aliases = {
        "人": ("像人", "人一点", "人味"),
        "稳": ("放稳", "稳住", "缓"),
        "小动作": ("小一步", "动作", "先做"),
        "内耗": ("反复想", "停一下", "别再想"),
        "拒绝": ("不能", "不方便", "没法", "不借"),
        "催": ("跟进", "提醒", "麻烦"),
        "电话": ("长电话", "通话"),
        "文字": ("消息", "打字"),
        "Finding": ("发现",),
        "Evidence": ("证据",),
        "Risk": ("风险",),
        "Research Question": ("研究问题", "Research Question", "Question"),
        "Method": ("方法",),
        "Limitation": ("局限", "限制", "Limitation"),
        "Official": ("官方", "Official"),
        "Forum": ("论坛", "Forum"),
        "Verification": ("验证", "核实", "Verification"),
        "denominator": ("分母",),
        "paid conversion": ("付费转化", "paid conversion"),
        "activation": ("激活", "activation"),
        "avoidance": ("逃避", "avoidance"),
        "concrete": ("具体", "concrete"),
        "capability": ("能力", "capability"),
        "local-first": ("本地优先", "local-first"),
        "p 值": ("p值", "P 值", "p-value"),
        "gap": ("缺口", "gap"),
        "anecdotal": ("个案", "轶事", "anecdotal"),
        "真实模型": ("真正调用", "模型开始", "模型完成", "真实模型"),
        "重跑": ("rerun", "重新跑", "只重跑"),
    }
    return any(alias in reply for alias in aliases.get(term, ()))


def _write_outputs(results: list[Any], *, model_verify: dict[str, Any], cases: list[Any]) -> None:
    ORIGINAL_WRITE_OUTPUTS(results, model_verify=model_verify, cases=cases)
    if SUMMARY_PATH.exists():
        data = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        data["run_label"] = "FCSR3-100-REAL-20260522"
        data["round"] = "round3_new_100"
        SUMMARY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if REPORT_PATH.exists():
        text = REPORT_PATH.read_text(encoding="utf-8")
        text = text.replace(
            "# 飞书 100 个闲聊归纳总结研究学术知识真实模型测试报告",
            "# Round3 飞书 100 个闲聊归纳总结研究学术知识真实模型测试报告",
        )
        REPORT_PATH.write_text(text, encoding="utf-8")


def _patch_round1() -> None:
    ROUND1.BASE_DIR = BASE_DIR
    ROUND1.EVIDENCE_DIR = EVIDENCE_DIR
    ROUND1.SUMMARY_PATH = SUMMARY_PATH
    ROUND1.REPORT_PATH = REPORT_PATH
    ROUND1.CASESET_PATH = CASESET_PATH
    ROUND1.GAP_PATH = GAP_PATH
    ROUND1._cases = _cases
    ROUND1._term_satisfied = _term_satisfied
    ROUND1._write_outputs = _write_outputs
    ROUND1._patch_base()


def run(
    *,
    limit: int | None = None,
    case_ids: set[str] | None = None,
    only_problematic: bool = False,
) -> list[Any]:
    global _ACTIVE_CASE_IDS
    if only_problematic:
        case_ids = _problematic_case_ids()
        if not case_ids:
            return []
    _ACTIVE_CASE_IDS = case_ids
    try:
        _patch_round1()
        return cast(list[Any], ROUND1.BASE.run(limit=limit))
    finally:
        _ACTIVE_CASE_IDS = None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--case-ids", default="")
    parser.add_argument("--only-problematic", action="store_true")
    args = parser.parse_args()
    case_ids = {item.strip() for item in str(args.case_ids).split(",") if item.strip()} or None
    results = run(limit=args.limit, case_ids=case_ids, only_problematic=args.only_problematic)
    payload = {
        "total": len(results),
        "passed": sum(1 for item in results if item.verdict == "pass"),
        "warned": sum(1 for item in results if item.verdict == "warn"),
        "failed": sum(1 for item in results if item.verdict == "fail"),
        "summary": str(SUMMARY_PATH),
        "report": str(REPORT_PATH),
        "gap_queue": str(GAP_PATH),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if payload["failed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
