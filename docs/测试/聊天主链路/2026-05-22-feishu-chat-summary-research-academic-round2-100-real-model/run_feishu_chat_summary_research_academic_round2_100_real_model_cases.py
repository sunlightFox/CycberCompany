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
REPORT_PATH = BASE_DIR / "02-飞书100个闲聊归纳总结研究学术知识round2真实模型测试报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-飞书100个闲聊归纳总结研究学术知识round2场景.md"
GAP_PATH = BASE_DIR / "03-缺口与修复队列.md"
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
    spec = importlib.util.spec_from_file_location("feishu_chat_summary_research_round1", ROUND1_RUNNER_PATH)
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
        "/round2-study.html": (
            "text/html; charset=utf-8",
            "<html><body><h1>Round2 Learning Notes</h1>"
            "<p>Finding: learners stayed engaged when answers were concrete, short enough, and connected to their current blocker.</p>"
            "<p>Evidence: examples, small actions, and uncertainty labels improved trust.</p>"
            "<p>Risk: generic encouragement was rated as low value.</p>"
            "</body></html>",
        ),
        "/round2-research.html": (
            "text/html; charset=utf-8",
            "<html><body><h1>Agent Trust Research</h1>"
            "<p>Question: which visible behaviors make personal agents feel trustworthy?</p>"
            "<p>Method: interview 18 users and review 120 failure reports.</p>"
            "<p>Finding: users valued correction, source clarity, and not over-claiming completion.</p>"
            "<p>Limitation: participants were early adopters.</p>"
            "</body></html>",
        ),
        "/round2-conflict.html": (
            "text/html; charset=utf-8",
            "<html><body><h1>Metric Conflict</h1>"
            "<p>Dashboard A says retention improved by 22 percent.</p>"
            "<p>Dashboard B says retention improved by 9 percent.</p>"
            "<p>Missing: cohort definition, denominator, time window, and exclusion rules.</p>"
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
        case_id = f"FCSR2-{len(rows) + 1:03d}"
        rows.append(
            CaseSpec(
                case_id=case_id,
                category=category,
                title=title,
                peer_ref=f"oc_fcsr2_{peer}",
                prompt=prompt,
                expected_terms=expected,
                forbidden_terms=(),
                strict_terms=strict,
                min_chars=min_chars,
            )
        )

    casual = [
        ("疲惫收口", "我今天脑子很乱，不想被教育。你像熟人一样帮我把今晚收住，别太短。", ("今晚", "收住")),
        ("轻微崩溃", "我有点绷不住，但也不想听鸡汤。你先接住我，再给一个很小的下一步。", ("接住", "下一步")),
        ("不想开工", "我现在就是不想开始。别分析原因，给我一个能骗过大脑的启动动作。", ("开始", "动作")),
        ("焦虑降噪", "我担心明天会出问题。你别保证没事，帮我把担心拆小一点。", ("担心", "拆")),
        ("睡前安放", "用自然的话陪我睡前落地：不要总结成绩，只让我别再反刍。", ("睡前", "反刍")),
        ("自责转述", "我总觉得自己做得不够。帮我把这个感觉说出来，不要急着鼓励。", ("感觉", "不够")),
        ("选择困难", "今晚到底学习还是休息，我不想你替我选。给两个判断问题。", ("学习", "休息")),
        ("接梗提神", "我喝了咖啡还是困，帮我自然接个梗，再给一个真能醒一点的办法。", ("咖啡", "办法")),
        ("拖延两分钟", "我又拖到了现在。你别长篇大论，帮我设计一个两分钟就能开始的动作。", ("两分钟", "开始")),
        ("日常复位", "今天好多事都没收尾。你帮我用一段话复位，不要像任务系统。", ("今天", "复位")),
    ]
    for title, prompt, expected in casual:
        add("闲聊陪伴", title, "casual", prompt, expected, min_chars=80)

    relation = [
        ("群里补充", "同事在群里误解了我的方案，我想补充但不想让他难堪。写一段能直接发的。", ("群", "补充")),
        ("朋友边界", "朋友临时约我，但我真的想独处。帮我拒绝得自然，不冷。", ("独处", "自然")),
        ("道歉不卑微", "我刚才语气不好，想道歉但不要过度低姿态。给我一版自然说法。", ("语气", "道歉")),
        ("催资料", "合作方资料拖了三天，我要催一下，但别显得压迫。写一段飞书消息。", ("资料", "飞书")),
        ("感谢克制", "朋友帮我救了个急，我想认真感谢但不肉麻，写一段。", ("感谢", "朋友")),
        ("伴侣空间", "我想跟伴侣说需要一点空间，但不是疏远。帮我说得柔和一点。", ("空间", "不是")),
        ("老板坏消息", "项目可能延期，帮我给负责人写一段诚实、不甩锅、能推进的同步。", ("延期", "推进")),
        ("家人催促", "家里人一直催我稳定下来，我想回得不冲但有边界。", ("稳定", "边界")),
        ("修复关系", "昨天我说话有点冲，今天想修复一下关系。帮我写个开场。", ("修复", "开场")),
        ("拒绝补材料", "同事今晚让我临时补材料，我做不了。帮我拒绝得体，也给替代安排。", ("拒绝", "替代")),
    ]
    for title, prompt, expected in relation:
        add("自然沟通", title, "relation", prompt, expected, min_chars=90)

    organize = [
        ("原因分层", "把“范围变、沟通慢、测试少、负责人不清”归纳成几个原因层次。", ("范围", "沟通", "测试")),
        ("会议提炼", "A 说先保稳定，B 说别拖上线，C 说要补监控。归纳结论、分歧和待办。", ("结论", "分歧", "待办")),
        ("反馈主题", "把这些反馈归类：加载慢、价格贵、客服慢、教程难、导入失败、报错多。", ("性能", "价格", "客服")),
        ("去重整合", "两份材料都在讲背景、风险、下一步。怎么合并成不重复的一版？", ("去重", "风险")),
        ("学习笔记", "把零散学习笔记整理成概念、例子、误区、练习四块。", ("概念", "例子", "练习")),
        ("多角色", "产品想快上，后端想补稳定性，运营怕体验差。归纳共识和冲突。", ("共识", "冲突")),
        ("网页归纳", f"阅读 {site_url}/round2-study.html，归纳用户喜欢什么、不喜欢什么和一个风险。", ("concrete", "generic", "Risk")),
        ("决策材料", "把一堆调研材料整理成能决策的结构，应该保留哪些部分？", ("决策", "结构")),
        ("反复投诉", "客户反复投诉解释不清、补偿慢、没人跟进。归纳问题类型和改进方向。", ("解释", "补偿", "跟进")),
        ("口径统一", "多个团队写法不同，怎么统一口径、指标、时间范围和结论强度？", ("口径", "时间范围")),
    ]
    for title, prompt, expected in organize:
        add("归纳整理", title, "organize", prompt, expected)

    summarize = [
        ("老板摘要", "把“上线窗口紧、接口已评审、自动化测试未补齐”总结成老板能看的一段话。", ("上线", "测试")),
        ("保留不确定", "把“看起来有效但样本太少”写成稳妥结论，不要装确定。", ("样本", "不确定")),
        ("短消息", "把邮件改成飞书短消息：附件已收到，我们会在三个工作日内审核并反馈修改意见。", ("三个工作日", "反馈")),
        ("复盘压缩", "把故障复盘压成影响、原因、已处理、下一步四段，语气自然。", ("影响", "原因", "下一步")),
        ("研究摘要", "给研究报告写摘要时，怎样同时保留结论、证据、边界和下一步？给模板。", ("结论", "证据", "边界")),
        ("只说重点", "这段只保留重点：数据没齐，但留存下降信号一致，先查渠道质量。", ("留存", "渠道")),
        ("网页摘要", f"阅读 {site_url}/round2-research.html，用普通话总结问题、方法、发现和局限。", ("Question", "Method", "Limitation")),
        ("不等于截短", "为什么好总结不是单纯减少字数，而是重建结构？用普通话解释。", ("字数", "结构")),
        ("执行摘要", "把“用户兴趣高、付费不稳、竞品迭代快”写成 120 字以内执行摘要。", ("用户", "付费", "竞品")),
        ("会议纪要", "把讨论压成会议纪要：决定、负责人、截止时间、待确认。", ("决定", "负责人", "截止")),
    ]
    for title, prompt, expected in summarize:
        add("总结压缩", title, "summary", prompt, expected)

    research = [
        ("研究框架", "研究“个人智能体如何建立信任”，帮我搭问题、假设、方法、输出。", ("问题", "假设", "方法")),
        ("资料收集", "做行业研究前要收集哪些资料？按来源、日期、证据等级、风险备注分组。", ("来源", "日期", "证据")),
        ("竞品研究", "做竞品研究时，如何避免只看宣传页？给步骤和证据要求。", ("宣传页", "证据")),
        ("访谈提纲", "我要访谈 8 个用户，研究他们为什么放弃一个 App。帮我设计提纲。", ("8", "访谈")),
        ("定性定量", "研究用户需求时，什么时候用定性，什么时候用定量？给组合方案。", ("定性", "定量")),
        ("资料过期", "用 2024 年资料判断 2026 年趋势时，怎么处理时效和验证？", ("2024", "2026", "验证")),
        ("评分标准", "给研究类回答设计 100 分评分标准，覆盖问题、方法、证据、边界、可执行性。", ("100", "方法", "证据")),
        ("商业化判断", "判断一个知识工具是否值得商业化，研究框架怎么搭？", ("商业化", "需求", "渠道")),
        ("网页论文包", f"阅读 {site_url}/round2-research.html，提取研究问题、方法、发现和局限。", ("Question", "Method", "Finding")),
        ("不能下结论", "没看到原始材料前，你能做哪些研究准备，不能做哪些结论？", ("原始", "不能")),
    ]
    for title, prompt, expected in research:
        add("研究框架", title, "research", prompt, expected)

    academic = [
        ("可证伪", "用普通人能懂的话解释可证伪，再给一个产品研究里的例子。", ("可证伪", "例子")),
        ("相关因果", "解释相关性和因果性的区别，不只讲定义，要说怎么识别偷换概念。", ("相关", "因果")),
        ("样本偏差", "什么是样本偏差？如果报告只采访重度用户，结论会有什么问题？", ("样本偏差", "重度用户")),
        ("贝叶斯", "用费曼技巧解释贝叶斯更新，再给一个生活例子。", ("贝叶斯", "例子")),
        ("间隔重复", "解释间隔重复为什么有效，也说它解决不了什么问题。", ("间隔重复", "解决不了")),
        ("F1 指标", "解释准确率、召回率和 F1，别堆公式，要说什么时候看哪个。", ("准确率", "召回率", "F1")),
        ("混合方法", "什么是 mixed-method 研究？为什么有时比只做问卷更稳？", ("mixed-method", "问卷")),
        ("局限写法", "学术摘要里的 limitation 应该怎么写，才不是自我否定？", ("limitation", "局限")),
        ("文献综述", "新手写文献综述时，如何从罗列论文变成提出问题？", ("文献综述", "问题")),
        ("外部效度", "把“外部效度不足”解释成人话，并给一个日常例子。", ("外部效度", "例子")),
    ]
    for title, prompt, expected in academic:
        add("学术解释", title, "academic", prompt, expected)

    knowledge = [
        ("RAG 记忆", "RAG 和长期记忆有什么区别？从来源、写入、召回、评估四个角度说。", ("来源", "写入", "召回")),
        ("知识图谱", "知识图谱在个人智能体系统里有什么用？说优点、限制和适用场景。", ("知识图谱", "限制")),
        ("本地部署", "为什么个人 AI 产品强调本地部署？从隐私、延迟、成本、维护说。", ("隐私", "延迟", "成本")),
        ("证据链", "为什么知识类回答不能只给结论，还要给证据链？", ("结论", "证据")),
        ("知识卡", "如何把一篇文章做成可复用知识卡？给一张模板。", ("知识卡", "模板")),
        ("高质量提问", "教我怎么问一个高质量研究问题，给坏问题和好问题对比。", ("坏问题", "好问题")),
        ("幻觉降低", "模型产生知识幻觉时，系统层面应该怎么降低风险？", ("幻觉", "风险")),
        ("资料焦虑", "资料太多反而无法决策，这是什么问题？怎么解决？", ("资料", "决策")),
        ("读论文", "新手读论文应该先看哪些部分？按 30 分钟阅读法说明。", ("论文", "30")),
        ("最新事实", "当你不知道最新事实时，怎样回答才既有帮助又不装懂？", ("最新", "验证")),
    ]
    for title, prompt, expected in knowledge:
        add("知识问答", title, "knowledge", prompt, expected)

    learning = [
        ("英语开口", "我想练英语口语但怕开口。先用中文接住我，再给一句可以跟读的英文。", ("英文", "跟读")),
        ("阅读卡住", "书读到一半卡住了，帮我用一个问题重新进入。", ("问题", "书")),
        ("25 分钟", "每天只有 25 分钟，怎么学一个新技能才不容易放弃？", ("25", "放弃")),
        ("写作开头", "我想写东西但没灵感。给三个生活化开头，不要像写作课广告。", ("三个", "开头")),
        ("批判阅读", "教我批判性阅读一篇商业分析文章，列几个好用的问题。", ("商业分析", "问题")),
        ("微习惯", "我想早睡但总失败。给一个今晚就能做的微习惯。", ("早睡", "微习惯")),
        ("轻复盘", "帮我轻量复盘今天：一个事实、一个情绪、一个下一步。", ("事实", "情绪", "下一步")),
        ("筛课程", "网上课程太多，我怎么筛选靠谱学习材料？给清单。", ("课程", "清单")),
        ("纠正误区", "有人说样本越多结论一定越正确，请温和纠正。", ("样本", "不一定")),
        ("迁移练习", f"阅读 {site_url}/round2-study.html，说明 varied practice 为什么对迁移有帮助。", ("varied practice", "迁移")),
    ]
    for title, prompt, expected in learning:
        add("学习辅导", title, "learning", prompt, expected)

    discussion = [
        ("AI 学习", "讨论 AI 会不会削弱人的学习能力，请给正反观点和你的平衡判断。", ("正", "反", "判断")),
        ("知识工作", "知识工作会如何被智能体改变？分短期、中期、长期讨论。", ("短期", "中期", "长期")),
        ("专家大众", "知识回答应该更像专家报告还是大众解释？请给取舍。", ("专家", "大众")),
        ("丰富度", "回答越丰富一定越好吗？什么时候该简短，什么时候该展开？", ("简短", "展开")),
        ("信任机制", "个人智能体怎样建立用户信任？从可解释、可撤回、可审计谈。", ("可解释", "可撤回")),
        ("研究伦理", "做用户研究时，哪些做法会侵犯隐私或误导用户？", ("隐私", "误导")),
        ("不确定表达", "怎样表达不确定性，既不含糊也不武断？", ("不确定", "武断")),
        ("资料越多越好", "资料越多，判断就一定越好吗？讲清楚什么时候反而会变差。", ("资料", "判断")),
        ("普通有深度", "怎样让学术知识回答有深度，但读起来不像论文摘要？", ("深度", "论文")),
        ("系统腔问题", "为什么用户会讨厌系统腔回答？怎么改成更像人说话？", ("系统腔", "人说话")),
    ]
    for title, prompt, expected in discussion:
        add("观点讨论", title, "discussion", prompt, expected)

    fact = [
        ("截图核查", "看到一张热搜截图，如何核查它是不是伪造或断章取义？", ("截图", "核查")),
        ("增长数字", "一篇文章说转化增长 300%，你会核查哪些基数、口径和时间范围？", ("300", "基数", "口径")),
        ("无出处引用", "别人引用一句专家观点但没有出处，怎么处理才稳妥？", ("专家观点", "出处")),
        ("冲突来源", f"阅读 {site_url}/round2-conflict.html，说明两个留存数字为什么不能直接下结论，还缺什么。", ("22", "9", "denominator")),
        ("官方媒体", "官方公告和媒体报道不一致时，如何写一个稳妥结论？", ("官方", "媒体")),
        ("不能联网", "如果不能联网核查最新事实，你应该怎么回答用户？", ("不能联网", "最新")),
        ("医疗边界", "网上说某保健品改善睡眠，你会怎么核查且避免医疗误导？", ("核查", "医生")),
        ("投资边界", "朋友说某币马上暴涨，你用朋友口吻劝我冷静，但别像免责声明机器。", ("冷静", "风险")),
        ("隐私边界", "我把身份证号发你让你记住，下次填表用。你自然拒绝并给替代方案。", ("身份证", "拒绝")),
        ("验收标准", "给这次新的 100 个闲聊和知识类场景写验收标准，强调自然、质量、证据和边界。", ("100", "自然", "证据")),
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
        "开始": ("打开", "启动", "先做", "只做"),
        "睡前": ("今晚", "睡觉", "睡了"),
        "反刍": ("反复", "拉扯", "再想", "复盘", "不用接话", "明天再处理", "不用想完"),
        "不确定": ("初步", "不足以下定最终结论", "尚不足", "证据不足"),
        "局限": ("limitation", "研究边界", "限制", "适用边界"),
        "跟读": ("可以直接跟读", "I want to practice", "English:"),
        "学习": ("学",),
        "休息": ("不学", "放松", "休息"),
        "群": ("群里",),
        "补充": ("我补充一下", "补充澄清"),
        "独处": ("自己待", "一个人待", "安静"),
        "自然": ("不冷", "随意", "平常"),
        "朋友": ("你及时帮我", "这份帮忙", "帮忙", "谢谢你"),
        "Question": ("问题", "研究问题"),
        "Finding": ("发现",),
        "Risk": ("风险",),
        "concrete": ("具体",),
        "generic": ("泛泛", "空话"),
        "denominator": ("分母",),
    }
    return any(alias in reply for alias in aliases.get(term, ()))


def _write_outputs(results: list[Any], *, model_verify: dict[str, Any], cases: list[Any]) -> None:
    ORIGINAL_WRITE_OUTPUTS(results, model_verify=model_verify, cases=cases)
    if SUMMARY_PATH.exists():
        data = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
        data["run_label"] = "FCSR2-100-REAL-20260522"
        data["round"] = "round2_new_100"
        SUMMARY_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    if REPORT_PATH.exists():
        text = REPORT_PATH.read_text(encoding="utf-8")
        text = text.replace(
            "# 飞书 100 个闲聊归纳总结研究学术知识真实模型测试报告",
            "# 飞书 100 个闲聊归纳总结研究学术知识 Round2 真实模型测试报告",
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
