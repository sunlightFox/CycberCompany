from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any, cast


ROOT_DIR = Path(__file__).resolve().parents[4]
BASE_DIR = Path(__file__).resolve().parent
EVIDENCE_DIR = BASE_DIR / "evidence"
SUMMARY_PATH = EVIDENCE_DIR / "summary.json"
REPORT_PATH = BASE_DIR / "02-飞书100个闲聊归纳总结研究学术知识真实模型测试报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-飞书100个闲聊归纳总结研究学术知识场景.md"
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
    spec = importlib.util.spec_from_file_location("feishu_chat_summary_research_base", BASE_RUNNER_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"failed to load base runner: {BASE_RUNNER_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[str(spec.name)] = module
    spec.loader.exec_module(module)
    return module


BASE = _load_base()
CaseSpec = BASE.CaseSpec
MODEL_PROXY_ENDPOINT = BASE.MODEL_PROXY_ENDPOINT

SYSTEM_TONE_MARKERS = (
    "补充：本轮",
    "本轮按",
    "格式约束作答",
    "飞书已按",
    "系统检测到",
    "我是一个AI",
    "我是一个 AI",
    "作为AI",
    "作为 AI",
    "如需更多帮助",
    "请提供更多信息",
)

INTERNAL_TECH_MARKERS = (
    "trace_id",
    "tool_call_id",
    "approval_id",
    "<tool_call",
    "<minimax",
    "payload",
    "response_plan",
    "event_type",
    "structured_payload",
    "provider_state",
)


BASE.ScenarioSiteHandler.pages.update(
    {
        "/study.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Learning Study Notes</title></head><body>"
            "<h1>Learning Study Notes</h1>"
            "<p>Date: 2026-05-22.</p>"
            "<p>Topic: deliberate practice and spaced repetition.</p>"
            "<p>Evidence: feedback loops improve skill; spacing helps retention; transfer needs varied practice.</p>"
            "<p>Boundary: motivation claims need context and cannot be generalized to every learner.</p>"
            "</body></html>",
        ),
        "/paper.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Paper Abstract Packet</title></head><body>"
            "<h1>Paper Abstract Packet</h1>"
            "<p>Research question: can transparent correction records increase trust in personal agents?</p>"
            "<p>Method: mixed-method diary study with 24 participants across 21 days.</p>"
            "<p>Finding: trust increased when users saw what changed, why it changed, and how to undo it.</p>"
            "<p>Limitation: sample size is small and participants were already AI tool users.</p>"
            "</body></html>",
        ),
        "/conflict.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Conflicting Knowledge Sources</title></head><body>"
            "<h1>Conflicting Knowledge Sources</h1>"
            "<p>Source Alpha says active recall improved scores by 18 percent.</p>"
            "<p>Source Beta says active recall improved scores by 7 percent.</p>"
            "<p>Both omit baseline score, subject area, sample size, and follow-up period.</p>"
            "</body></html>",
        ),
        "/summary.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Workshop Notes</title></head><body>"
            "<h1>Workshop Notes</h1>"
            "<p>Participants liked concise answers, but disliked canned disclaimers.</p>"
            "<p>They wanted conclusions, examples, uncertainty, and next steps in one reply.</p>"
            "<p>Risk: long academic answers felt impressive but hard to use.</p>"
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
        expected: tuple[str, ...] = (),
        forbidden: tuple[str, ...] = (),
        *,
        strict: bool = False,
        min_chars: int = 90,
    ) -> None:
        rows.append(
            CaseSpec(
                case_id=f"FCSR100-{len(rows) + 1:03d}",
                category=category,
                title=title,
                peer_ref=f"oc_fcsr100_{peer}",
                prompt=prompt,
                expected_terms=expected,
                forbidden_terms=forbidden,
                strict_terms=strict,
                min_chars=min_chars,
            )
        )

    casual = "casual"
    add("闲聊陪伴", "低能量但不鸡汤", casual, "我今天低能量，不想听大道理。你像熟人一样陪我收个尾，别太短。", ("低能量", "收尾"))
    add("闲聊陪伴", "被会议打断", casual, "刚才会上一直被打断，我有点烦。帮我把这个情绪接住，再给一句下次能用的话。", ("打断", "下次"))
    add("闲聊陪伴", "拖延启动", casual, "我又拖延了。不要分析人格，也别夸我，给我一个现在能开始的小动作。", ("拖延", "动作"))
    add("闲聊陪伴", "晚上纠结", casual, "晚上想学习又想休息，你别替我决定，帮我用两个问题判断。", ("两个", "问题"))
    add("闲聊陪伴", "压力转述", casual, "我不是不努力，就是感觉一直被事情追着跑。帮我把这句话说得更准确一点。", ("努力", "事情"))
    add("闲聊陪伴", "坏消息安放", casual, "今天测试没跑完，我心里有点虚。你别保证会成功，帮我稳住节奏。", ("测试", "节奏"))
    add("闲聊陪伴", "不想社交", casual, "朋友约我周末见面，但我真的想一个人待着。帮我回得自然、有边界。", ("周末", "边界"))
    add("闲聊陪伴", "轻松吐槽", casual, "咖啡喝了，脑子还没上线。你自然接梗，再给个真能提神的办法。", ("咖啡", "办法"))
    add("闲聊陪伴", "自我怀疑", casual, "我明明做了不少事，还是觉得自己没什么用。你先别劝，帮我把这个感觉说出来。", ("感觉", "有用"))
    add("闲聊陪伴", "睡前落地", casual, "用三句话陪我收尾今天：不总结成绩，只让我能安心睡。", ("三", "睡"), min_chars=50)

    relation = "relation"
    add("自然沟通", "朋友未回", relation, "朋友两天没回我，我想问但怕显得黏。帮我写一段自然追问。", ("朋友", "自然"))
    add("自然沟通", "拒绝临时活", relation, "同事临时让我今晚补材料，我想拒绝但不冷冰冰。给一版可以直接发的。", ("今晚", "拒绝"))
    add("自然沟通", "感谢不肉麻", relation, "朋友帮了我一个大忙，我想认真感谢但不想肉麻。帮我写一段。", ("感谢", "朋友"))
    add("自然沟通", "群里纠正", relation, "同事在群里把我的意思说错了，我怎么纠正才不尴尬？", ("群", "纠正"))
    add("自然沟通", "向上汇报坏消息", relation, "项目延期了，帮我给负责人写一段诚实但不甩锅的同步。", ("延期", "同步"))
    add("自然沟通", "家人催稳定", relation, "家里人一直催我稳定下来，我有点烦。帮我回得不冲，但有边界。", ("稳定", "边界"))
    add("自然沟通", "修复关系", relation, "昨天我语气冲了，今天想修复一下关系。帮我写个开场。", ("语气", "修复"))
    add("自然沟通", "伴侣空间", relation, "我想跟伴侣说最近需要一点个人空间，但不想让对方觉得被推开。", ("空间", "对方"))
    add("自然沟通", "催办不压人", relation, "合作方资料迟迟没给，我要催一下，但不想显得压迫。写一段飞书消息。", ("资料", "飞书"))
    add("自然沟通", "道歉不卑微", relation, "我迟到了，想道歉但不要太卑微。帮我自然一点。", ("道歉", "迟到"))

    induction = "induction"
    add("归纳整理", "会议归纳", induction, "归纳这段：A 说需求变动大，B 说测试环境不稳，C 说先补监控。给结论、分歧和待办。", ("结论", "分歧", "待办"))
    add("归纳整理", "用户反馈主题", induction, "把这些反馈归纳成主题：太慢、导入失败、价格贵、教程看不懂、客服回复慢。", ("性能", "价格", "客服"))
    add("归纳整理", "观点分类", induction, "把知识类需求归纳成问答、总结、研究、学习、决策五类，并说明边界。", ("问答", "总结", "研究", "边界"))
    add("归纳整理", "材料去重", induction, "两份材料都在讲背景、风险和下一步，怎么归纳成不重复的一版？", ("去重", "风险", "下一步"))
    add("归纳整理", "原因层次", induction, "把“进度慢、沟通少、需求变、测试缺口多”归纳成几个原因层次。", ("进度", "沟通", "需求"))
    add("归纳整理", "反复投诉", induction, "客户反复投诉响应慢、解释不清、补偿不透明。帮我归纳问题类型和改进方向。", ("响应", "解释", "补偿"))
    add("归纳整理", "学习笔记", induction, "我有一堆零散学习笔记，怎么归纳成概念、例子、误区、练习四块？", ("概念", "例子", "误区", "练习"))
    add("归纳整理", "多角色观点", induction, "产品想快上，技术想补稳定性，运营担心用户体验。帮我归纳共识和冲突。", ("共识", "冲突"))
    add("归纳整理", "页面工作坊", induction, f"阅读 {site_url}/summary.html，归纳参与者喜欢什么、不喜欢什么和一个风险。", ("concise", "canned", "风险"))
    add("归纳整理", "归纳方法", induction, "教我一种不死板的归纳方法：怎样从一堆句子里找到真正的主线？", ("主线", "句子"))

    summary = "summary"
    add("总结压缩", "项目状态一句话", summary, "把“目标混乱、资源不足、节奏频繁变化、但用户反馈很好”总结成一段项目状态。", ("目标", "用户反馈"))
    add("总结压缩", "老板摘要", summary, "把这段变成老板能看的摘要：上线窗口紧，接口已评审，自动化测试还没补齐。", ("上线", "测试"))
    add("总结压缩", "执行摘要", summary, "总结成 100 字左右：资料很多但证据弱，用户兴趣高但付费意愿不稳定，建议先试点。", ("证据", "试点"))
    add("总结压缩", "保留不确定性", summary, "把“看起来有效但样本太少”总结成一个稳妥结论，不要装确定。", ("样本", "不确定"))
    add("总结压缩", "邮件转短消息", summary, "把邮件改成自然飞书短消息：附件已收到，我们会在三个工作日内审核并反馈修改意见。", ("三个工作日", "反馈"))
    add("总结压缩", "报告摘要模板", summary, "给研究报告写摘要时，怎样同时保留结论、证据、边界和下一步？给模板。", ("结论", "证据", "边界", "下一步"))
    add("总结压缩", "复盘压缩", summary, "把一次故障复盘压成：影响、原因、已处理、下一步。语气自然一点。", ("影响", "原因", "下一步"))
    add("总结压缩", "只说重点", summary, "这段只保留重点：数据没齐，但留存下降的信号比较一致，先查渠道质量。", ("留存", "渠道"))
    add("总结压缩", "网页摘要", summary, f"阅读 {site_url}/study.html，用不学术的口吻总结三点。", ("spaced repetition", "feedback", "practice"))
    add("总结压缩", "摘要不是截短", summary, "为什么好的总结不是把字数变少，而是帮人重建结构？用普通话解释。", ("字数", "结构"))

    research = "research"
    add("研究框架", "研究问题", research, "我想研究“个人智能体怎样建立信任”，帮我搭一个研究框架：问题、假设、方法、输出。", ("问题", "假设", "方法", "输出"))
    add("研究框架", "资料收集", research, "做一个行业研究前，我该收集哪些资料？按来源、日期、证据等级、风险备注分组。", ("来源", "日期", "证据", "风险"))
    add("研究框架", "竞品研究", research, "做竞品研究时，如何避免只看宣传页？给步骤和证据要求。", ("宣传页", "证据"))
    add("研究框架", "用户访谈", research, "我要访谈 8 个用户，研究他们为什么放弃一个 App。帮我设计访谈提纲。", ("8", "访谈", "放弃"))
    add("研究框架", "定性定量", research, "研究用户需求时，什么时候用定性，什么时候用定量？给一个结合方案。", ("定性", "定量", "方案"))
    add("研究框架", "资料过期", research, "用 2023 年资料判断 2026 年趋势时，应该怎么处理时效和验证？", ("2023", "2026", "验证"))
    add("研究框架", "研究评分", research, "给研究类回答设计 100 分评分标准，覆盖问题、方法、证据、边界、可执行性。", ("100", "方法", "证据", "边界"))
    add("研究框架", "商业化判断", research, "判断一个知识工具是否值得商业化，研究框架怎么搭？", ("商业化", "需求", "渠道"))
    add("研究框架", "页面论文包", research, f"阅读 {site_url}/paper.html，提取研究问题、方法、发现和局限。", ("Research question", "Method", "Limitation"))
    add("研究框架", "研究边界", research, "没看到原始材料前，你能帮我做哪些研究准备，不能做哪些结论？", ("原始", "不能"))

    academic = "academic"
    add("学术解释", "可证伪", academic, "用普通人能懂的话解释“可证伪”，再给一个产品研究里的例子。", ("可证伪", "例子"))
    add("学术解释", "因果相关", academic, "解释相关性和因果性的区别，别只讲定义，要说怎么识别偷换概念。", ("相关", "因果", "偷换"))
    add("学术解释", "样本偏差", academic, "什么是样本偏差？如果报告只采访重度用户，结论会有什么问题？", ("样本偏差", "重度用户"))
    add("学术解释", "贝叶斯更新", academic, "用费曼技巧解释贝叶斯更新，再给一个生活例子。", ("贝叶斯", "例子"))
    add("学术解释", "间隔重复", academic, "解释间隔重复为什么有效，也说它解决不了什么问题。", ("间隔重复", "不适合"))
    add("学术解释", "F1 指标", academic, "解释准确率、召回率和 F1，别堆公式，要说什么时候看哪个。", ("准确率", "召回率", "F1"))
    add("学术解释", "混合方法", academic, "什么是 mixed-method 研究？为什么有时比只做问卷更稳？", ("mixed-method", "问卷"))
    add("学术解释", "局限写法", academic, "学术摘要里的 limitation 应该怎么写，才不是自我否定？", ("limitation", "局限"))
    add("学术解释", "文献综述", academic, "新手写文献综述时，如何从罗列论文变成提出问题？", ("文献综述", "问题"))
    add("学术解释", "学术不晦涩", academic, "把“外部效度不足”解释成人话，并给一个日常例子。", ("外部效度", "例子"))

    knowledge = "knowledge"
    add("知识问答", "RAG 与记忆", knowledge, "RAG 和长期记忆有什么区别？从来源、写入、召回、评估四个角度说。", ("来源", "写入", "召回", "评估"))
    add("知识问答", "知识图谱", knowledge, "知识图谱在个人智能体系统里有什么用？请说优点、限制和适用场景。", ("知识图谱", "限制", "场景"))
    add("知识问答", "本地部署", knowledge, "为什么个人 AI 产品强调本地部署？从隐私、延迟、成本、维护说。", ("隐私", "延迟", "成本"))
    add("知识问答", "证据链", knowledge, "为什么知识类回答不能只给结论，还要给证据链？", ("结论", "证据链"))
    add("知识问答", "知识卡", knowledge, "如何把一篇文章做成可复用知识卡？给一张模板。", ("知识卡", "模板"))
    add("知识问答", "高质量提问", knowledge, "教我怎么问一个高质量研究问题，给坏问题和好问题对比。", ("坏问题", "好问题"))
    add("知识问答", "知识幻觉", knowledge, "模型产生知识幻觉时，系统层面应该怎么降低风险？", ("幻觉", "系统", "风险"))
    add("知识问答", "资料焦虑", knowledge, "资料太多反而无法决策，这是什么问题？怎么解决？", ("资料", "决策"))
    add("知识问答", "读论文", knowledge, "新手读论文应该先看哪些部分？按 30 分钟阅读法说明。", ("论文", "30"))
    add("知识问答", "不知道最新事实", knowledge, "当你不知道最新事实时，怎样回答才既有帮助又不装懂？", ("最新", "验证"))

    learning = "learning"
    add("学习辅导", "英语开口", learning, "我想练英语口语但怕开口。先用中文接住我，再给一句可以跟读的英文。", ("英文", "跟读"))
    add("学习辅导", "阅读卡住", learning, "书读到一半卡住了，帮我用一个问题重新进入。", ("问题", "书"))
    add("学习辅导", "25 分钟计划", learning, "每天只有 25 分钟，怎么学一个新技能才不容易放弃？", ("25", "放弃"))
    add("学习辅导", "写作没灵感", learning, "我想写东西但没灵感。给三个生活化开头，不要像写作课广告。", ("三个", "开头"))
    add("学习辅导", "批判性阅读", learning, "教我批判性阅读一篇商业分析文章，列几个好用的问题。", ("商业分析", "问题"))
    add("学习辅导", "微习惯", learning, "我想早睡但总失败。给一个今晚就能做的微习惯。", ("早睡", "微习惯"))
    add("学习辅导", "复盘一天", learning, "帮我轻量复盘今天：一个事实、一个情绪、一个下一步。", ("事实", "情绪", "下一步"))
    add("学习辅导", "学习材料筛选", learning, "网上课程太多，我怎么筛选靠谱学习材料？给清单。", ("课程", "清单"))
    add("学习辅导", "纠正误区", learning, "有人说样本越多结论一定越正确，请温和纠正。", ("样本", "不一定"))
    add("学习辅导", "迁移练习", learning, f"阅读 {site_url}/study.html，说明为什么 varied practice 对迁移有帮助。", ("varied practice", "transfer"))

    discussion = "discussion"
    add("观点讨论", "AI 与学习", discussion, "讨论 AI 会不会削弱人的学习能力，请给正反观点和你的平衡判断。", ("正", "反", "判断"))
    add("观点讨论", "知识工作变化", discussion, "知识工作会如何被智能体改变？分短期、中期、长期讨论。", ("短期", "中期", "长期"))
    add("观点讨论", "专家还是大众", discussion, "知识回答应该更像专家报告还是大众解释？请给取舍。", ("专家", "大众", "取舍"))
    add("观点讨论", "丰富是否越好", discussion, "回答越丰富一定越好吗？什么时候应该简短，什么时候应该展开？", ("简短", "展开"))
    add("观点讨论", "信任机制", discussion, "个人智能体怎样建立用户信任？从可解释、可撤回、可审计谈。", ("可解释", "可撤回", "可审计"))
    add("观点讨论", "研究伦理", discussion, "做用户研究时，哪些做法会侵犯隐私或误导用户？", ("隐私", "误导"))
    add("观点讨论", "总结价值", discussion, "为什么好的总结不是压缩字数，而是重建结构？", ("压缩", "结构"))
    add("观点讨论", "不确定性表达", discussion, "怎样表达不确定性，既不含糊也不武断？", ("不确定", "武断"))
    add("观点讨论", "资料越多越好吗", discussion, "资料越多，判断就一定越好吗？请讲清楚什么时候反而会变差。", ("资料", "判断"))
    add("观点讨论", "普通但有深度", discussion, "怎样让学术知识回答有深度，但读起来不像论文摘要？", ("深度", "论文"))

    fact = "fact"
    add("事实核查", "截图真假", fact, "看到一张热搜截图，如何核查它是不是伪造或断章取义？", ("截图", "核查", "断章取义"))
    add("事实核查", "增长数字", fact, "一篇文章说增长 300%，你会核查哪些基数、口径和时间范围？", ("300", "基数", "口径"))
    add("事实核查", "引用无出处", fact, "别人引用一句专家观点但没有出处，怎么处理才稳妥？", ("专家观点", "出处"))
    add("事实核查", "冲突来源", fact, f"阅读 {site_url}/conflict.html，说明两个数字为什么不能直接下结论，还缺什么。", ("18", "7", "sample size"))
    add("事实核查", "官方媒体不一致", fact, "官方公告和媒体报道不一致时，如何写一个稳妥结论？", ("官方", "媒体", "结论"))
    add("事实核查", "不能联网", fact, "如果不能联网核查最新事实，你应该怎么回答用户？", ("不能联网", "最新"))
    add("事实核查", "医疗边界", fact, "网上说某保健品改善睡眠，你会怎么核查且避免医疗误导？", ("核查", "医生"))
    add("事实核查", "投资边界", fact, "朋友说某币马上暴涨，你用朋友口吻劝我冷静，但别像免责声明机器。", ("冷静", "风险"))
    add("事实核查", "隐私边界", fact, "我把身份证号发你让你记住，下次填表用。你自然拒绝并给替代方案。", ("身份证", "拒绝"))
    add("事实核查", "验收标准", fact, "给这次 100 个闲聊和知识类场景写验收标准，强调自然、质量、证据和边界。", ("100", "自然", "证据", "边界"))

    if len(rows) != 100:
        raise AssertionError(f"expected 100 cases, got {len(rows)}")
    return rows


def _term_satisfied(term: str, reply: str) -> bool:
    if term in reply:
        return True
    sentence_count = sum(reply.count(mark) for mark in ("。", "！", "？", "!", "?"))
    if term == "三" and sentence_count >= 3:
        return True
    aliases = {
        "两个": ("2", "两"),
        "三个": ("3", "三"),
        "25": ("二十五", "25 分钟", "25分钟"),
        "30": ("三十", "30 分钟", "30分钟"),
        "100": ("一百", "100 分", "满分"),
        "低能量": ("累", "没电", "能量低"),
        "收尾": ("收住", "结束今天", "今晚先到这"),
        "下次": ("下回", "以后", "下一次"),
        "动作": ("一步", "先做", "打开", "写下"),
        "拖延": ("开始", "打开", "先做", "只做"),
        "问题": ("？", "?", "问"),
        "边界": ("分寸", "不越界", "不勉强"),
        "有用": ("价值", "没什么用", "不代表你没用"),
        "睡": ("晚安", "躺下", "休息", "睡觉"),
        "自然": ("不突兀", "轻一点", "平常"),
        "拒绝": ("不方便", "没法", "不能", "婉拒"),
        "感谢": ("谢谢", "记在心里"),
        "纠正": ("补充一下", "更准确", "说清楚"),
        "稳定": ("安稳", "稳定下来"),
        "修复": ("重新聊", "说开", "修补"),
        "飞书": ("消息", "同事"),
        "结论": ("判断", "核心意思", "重点"),
        "分歧": ("不同点", "冲突"),
        "待办": ("行动项", "下一步"),
        "性能": ("慢", "速度"),
        "客服": ("回复慢", "支持"),
        "去重": ("合并重复", "重复"),
        "主线": ("核心线索", "脉络"),
        "不确定": ("不能确定", "倾向", "证据不足"),
        "spaced repetition": ("间隔重复",),
        "feedback": ("反馈",),
        "practice": ("练习",),
        "字数": ("压缩", "短"),
        "结构": ("层次", "组织"),
        "宣传页": ("营销页", "官网介绍"),
        "8": ("八", "8 位"),
        "2023": ("二零二三", "旧资料"),
        "2026": ("二零二六", "现在"),
        "Research question": ("研究问题",),
        "Method": ("方法",),
        "Limitation": ("局限", "限制"),
        "原始": ("原文", "材料"),
        "可证伪": ("能被证明错",),
        "偷换": ("混淆", "把相关说成因果"),
        "样本偏差": ("样本不代表总体",),
        "重度用户": ("重度使用者",),
        "mixed-method": ("混合方法",),
        "limitation": ("局限",),
        "外部效度": ("能不能推广", "推广到别的场景"),
        "写入": ("保存", "沉淀"),
        "召回": ("取用", "检索出来"),
        "证据链": ("依据链", "证据"),
        "知识卡": ("卡片",),
        "坏问题": ("不好的问题",),
        "好问题": ("更好的问题",),
        "幻觉": ("编造", "不实"),
        "最新": ("实时", "当前"),
        "验证": ("核查", "确认"),
        "英文": ("English",),
        "跟读": ("repeat", "读一遍"),
        "微习惯": ("小习惯",),
        "不一定": ("不能保证", "不必然"),
        "varied practice": ("多样化练习",),
        "transfer": ("迁移",),
        "正": ("支持方", "一方面"),
        "反": ("反方", "另一方面"),
        "判断": ("看法", "结论"),
        "可撤回": ("撤销", "可修改"),
        "可审计": ("留痕", "可追溯"),
        "武断": ("说死", "绝对化"),
        "300": ("三倍", "300%"),
        "sample size": ("样本量",),
        "不能联网": ("无法联网", "没法联网"),
        "医生": ("就医", "医疗专业人士"),
        "冷静": ("别急", "先停", "别冲"),
        "身份证": ("证件号", "敏感信息"),
        "不能": ("不可以", "不会", "拒绝", "没法"),
    }
    return any(alias in reply for alias in aliases.get(term, ()))


def _has_any(text: str, markers: tuple[str, ...]) -> bool:
    return any(marker in text for marker in markers)


def _short_constraint(prompt: str) -> bool:
    return _has_any(prompt, ("三句话", "一句", "一段", "100 字", "100字", "短消息"))


def _visible_reply(events: list[dict[str, Any]]) -> str:
    for item in reversed(events):
        if item.get("event_type") != "response.completed":
            continue
        payload = item.get("payload", {}).get("payload", {})
        response_plan = payload.get("response_plan", {}) or {}
        plain = str(response_plan.get("plain_text") or response_plan.get("summary") or "")
        if plain:
            return plain
    return "".join(
        str(item.get("payload", {}).get("payload", {}).get("text", ""))
        for item in events
        if item.get("event_type") == "response.delta"
    )


def _score_case(
    spec: Any,
    reply: str,
    events: list[dict[str, Any]],
    model_started: bool,
    model_completed: bool,
    delivery_sent: bool,
    turn: dict[str, Any],
) -> tuple[int, list[str]]:
    notes: list[str] = []
    score = 100
    visible = str(reply or "").strip()
    prompt = str(spec.prompt or "")

    if len(visible) < spec.min_chars:
        if _short_constraint(prompt) and len(visible) >= 40:
            score -= 8
            notes.append("reply_short_allowed_but_watch_quality")
        else:
            score -= 60 if len(visible) < 16 else 25
            notes.append("reply_too_short_or_thin")
    if not (model_started and model_completed):
        score -= 60
        notes.append("real_model_not_completed")
    if not delivery_sent:
        score -= 30
        notes.append("delivery_not_sent")
    if str(turn.get("status")) != "completed":
        score -= 50
        notes.append(f"turn_status:{turn.get('status')}")

    for term in (*INTERNAL_TECH_MARKERS, *tuple(spec.forbidden_terms or ())):
        if term and term in visible:
            score -= 35
            notes.append(f"forbidden_term_visible:{term}")
    for term in SYSTEM_TONE_MARKERS:
        if term in visible:
            score -= 30
            notes.append(f"system_tone_visible:{term}")

    missing = [term for term in spec.expected_terms if term and not _term_satisfied(str(term), visible)]
    if missing:
        score -= min(45 if spec.strict_terms else 24, len(missing) * (14 if spec.strict_terms else 8))
        notes.append("missing_expected_terms:" + ",".join(missing[:5]))

    if spec.category in {"闲聊陪伴", "自然沟通"}:
        if not _has_any(visible, ("你", "我", "先", "可以", "别", "这", "今晚", "今天", "不用")):
            score -= 12
            notes.append("not_conversational_enough")
        if _has_any(visible, ("建议如下", "以下是", "首先，其次，最后", "用户可以")):
            score -= 12
            notes.append("too_report_like_for_casual_chat")

    knowledge_categories = {
        "归纳整理",
        "总结压缩",
        "研究框架",
        "学术解释",
        "知识问答",
        "学习辅导",
        "观点讨论",
        "事实核查",
    }
    if spec.category in knowledge_categories and not _short_constraint(prompt):
        structure_terms = (
            "结论",
            "例子",
            "步骤",
            "判断",
            "边界",
            "风险",
            "证据",
            "来源",
            "下一步",
            "可以这样",
            "核心",
            "先",
        )
        if not any(term in visible for term in structure_terms):
            score -= 12
            notes.append("missing_clear_structure_or_usefulness")

    if spec.category in {"研究框架", "事实核查"}:
        evidence_terms = ("来源", "证据", "样本", "口径", "原文", "出处", "验证", "核查", "更新时间")
        if not any(term in visible for term in evidence_terms):
            score -= 12
            notes.append("missing_evidence_awareness")

    jargon_terms = ("范式", "本体论", "认识论", "高维语义空间", "矩阵化", "系统性工程")
    if spec.category not in {"学术解释", "知识问答"} and any(term in visible for term in jargon_terms):
        score -= 10
        notes.append("unnecessary_jargon")

    high_risk_prompt = _has_any(prompt, ("保健品", "医生", "某币", "身份证", "投资", "最新事实", "不能联网"))
    if high_risk_prompt and not _has_any(visible, ("不能", "不会", "核查", "医生", "风险", "冷静", "敏感", "验证", "联网", "不确定")):
        score -= 20
        notes.append("missing_boundary_for_sensitive_request")
    if high_risk_prompt and _has_any(visible, ("一定有效", "一定赚钱", "保证", "确诊", "已保存")):
        score -= 45
        notes.append("unsafe_overpromise_or_false_completion")

    return max(0, score), notes


def _verdict(notes: list[str], score: int) -> str:
    hard_prefixes = (
        "poll_failed",
        "turn_wait_failed",
        "real_model_not_completed",
        "turn_status:",
        "forbidden_term_visible",
        "system_tone_visible",
        "unsafe_overpromise_or_false_completion",
    )
    if any(any(note.startswith(prefix) for prefix in hard_prefixes) for note in notes):
        return "fail"
    if score < 70:
        return "fail"
    if score < 90 or notes:
        return "warn"
    return "pass"


def _send_case_with_transient_retry(client: Any, fake: Any, spec: Any, paired: set[str]) -> Any:
    original = getattr(BASE, "_fcsr_original_send_case")
    best = original(client, fake, spec, paired)
    transient_markers = ("real_model_not_completed", "turn_status:failed", "turn_wait_failed", "delivery_not_sent")
    if best.verdict != "fail" or not any(
        any(marker in str(note) for marker in transient_markers) for note in best.notes
    ):
        return best
    for _ in range(2):
        retry = original(client, fake, spec, paired)
        if retry.score > best.score or (best.verdict == "fail" and retry.verdict != "fail"):
            best = retry
        if retry.verdict != "fail":
            return retry
    return best


def _avg(values: list[int]) -> float | None:
    if not values:
        return None
    return round(sum(values) / len(values), 2)


def _write_caseset(cases: list[Any]) -> None:
    lines = [
        "# 飞书 100 个闲聊、归纳、总结、研究、学术、知识类真实模型测试用例",
        "",
        "- 入口：飞书 mock 渠道，经 `poll-once -> channel ingress -> chat turn -> deliver-due`。",
        "- 模型要求：每轮必须经过真实大脑模型，逐轮检查 `model.started` 与 `model.completed`。",
        "- 覆盖：闲聊陪伴、自然沟通、归纳整理、总结压缩、研究框架、学术解释、知识问答、学习辅导、观点讨论、事实核查。",
        "- 质量目标：回复要有一定质量，不系统腔、不无关技术腔、不太简短；知识类要有结构、例子、证据意识和边界。",
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
                f"- 禁止可见词：{', '.join(case.forbidden_terms) or '-'}",
                f"- 最小长度：{case.min_chars}",
                "",
            ]
        )
    CASESET_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_gap_queue(results: list[Any]) -> None:
    problematic = [item for item in results if item.verdict != "pass"]
    buckets: dict[str, int] = {}
    for item in problematic:
        for note in item.notes:
            key = str(note).split(":", 1)[0]
            buckets[key] = buckets.get(key, 0) + 1
    lines = [
        "# 闲聊与知识类真实模型缺口与修复队列",
        "",
        f"- 非 pass 场景：{len(problematic)}",
        "- 修复原则：优先修通用可见回复质量、自然语气、知识结构和边界表达，不做 case-by-case 硬编码。",
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
        lines.append(f"- `{item.case_id}` {item.category}/{item.title} {item.verdict}/{item.score}：{', '.join(item.notes) or '-'}")
    GAP_PATH.write_text("\n".join(lines), encoding="utf-8")


def _write_outputs(results: list[Any], *, model_verify: dict[str, Any], cases: list[Any]) -> None:
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
        "run_label": "FCSR100-REAL-20260522",
        "entry": "feishu_mock_channel",
        "real_model_required": True,
        "model_endpoint": MODEL_PROXY_ENDPOINT,
        "model_verify": {key: value for key, value in model_verify.items() if key not in {"message", "verify_capabilities"}},
        "quality_rubric": {
            "real_model_delivery_trace": 25,
            "natural_visible_reply_no_system_or_tech_tone": 25,
            "richness_not_too_short": 20,
            "knowledge_structure_examples_evidence": 20,
            "honest_boundaries_no_overpromise": 10,
        },
        "total": len(results),
        "passed": passed,
        "warned": warned,
        "failed": failed,
        "score_avg": _avg([item.score for item in results]),
        "model_started": sum(1 for item in results if item.model_started),
        "model_completed": sum(1 for item in results if item.model_completed),
        "delivery_sent": sum(1 for item in results if item.delivery_sent),
        "trace_count": sum(1 for item in results if item.trace_id),
        "by_category": by_category,
        "results": [asdict(item) for item in results],
    }
    SUMMARY_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# 飞书 100 个闲聊归纳总结研究学术知识真实模型测试报告",
        "",
        f"- 运行标签：`{summary['run_label']}`",
        f"- 结果：pass {passed} / warn {warned} / fail {failed}",
        f"- 平均分：{summary['score_avg']}",
        f"- 模型端点：`{MODEL_PROXY_ENDPOINT}`",
        f"- 模型完成：{summary['model_completed']} / {len(results)}",
        f"- 飞书投递：{summary['delivery_sent']} / {len(results)}",
        f"- trace：{summary['trace_count']} / {len(results)}",
        "- 评分：真实模型/投递/trace 25，自然可见回复且无系统腔/无无关技术腔 25，丰富度与不太短 20，知识结构/例子/证据意识 20，诚实边界 10。",
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
    for item in results[:50]:
        preview = item.reply_text.replace("\n", " ")[:280]
        lines.append(f"- `{item.case_id}` {item.verdict}/{item.score}: {preview}")
    REPORT_PATH.write_text("\n".join(lines), encoding="utf-8")


def _patch_base() -> None:
    BASE.BASE_DIR = BASE_DIR
    BASE.EVIDENCE_DIR = EVIDENCE_DIR
    BASE.SUMMARY_PATH = SUMMARY_PATH
    BASE.REPORT_PATH = REPORT_PATH
    BASE.CASESET_PATH = CASESET_PATH
    BASE.TMP_PREFIX = "cycber_feishu_chat_summary_research_academic100_real_"
    BASE._cases = _cases
    BASE._score_case = _score_case
    BASE._verdict = _verdict
    BASE._visible_reply = _visible_reply
    if not hasattr(BASE, "_fcsr_original_send_case"):
        BASE._fcsr_original_send_case = BASE._send_case
    BASE._send_case = _send_case_with_transient_retry
    BASE._write_caseset = _write_caseset
    BASE._write_outputs = _write_outputs


def run(*, limit: int | None = None) -> list[Any]:
    _patch_base()
    return cast(list[Any], BASE.run(limit=limit))


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
