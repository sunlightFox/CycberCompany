from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any


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
REPORT_PATH = BASE_DIR / "02-飞书100个知识类真实模型测试执行报告.md"
CASESET_PATH = BASE_DIR / "01-飞书100个知识类真实模型测试场景.md"


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("feishu_broad_real_runner", SOURCE_RUNNER)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base runner: {SOURCE_RUNNER}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[str(spec.name)] = module
    spec.loader.exec_module(module)
    return module


base = _load_base()
_BASE_SCORE_CASE = base._score_case

base.BASE_DIR = BASE_DIR
base.EVIDENCE_DIR = EVIDENCE_DIR
base.SUMMARY_PATH = SUMMARY_PATH
base.REPORT_PATH = REPORT_PATH
base.CASESET_PATH = CASESET_PATH
base.TMP_PREFIX = "cycber_feishu_knowledge100_real_"

base.ScenarioSiteHandler.pages.update(
    {
        "/research.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Knowledge Research Packet</title></head><body>"
            "<h1>Knowledge Research Packet</h1>"
            "<p>Date: 2026-05-21.</p>"
            "<p>Topic: personal AI operating systems.</p>"
            "<p>Evidence: memory governance, approval traceability, channel reliability.</p>"
            "<p>Open question: long-term trust requires transparent correction records.</p>"
            "</body></html>",
        ),
        "/market.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Market Notes</title></head><body>"
            "<h1>Market Notes</h1>"
            "<p>Segment A values privacy and local deployment.</p>"
            "<p>Segment B values integration speed and ready-made workflows.</p>"
            "<p>Risk: source freshness and vendor claims must be verified.</p>"
            "</body></html>",
        ),
        "/conflict.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Conflicting Sources</title></head><body>"
            "<h1>Conflicting Sources</h1>"
            "<p>Source Alpha says adoption grew 18 percent.</p>"
            "<p>Source Beta says adoption grew 9 percent.</p>"
            "<p>Both omit sample size, collection method, and update time.</p>"
            "</body></html>",
        ),
    }
)


def _knowledge_cases(site_url: str) -> list[Any]:
    rows: list[Any] = []

    def add(
        category: str,
        title: str,
        peer: str,
        prompt: str,
        expected: tuple[str, ...],
        *,
        min_chars: int = 100,
        strict: bool = False,
        forbidden: tuple[str, ...] = (),
    ) -> None:
        rows.append(
            base.CaseSpec(
                case_id=f"FKNOW100-{len(rows) + 1:03d}",
                category=category,
                title=title,
                peer_ref=f"oc_knowledge100_{peer}_{len(rows) + 1:03d}",
                prompt=prompt,
                expected_terms=expected,
                forbidden_terms=forbidden,
                strict_terms=strict,
                min_chars=min_chars,
            )
        )

    # 资料收集：强调字段、来源、证据和更新口径。
    add("资料收集", "行业资料字段", "collect", "我要收集个人 AI 助手行业资料，给我一份资料字段清单，必须包含来源、日期、证据等级和风险备注。", ("来源", "日期", "证据", "风险"), strict=True)
    add("资料收集", "产品调研清单", "collect", "调研一个知识库产品时，应收集哪些信息？请按基础信息、功能、价格、风险、证据来源分组。", ("功能", "价格", "证据来源", "风险"))
    add("资料收集", "专家访谈提纲", "collect", "我要采访 5 位重度笔记软件用户，帮我设计访谈问题，能支持后续归纳分析。", ("5", "问题", "归纳"))
    add("资料收集", "竞品资料表", "collect", "给我一个竞品资料表字段，适合比较 6 个 SaaS 工具，不要生成文件，只给字段和评分维度。", ("字段", "评分", "维度"))
    add("资料收集", "政策材料收集", "collect", "收集某项新政策资料时，怎样区分官方原文、媒体解读和个人评论？", ("官方", "媒体", "评论"))
    add("资料收集", "论文资料卡", "collect", "给我一张论文资料卡模板，包含研究问题、方法、样本、结论、局限。", ("研究问题", "方法", "局限"))
    add("资料收集", "用户反馈编码", "collect", "我有 200 条用户反馈，想归类问题类型，先给编码框架和抽样检查方法。", ("200", "编码", "抽样"))
    add("资料收集", "本地页面资料", "collect", f"读取 {site_url}/research.html，提取主题、证据和未解决问题。", ("personal AI operating systems", "memory governance", "未解决问题"))
    add("资料收集", "来源可信度表", "collect", "给我一个来源可信度评估表，适合判断网页、报告、访谈和论坛帖。", ("可信度", "网页", "报告", "访谈"))
    add("资料收集", "资料收集流程", "collect", "设计一个资料收集流程：从问题定义到资料入库，要说明每一步产物。", ("问题定义", "入库", "产物"))

    # 深度问答：要求解释清楚、避免空泛。
    add("问答", "RAG 与长期记忆", "qa", "RAG 和长期记忆有什么区别？请从来源、写入、召回、评估四个角度解释。", ("来源", "写入", "召回", "评估"))
    add("问答", "为什么要证据链", "qa", "为什么知识类回答不能只给结论，还要给证据链？请结合误导风险说明。", ("结论", "证据链", "风险"))
    add("问答", "什么是可证伪", "qa", "用普通人能懂的话解释“可证伪”，再给一个产品研究中的例子。", ("可证伪", "例子"))
    add("问答", "因果和相关", "qa", "解释相关性和因果性的区别，给出判断一篇文章是否偷换概念的办法。", ("相关", "因果", "偷换"))
    add("问答", "样本偏差", "qa", "什么是样本偏差？如果一份报告只采访重度用户，结论会有什么问题？", ("样本偏差", "重度用户"))
    add("问答", "知识图谱用途", "qa", "知识图谱在个人智能体系统里有什么用？请说优点、限制和适用场景。", ("知识图谱", "限制", "场景"))
    add("问答", "评测指标解释", "qa", "解释准确率、召回率、F1，别只给公式，要说明什么时候看哪个。", ("准确率", "召回率", "F1"))
    add("问答", "隐私与本地部署", "qa", "为什么很多个人 AI 产品强调本地部署？请从隐私、延迟、成本、维护讨论。", ("隐私", "延迟", "成本", "维护"))
    add("问答", "专家意见权重", "qa", "专家意见和真实用户数据冲突时，应该如何权衡？", ("专家", "用户数据", "权衡"))
    add("问答", "知识边界", "qa", "当你不知道最新事实时，怎样回答才既有帮助又不装懂？", ("不确定", "最新", "验证"))

    # 总结归纳：不同输入形态的压缩和结构化。
    add("总结归纳", "会议要点归纳", "summary", "把这段归纳成结论、证据、待办：A 说需求不稳定，B 说用户最关心导出，C 说先做权限。", ("结论", "证据", "待办"))
    add("总结归纳", "长段压缩", "summary", "把“市场热度高、用户愿意尝试、但付费意愿不稳定，且竞品更新很快”总结成 3 条判断。", ("市场", "付费", "竞争"), min_chars=80)
    add("总结归纳", "多观点归纳", "summary", "甲认为先扩功能，乙认为先修稳定性，丙担心支持成本。请归纳分歧、共识和下一步。", ("分歧", "共识", "下一步"))
    add("总结归纳", "报告摘要", "summary", "给一份研究报告写摘要时，如何避免把假设写成事实？请给模板。", ("假设", "事实", "模板"))
    add("总结归纳", "用户反馈主题", "summary", "归纳这些反馈主题：太慢、价格贵、导入失败、教程看不懂、客服回复慢。", ("性能", "价格", "导入"), min_chars=80)
    add("总结归纳", "二级分类", "summary", "把知识类需求分成问答、总结、研究、分析、学习五类，并说明边界。", ("问答", "总结", "研究", "分析", "学习"))
    add("总结归纳", "结论先行", "summary", "把下面内容改成结论先行：资料还不够，但目前证据更支持先做小范围试点。", ("证据", "试点"), min_chars=80)
    add("总结归纳", "风险摘要", "summary", "把一个研究项目的主要风险归纳为数据风险、解释风险、执行风险，每类给例子。", ("数据风险", "解释风险", "执行风险"))
    add("总结归纳", "时间线", "summary", "把资料整理成时间线时，应记录哪些字段，如何处理日期不确定？", ("时间线", "日期", "不确定"))
    add("总结归纳", "本地页面摘要", "summary", f"读取 {site_url}/market.html，归纳两个用户分群和一个风险。", ("Segment A", "Segment B", "source freshness"), min_chars=80)

    # 研究框架：强调问题、假设、方法、评估。
    add("研究框架", "研究计划", "research", "帮我设计一个研究计划：问题是“个人智能体怎样建立信任”，包含假设、资料、方法、输出。", ("假设", "资料", "方法", "输出"))
    add("研究框架", "竞品研究法", "research", "做竞品研究时，如何避免只看宣传页？请给步骤和证据要求。", ("宣传页", "证据"))
    add("研究框架", "用户研究", "research", "我要研究用户为什么放弃一个 App，给我定性和定量结合的方案。", ("定性", "定量", "方案"))
    add("研究框架", "技术选型研究", "research", "研究向量数据库选型时，应该比较哪些维度？请说明评分方法。", ("向量数据库", "维度", "评分"))
    add("研究框架", "学习路径研究", "research", "帮我研究一个人如何从零学数据分析，先给资料收集与课程筛选框架。", ("资料", "课程", "筛选"))
    add("研究框架", "市场进入研究", "research", "如果要判断一个小工具是否值得商业化，研究框架怎么搭？", ("商业化", "需求", "渠道"))
    add("研究框架", "案例研究", "research", "如何做一个失败产品的案例研究？请给资料来源、分析维度和输出结构。", ("资料来源", "分析维度", "输出"))
    add("研究框架", "趋势研究", "research", "研究 2026 年知识管理趋势时，如何处理过期资料和短期噪音？", ("2026", "过期", "噪音"))
    add("研究框架", "指标研究", "research", "设计一个聊天质量研究框架，指标要覆盖准确性、帮助度、边界、安全。", ("准确性", "帮助度", "边界", "安全"))
    add("研究框架", "研究评分标准", "research", "给这类知识研究回答设计评分标准，满分 100，至少 5 个维度。", ("100", "维度", "评分"))

    # 分析比较：结构化判断与权衡。
    add("分析比较", "方案对比", "analysis", "对比“先做自动化测试”和“先修用户反馈问题”，给出适用条件和风险。", ("适用条件", "风险"))
    add("分析比较", "框架优缺点", "analysis", "比较 SWOT、PEST、五力模型在个人项目分析中的适用边界。", ("SWOT", "PEST", "五力"))
    add("分析比较", "成本收益", "analysis", "分析把资料整理自动化的成本收益，别只说好处，也说维护成本。", ("收益", "维护成本"))
    add("分析比较", "定性定量", "analysis", "什么时候该用定性分析，什么时候该用定量分析？给例子。", ("定性", "定量", "例子"))
    add("分析比较", "用户群比较", "analysis", f"读取 {site_url}/market.html，比较 Segment A 和 Segment B 的不同诉求。", ("Segment A", "Segment B"), min_chars=80)
    add("分析比较", "冲突数据分析", "analysis", f"读取 {site_url}/conflict.html，分析两个增长数字为什么不能直接下结论。", ("18", "9", "sample size"))
    add("分析比较", "风险优先级", "analysis", "把知识类回答的风险按严重度排序：过期、无来源、编造、泄露隐私、建议越界。", ("过期", "无来源", "编造", "隐私"))
    add("分析比较", "质量诊断", "analysis", "一段回答内容很多但没有结论，如何诊断问题并改进？", ("结论", "诊断", "改进"))
    add("分析比较", "证据权重", "analysis", "官方文档、第三方测评、用户评论、个人博客，权重如何排序？", ("官方文档", "用户评论", "权重"))
    add("分析比较", "决策矩阵", "analysis", "给我一个决策矩阵，比较三种学习资料：书、课程、项目实战。", ("书", "课程", "项目实战"))

    # 观点探讨：开放问题但要有结构。
    add("观点探讨", "AI 与学习", "discussion", "讨论 AI 会不会削弱人的学习能力，请给正反观点和你的平衡判断。", ("正", "反", "判断"))
    add("观点探讨", "知识工作变化", "discussion", "知识工作会如何被智能体改变？请分短期、中期、长期讨论。", ("短期", "中期", "长期"))
    add("观点探讨", "信任机制", "discussion", "个人智能体怎样建立用户信任？从可解释、可撤回、可审计角度谈。", ("可解释", "可撤回", "可审计"))
    add("观点探讨", "资料焦虑", "discussion", "资料太多反而无法决策，这是什么问题？怎么解决？", ("资料", "决策"))
    add("观点探讨", "总结的价值", "discussion", "为什么好的总结不是压缩字数，而是重建结构？", ("压缩", "结构"))
    add("观点探讨", "研究伦理", "discussion", "做用户研究时，哪些做法会侵犯隐私或误导用户？", ("隐私", "误导"))
    add("观点探讨", "知识幻觉", "discussion", "模型产生知识幻觉时，系统层面应该怎么降低风险？", ("幻觉", "系统", "风险"))
    add("观点探讨", "专家与大众", "discussion", "知识回答应该更像专家报告还是大众解释？请给取舍。", ("专家", "大众", "取舍"))
    add("观点探讨", "答案丰富度", "discussion", "回答越丰富一定越好吗？什么时候应该简短，什么时候应该展开？", ("简短", "展开"))
    add("观点探讨", "不确定性表达", "discussion", "怎样表达不确定性，既不含糊也不武断？", ("不确定", "武断"))

    # 学习解释：面向学习者的分层说明。
    add("学习解释", "费曼解释", "learn", "用费曼技巧解释“贝叶斯更新”，再给一个生活例子。", ("贝叶斯", "例子"))
    add("学习解释", "类比解释", "learn", "用类比解释“向量检索”，但最后指出类比哪里不准确。", ("向量检索", "类比", "准确"))
    add("学习解释", "学习计划", "learn", "给我一个 7 天入门信息检索的学习计划，每天有目标和练习。", ("7", "目标", "练习"))
    add("学习解释", "概念辨析", "learn", "辨析摘要、归纳、分析、评论四个概念，给短例子。", ("摘要", "归纳", "分析", "评论"))
    add("学习解释", "错误纠正", "learn", "有人说“样本越多结论一定越正确”，请纠正这个说法。", ("样本", "结论", "不一定"))
    add("学习解释", "提问方法", "learn", "教我怎么问一个高质量研究问题，给坏问题和好问题对比。", ("坏问题", "好问题"))
    add("学习解释", "阅读论文", "learn", "新手读论文应该先看哪些部分？按 30 分钟阅读法说明。", ("30", "论文"))
    add("学习解释", "做笔记", "learn", "如何把一篇文章做成可复用知识卡？给模板。", ("知识卡", "模板"))
    add("学习解释", "批判性阅读", "learn", "教我批判性阅读一篇商业分析文章，列检查问题。", ("商业分析", "问题"))
    add("学习解释", "复习策略", "learn", "解释间隔重复为什么有效，并说它不适合解决什么问题。", ("间隔重复", "不适合"))

    # 决策支持：不替用户拍板，给框架与条件。
    add("决策支持", "是否做专题", "decision", "我是否该做一个 AI 知识库专题？请给决策框架，不要替我拍板。", ("框架", "不"))
    add("决策支持", "优先级排序", "decision", "资料收集、访谈、竞品分析、原型验证，资源有限时怎么排序？", ("排", "资源"))
    add("决策支持", "证据不足", "decision", "现在只有 3 条用户反馈，我能得出什么结论，不能得出什么结论？", ("3", "能", "不能"))
    add("决策支持", "继续还是停止", "decision", "一个研究项目做了一半发现方向可能错了，怎么判断继续、调整或停止？", ("继续", "调整", "停止"))
    add("决策支持", "购买知识服务", "decision", "要不要买一个 1999 元的知识付费课？给判断清单和风险。", ("1999", "清单", "风险"))
    add("决策支持", "选题筛选", "decision", "我有 10 个选题，如何筛出最值得研究的 3 个？", ("10", "3", "筛"))
    add("决策支持", "团队汇报", "decision", "把研究结论汇报给团队前，哪些内容必须可复核？", ("可复核", "结论"))
    add("决策支持", "MVP 判断", "decision", "如何判断一个知识工具 MVP 是否达到了继续投入的标准？", ("MVP", "标准"))
    add("决策支持", "风险闸门", "decision", "设计一个知识报告发布前的风险闸门，防止误导和泄密。", ("风险闸门", "误导", "泄密"))
    add("决策支持", "下一步建议", "decision", "基于“资料多但证据弱”，给我下一步研究建议。", ("资料", "证据", "下一步"))

    # 事实核查：处理冲突、过期和不确定。
    add("事实核查", "截图核查", "fact", "看到一张热搜截图，如何核查它是不是伪造或断章取义？", ("截图", "核查", "断章取义"))
    add("事实核查", "数字核查", "fact", "一篇文章说增长 300%，你会核查哪些基数和时间范围？", ("300", "基数", "时间范围"))
    add("事实核查", "引用核查", "fact", "别人引用了一句专家观点，但没有出处，如何处理？", ("专家观点", "出处"), min_chars=60)
    add("事实核查", "过期资料", "fact", "一份 2023 年报告还能不能用于 2026 年判断？请给判断规则。", ("2023", "2026", "规则"))
    add("事实核查", "多源冲突", "fact", f"读取 {site_url}/conflict.html，给出事实核查结论和还缺什么。", ("18", "9", "还缺"))
    add("事实核查", "统计口径", "fact", "两个团队都说自己转化率高，但口径不同，怎么核查？", ("转化率", "口径"))
    add("事实核查", "官方与媒体", "fact", "官方公告和媒体报道不一致时，如何写一个稳妥结论？", ("官方", "媒体", "结论"))
    add("事实核查", "无法联网", "fact", "如果不能联网核查最新事实，你应该怎么回答用户？", ("不能联网", "最新"))
    add("事实核查", "资料缺口", "fact", "资料缺少样本量、采集方式和更新时间，还能得出什么层级的结论？", ("样本量", "采集方式", "更新时间"))
    add("事实核查", "谣言传播", "fact", "判断一条谣言的传播路径时，应该收集哪些证据？", ("谣言", "传播", "证据"))

    # 质量评分：明确评估标准。
    add("质量评分", "知识回答评分", "quality", "给知识类回答设计 100 分评分标准，覆盖准确性、结构、证据、边界、可执行性。", ("100", "准确性", "结构", "证据", "边界"))
    add("质量评分", "丰富度评分", "quality", "如何判断一条回答内容足够丰富但不啰嗦？给评分标准。", ("丰富", "啰嗦", "评分"))
    add("质量评分", "总结质量评分", "quality", "给总结类任务制定评分标准：信息保真、结构清晰、重点突出、行动项。", ("保真", "结构", "行动项"))
    add("质量评分", "研究质量评分", "quality", "给研究框架类回答制定评分标准，要求能检查资料来源和方法。", ("资料来源", "方法", "评分"))
    add("质量评分", "分析质量评分", "quality", "分析类回答常见失败模式有哪些？如何扣分？", ("失败模式", "扣分"))
    add("质量评分", "问答质量评分", "quality", "问答类回答怎样算高质量？从正确、清楚、例子、边界说。", ("正确", "清楚", "例子", "边界"))
    add("质量评分", "事实核查评分", "quality", "事实核查回答怎么评分？要包含来源、冲突处理和不确定性表达。", ("来源", "冲突", "不确定性"))
    add("质量评分", "用户体验评分", "quality", "知识回答除了内容正确，还要怎样让用户读起来轻松？", ("结论", "轻松"))
    add("质量评分", "安全扣分项", "quality", "知识类回答哪些情况应该直接判 fail？请包含编造来源、泄露隐私、医疗法律越界。", ("fail", "编造", "隐私"))
    add("质量评分", "本轮验收", "quality", "给这次 100 个知识类飞书真实模型场景写验收标准，包含模型、通道、质量和证据。", ("100", "飞书", "真实模型", "证据"))

    return rows


def _score_case(
    spec: Any,
    reply: str,
    events: list[dict[str, Any]],
    model_started: bool,
    model_completed: bool,
    delivery_sent: bool,
    turn: dict[str, Any],
) -> tuple[int, list[str]]:
    score, notes = _BASE_SCORE_CASE(
        spec,
        reply,
        events,
        model_started,
        model_completed,
        delivery_sent,
        turn,
    )
    visible = str(reply or "")
    if len(visible.strip()) < spec.min_chars:
        score -= 15
        notes.append("knowledge_answer_too_thin")
    if spec.category not in {"问答", "学习解释"}:
        shape_terms = (
            "结论",
            "建议",
            "步骤",
            "维度",
            "风险",
            "证据",
            "来源",
            "下一步",
            "判断",
            "主题",
            "类别",
            "分类",
            "评分标准",
            "核查",
            "口径",
            "样本",
        )
        if not any(term in visible for term in shape_terms):
            score -= 10
            notes.append("missing_knowledge_structure")
    source_required_markers = (
        "来源",
        "证据",
        "核查",
        "可信度",
        "网页",
        "报告",
        "官方",
        "读取",
        "过期",
        "资料来源",
    )
    source_required = spec.category == "事实核查" or any(
        marker in spec.prompt or marker in spec.title for marker in source_required_markers
    )
    evidence_terms = ("来源", "证据", "原始", "样本", "出处", "口径", "更新时间", "采集方式")
    if source_required and spec.category in {"事实核查", "资料收集", "研究框架"} and not any(term in visible for term in evidence_terms):
        score -= 10
        notes.append("missing_source_or_evidence_awareness")
    return max(0, score), notes


def _write_caseset(cases: list[Any]) -> None:
    lines = [
        "# 飞书 100 个知识类真实模型测试场景",
        "",
        "- 入口：飞书渠道 mock connector。",
        "- 模型：真实大脑模型，逐轮要求 `model.started` 和 `model.completed`。",
        "- 覆盖：资料收集、问答、总结归纳、研究框架、分析比较、观点探讨、学习解释、决策支持、事实核查、质量评分。",
        "- 评分重点：内容丰富度、结构化程度、来源/证据意识、边界表达、可执行性。",
        "",
    ]
    for case in cases:
        lines.extend(
            [
                f"## {case.case_id} {case.title}",
                f"- 分类：{case.category}",
                f"- 飞书 peer：`{case.peer_ref}`",
                f"- 输入：{case.prompt}",
                f"- 期望关键词：{', '.join(case.expected_terms) or '-'}",
                f"- 最小长度：{case.min_chars}",
                "",
            ]
        )
    CASESET_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_outputs(results: list[Any], *, model_verify: dict[str, Any], cases: list[Any]) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    _write_caseset(cases)
    passed = sum(1 for item in results if item.verdict == "pass")
    warned = sum(1 for item in results if item.verdict == "warn")
    failed = sum(1 for item in results if item.verdict == "fail")
    by_category: dict[str, dict[str, int]] = {}
    for item in results:
        bucket = by_category.setdefault(item.category, {"total": 0, "pass": 0, "warn": 0, "fail": 0})
        bucket["total"] += 1
        bucket[item.verdict] += 1
    summary = {
        "run_label": "FKNOW100-REAL-20260521",
        "entry": "feishu_mock_channel",
        "real_model_required": True,
        "model_endpoint": base.MODEL_PROXY_ENDPOINT,
        "model_verify": {key: value for key, value in model_verify.items() if key not in {"message", "verify_capabilities"}},
        "quality_rubric": {
            "real_model_and_delivery": 25,
            "expected_terms_and_task_fit": 25,
            "richness_and_specificity": 20,
            "source_evidence_boundary": 20,
            "safety_and_no_internal_leakage": 10,
        },
        "total": len(results),
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "score_avg": base._avg([item.score for item in results]),
        "by_category": by_category,
        "results": [asdict(item) for item in results],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 飞书 100 个知识类真实模型测试执行报告",
        "",
        f"- 结果：{passed} pass / {warned} warn / {failed} fail。",
        f"- 平均分：{summary['score_avg']}。",
        f"- 模型端点：`{base.MODEL_PROXY_ENDPOINT}`。",
        "- 评分标准：真实模型与投递 25，任务贴合与关键词 25，丰富度 20，来源/证据/边界 20，安全与无内部泄露 10。",
        "",
        "## 分类统计",
        "",
        "| 分类 | 总数 | Pass | Warn | Fail |",
        "|---|---:|---:|---:|---:|",
    ]
    for category, stats in by_category.items():
        lines.append(f"| {category} | {stats['total']} | {stats['pass']} | {stats['warn']} | {stats['fail']} |")
    lines.extend(["", "## 明细", "", "| Case | 分类 | 场景 | 判定 | 分数 | 模型 | 投递 | 路由 | 备注 |", "|---|---|---|---:|---:|---|---|---|---|"])
    for item in results:
        lines.append(
            "| {case} | {category} | {title} | {verdict} | {score} | {model} | {delivered} | {route} | {notes} |".format(
                case=item.case_id,
                category=item.category,
                title=item.title,
                verdict=item.verdict,
                score=item.score,
                model="ok" if item.model_started and item.model_completed else "no",
                delivered="ok" if item.delivery_sent else "no",
                route=item.route_type or item.task_status or "-",
                notes=", ".join(item.notes) or "-",
            )
        )
    lines.extend(["", "## 样例回复摘录", ""])
    for item in results[:35]:
        preview = item.reply_text.replace("\n", " ")[:260]
        lines.append(f"- `{item.case_id}` {item.verdict}/{item.score}: {preview}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


base._cases = _knowledge_cases
base._score_case = _score_case
base._write_caseset = _write_caseset
base._write_outputs = _write_outputs


def main() -> None:
    base.main()


if __name__ == "__main__":
    main()
