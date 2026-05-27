from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import time
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
REPORT_PATH = BASE_DIR / "02-飞书100个办公场景真实模型测试执行报告.md"
CASESET_PATH = BASE_DIR / "01-测试用例-飞书100个办公场景真实模型.md"


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
_BASE_SEND_CASE = base._send_case

base.BASE_DIR = BASE_DIR
base.EVIDENCE_DIR = EVIDENCE_DIR
base.SUMMARY_PATH = SUMMARY_PATH
base.REPORT_PATH = REPORT_PATH
base.CASESET_PATH = CASESET_PATH
base.TMP_PREFIX = "cycber_feishu_office100_real_"

base.ScenarioSiteHandler.pages.update(
    {
        "/competitors.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Office Competitor Notes</title></head><body>"
            "<h1>Office Competitor Notes</h1>"
            "<p>Date: 2026-05-22.</p>"
            "<p>AlphaSheet: fast Excel cleanup, weak audit trail.</p>"
            "<p>BetaDeck: strong PPT templates, expensive enterprise plan.</p>"
            "<p>GammaDocs: reliable Word/PDF export, limited integrations.</p>"
            "</body></html>",
        ),
        "/finance.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Finance Snapshot</title></head><body>"
            "<h1>Finance Snapshot</h1>"
            "<p>Q1 revenue 1280, cost 860, receivables overdue 210.</p>"
            "<p>Q2 revenue 1510, cost 990, receivables overdue 360.</p>"
            "<p>Risk: cash collection slowed while revenue increased.</p>"
            "</body></html>",
        ),
        "/hr.html": (
            "text/html; charset=utf-8",
            "<html><head><title>Hiring Packet</title></head><body>"
            "<h1>Hiring Packet</h1>"
            "<p>Role: operations analyst.</p>"
            "<p>Must have: Excel modeling, SQL basics, written communication.</p>"
            "<p>Nice to have: dashboard experience and process automation.</p>"
            "</body></html>",
        ),
    }
)


def _office_cases(site_url: str) -> list[Any]:
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
        forbidden: tuple[str, ...] = (),
    ) -> None:
        rows.append(
            base.CaseSpec(
                case_id=f"FOFFICE100-{len(rows) + 1:03d}",
                category=category,
                title=title,
                peer_ref=f"oc_office100_{peer}_{len(rows) + 1:03d}",
                prompt=prompt,
                expected_terms=expected,
                forbidden_terms=forbidden,
                strict_terms=strict,
                min_chars=min_chars,
            )
        )

    # 01-10 文档交付：Word/PDF/PPT/Markdown 等常见办公输出。
    add("文档交付", "项目周报 Word", "docs", "我是一名项目经理，把本周完成接口联调、风险是测试环境不稳定、下周补回归测试，整理成适合导出 Word 的项目周报。", ("Word", "周报", "风险", "下周"), strict=True)
    add("文档交付", "高层汇报 PPT", "docs", "我是运营负责人，要做 6 页 PPT 汇报 5 月增长复盘，请给每页标题、核心要点和图表建议。", ("PPT", "标题", "图表", "复盘"), strict=True)
    add("文档交付", "制度 PDF 大纲", "docs", "我是行政主管，准备输出一份 PDF 版差旅报销制度，请先整理目录、适用范围、审批流程和注意事项。", ("PDF", "目录", "审批流程", "注意事项"))
    add("文档交付", "会议纪要 Markdown", "docs", "我是产品助理，把会议内容整理成 Markdown：结论、行动项、负责人、截止时间、待确认事项。", ("Markdown", "行动项", "负责人", "截止时间"), strict=True)
    add("文档交付", "客户提案 Word", "docs", "我是售前顾问，帮我把客户需求、方案亮点、实施计划、风险控制整理成 Word 提案结构。", ("Word", "客户需求", "实施计划", "风险控制"))
    add("文档交付", "培训讲义", "docs", "我是 HR，要给新员工做办公安全培训讲义，内容包含账号、文件、邮件、外发资料和审批边界。", ("培训", "账号", "邮件", "审批"))
    add("文档交付", "一页纸简报", "docs", "我是市场经理，把新品调研结论整理成一页纸简报，要求先结论后证据，再列下一步。", ("结论", "证据", "下一步"))
    add("文档交付", "合同审阅纪要", "docs", "我是法务助理，把合同审阅结果整理成纪要：关键条款、风险等级、修改建议、待确认人。", ("关键条款", "风险等级", "修改建议", "待确认"))
    add("文档交付", "日报转周报", "docs", "我是客服主管，把 5 天日报合并成周报框架，避免流水账，突出问题趋势和改进动作。", ("周报", "趋势", "改进"))
    add("文档交付", "交付验收单", "docs", "我是实施经理，设计一份项目交付验收单，包含交付物、验收标准、证据、未结项和签收。", ("交付物", "验收标准", "证据", "签收"))

    # 11-20 Excel/表格处理。
    add("表格处理", "销售 Excel 分析", "sheet", "我是销售运营，把 1月收入120成本80、2月收入150成本95、3月收入180成本130 整理成 Excel 分析口径，并给利润率判断。", ("Excel", "利润率", "判断"))
    add("表格处理", "线索转化表", "sheet", "我是销售主管，分析 A 渠道线索120成交24，B 渠道线索80成交20，C 渠道线索200成交22，给转化率表和建议。", ("转化率", "渠道", "建议"))
    add("表格处理", "预算差异表", "sheet", "我是财务 BP，预算 50 万、实际 57 万，请设计差异分析表字段，区分价格差、数量差和一次性因素。", ("预算", "实际", "差异", "一次性因素"))
    add("表格处理", "Excel 清洗规则", "sheet", "我是数据专员，客户表有重复手机号、空公司名、地区写法不统一，给 Excel 清洗步骤和校验规则。", ("重复", "空值", "统一", "校验"))
    add("表格处理", "透视表方案", "sheet", "我是运营分析师，订单表有日期、地区、品类、销售额、毛利，应该怎么做透视表看趋势？", ("透视表", "日期", "地区", "毛利"))
    add("表格处理", "异常值处理", "sheet", "我是审计助理，报销表里有金额异常、发票号重复、周末提交，请给筛查规则和风险分级。", ("异常", "重复", "风险分级"))
    add("表格处理", "表单字段设计", "sheet", "我是行政，设计办公用品领用表字段，要方便后续 Excel 汇总和库存核对。", ("字段", "Excel", "库存"))
    add("表格处理", "KPI 看板数据", "sheet", "我是客服运营，设计客服 KPI 表：响应时长、解决率、满意度、升级率，给公式和解释。", ("响应时长", "解决率", "满意度", "公式"))
    add("表格处理", "两表合并", "sheet", "我是人事，员工基础表和考勤表要合并，说明匹配键、冲突处理、缺失值和复核步骤。", ("匹配键", "冲突", "缺失值", "复核"))
    add("表格处理", "CSV 转 Excel", "sheet", "我是电商运营，CSV 订单数据要转成 Excel 汇总，请给导入、清洗、分组统计和输出步骤。", ("CSV", "Excel", "清洗", "分组统计"))

    # 21-30 资料收集与联网整理。
    add("资料收集", "竞品网页摘要", "research", f"我是产品经理，读取 {site_url}/competitors.html，整理三家竞品的优势、风险和适合放进汇报的结论。", ("AlphaSheet", "BetaDeck", "GammaDocs", "风险"), strict=True)
    add("资料收集", "联网收集计划", "research", "我是咨询顾问，要收集智能办公工具资料，给高质量收集计划：关键词、来源优先级、证据等级和去重方法。", ("关键词", "来源优先级", "证据等级", "去重"))
    add("资料收集", "资料卡模板", "research", "我是研究员，设计一张资料卡模板，包含来源、日期、摘要、证据、可信度、可复核链接和使用限制。", ("来源", "日期", "可信度", "使用限制"))
    add("资料收集", "网页信息边界", "research", "我是运营，网页宣传说功能很强，如何避免把宣传页当成事实？给核查步骤。", ("宣传页", "事实", "核查"))
    add("资料收集", "多来源冲突", "research", "我是战略分析师，两份资料对市场规模结论冲突，如何整理进汇报而不误导？", ("冲突", "不确定", "汇报"))
    add("资料收集", "素材入库", "research", "我是知识库管理员，网上收集到 30 篇材料，如何命名、打标签、去重、记录来源并入库？", ("命名", "标签", "去重", "来源"))
    add("资料收集", "政策资料整理", "research", "我是政府事务专员，整理政策资料时如何区分官方原文、媒体解读和专家评论？", ("官方原文", "媒体解读", "专家评论"))
    add("资料收集", "客户行业简报", "research", "我是客户成功经理，要给客户做行业简报，先给资料收集字段和最终简报结构。", ("字段", "简报结构", "客户"))
    add("资料收集", "旧资料更新", "research", "我是市场研究员，手上是 2023 年报告，要用于 2026 年判断，应该怎么标注时效和补充验证？", ("2023", "2026", "时效", "验证"))
    add("资料收集", "引用格式", "research", "我是助理，要把网上内容整理成报告，请给引用格式，避免没有来源、断章取义和过度概括。", ("引用", "来源", "断章取义"))

    # 31-40 合并、转换与格式整理。
    add("合并转换", "两份资料合并", "merge", "我是办公室文员，两份材料一份讲背景一份讲预算，如何合并成一份不重复、逻辑清楚的汇报？", ("合并", "去重", "逻辑"))
    add("合并转换", "Word 转 PPT", "merge", "我是培训经理，长 Word 讲义要转 PPT，给拆页原则、标题提炼和图表化建议。", ("Word", "PPT", "标题", "图表"))
    add("合并转换", "PPT 转纪要", "merge", "我是总经理助理，把一份 PPT 汇报转成会议纪要，应该保留哪些信息，删掉哪些展示性内容？", ("PPT", "会议纪要", "保留", "删掉"))
    add("合并转换", "PDF 摘要", "merge", "我是采购，供应商 PDF 很长，如何提取价格、交付周期、售后条款和风险点？", ("PDF", "价格", "交付周期", "风险"))
    add("合并转换", "MD 输出规范", "merge", "我是技术写作人员，把办公流程输出成 Markdown 文档，给标题层级、表格和检查清单规范。", ("Markdown", "标题层级", "表格", "检查清单"))
    add("合并转换", "多版本合并", "merge", "我是法务，合同有 A/B/C 三个修订版本，如何合并差异并形成待确认清单？", ("版本", "差异", "待确认"))
    add("合并转换", "图片文字整理", "merge", "我是行政，截图里有会议安排，先不做 OCR 文件，只说明如何整理成日程表并复核。", ("截图", "日程表", "复核"))
    add("合并转换", "录音纪要框架", "merge", "我是秘书，会议录音转文字后，如何整理成摘要、决议、行动项和争议点？", ("摘要", "决议", "行动项", "争议点"))
    add("合并转换", "附件命名规则", "merge", "我是项目助理，设计一套附件命名规则，适合合同、报价单、验收单和会议纪要。", ("命名规则", "合同", "报价单", "验收单"))
    add("合并转换", "格式统一", "merge", "我是运营，多个部门给的材料格式混乱，如何统一口径、标题、数字单位和时间格式？", ("口径", "标题", "单位", "时间格式"))

    # 41-50 HR、简历和行政办公。
    add("HR行政", "简历筛选标准", "hr", "我是 HR，要筛选运营分析师简历，请给硬性条件、加分项、风险信号和面试追问。", ("硬性条件", "加分项", "风险信号", "追问"))
    add("HR行政", "岗位网页提取", "hr", f"我是招聘专员，读取 {site_url}/hr.html，提取岗位必须项、加分项和筛选表字段。", ("operations analyst", "Excel", "SQL", "字段"), strict=True)
    add("HR行政", "面试评价表", "hr", "我是招聘经理，设计面试评价表，包含能力项、评分标准、证据记录和是否通过建议。", ("能力项", "评分标准", "证据", "通过"))
    add("HR行政", "候选人对比", "hr", "我是 HRBP，两个候选人一个经验强但沟通弱，一个潜力强但经验浅，如何做对比并给建议？", ("对比", "沟通", "潜力", "建议"))
    add("HR行政", "入职清单", "hr", "我是行政 HR，设计新员工入职清单，覆盖账号、设备、合同、培训、权限和资料归档。", ("账号", "设备", "合同", "权限"))
    add("HR行政", "考勤异常", "hr", "我是 HR，考勤表显示连续迟到和补卡频繁，如何整理事实、风险和沟通话术？", ("迟到", "补卡", "事实", "话术"))
    add("HR行政", "绩效材料", "hr", "我是部门主管，准备绩效沟通材料，如何把事实、贡献、问题和改进计划写清楚？", ("事实", "贡献", "改进计划"))
    add("HR行政", "培训反馈", "hr", "我是培训负责人，50 份培训反馈要归纳成主题、满意度、改进项和下期计划。", ("50", "满意度", "改进项"))
    add("HR行政", "行政采购比价", "hr", "我是行政采购，三家办公椅报价不同，如何做比价表并避免只看最低价？", ("比价", "最低价", "质量", "售后"))
    add("HR行政", "会议室排期", "hr", "我是行政，会议室冲突频繁，给一个排期规则和异常处理流程。", ("排期", "冲突", "流程"))

    # 51-60 财务、审计和经营报表。
    add("财务报表", "财务网页摘要", "finance", f"我是财务经理，读取 {site_url}/finance.html，整理收入、成本、逾期应收和现金风险。", ("Q1", "Q2", "overdue", "cash"), strict=True)
    add("财务报表", "月度经营报表", "finance", "我是财务 BP，设计月度经营报表结构，包含收入、成本、毛利、费用、现金流和风险解释。", ("收入", "成本", "毛利", "现金流"))
    add("财务报表", "应收账款分析", "finance", "我是财务，客户 A 逾期 30 天、客户 B 逾期 75 天，如何做应收风险分级和催收建议？", ("逾期", "风险分级", "催收"))
    add("财务报表", "费用报销审查", "finance", "我是审计，报销单要检查发票、金额、审批、业务合理性和重复报销，请给清单。", ("发票", "审批", "重复报销"))
    add("财务报表", "预算滚动预测", "finance", "我是财务分析师，如何把实际数、预算数和预测数整理成滚动预测表？", ("实际数", "预算数", "预测数"))
    add("财务报表", "利润下降解释", "finance", "我是经营分析，收入增长但利润下降，如何从成本、价格、产品结构和费用找原因？", ("收入增长", "利润下降", "成本", "产品结构"))
    add("财务报表", "老板版财务摘要", "finance", "我是财务主管，把复杂财务报表改写成老板 1 分钟能看懂的摘要。", ("摘要", "收入", "风险"))
    add("财务报表", "现金流预警", "finance", "我是出纳，设计现金流预警表，包含期初余额、预计流入、预计流出、缺口和动作。", ("现金流", "流入", "流出", "缺口"))
    add("财务报表", "审计底稿", "finance", "我是审计助理，整理审计底稿时要记录哪些证据、抽样方法和结论边界？", ("证据", "抽样", "结论边界"))
    add("财务报表", "发票台账", "finance", "我是会计，设计发票台账字段，方便后续核销、对账和税务检查。", ("发票", "核销", "对账", "税务"))

    # 61-70 项目、运营、法务和客服协作。
    add("协作运营", "项目风险清单", "ops", "我是项目经理，整理项目风险清单，包含风险描述、影响、概率、负责人、缓解措施和截止时间。", ("风险描述", "影响", "负责人", "缓解措施"))
    add("协作运营", "需求优先级", "ops", "我是产品经理，需求很多但资源有限，给 RICE 或类似框架做优先级排序。", ("RICE", "优先级", "排序"))
    add("协作运营", "客服问题归因", "ops", "我是客服主管，投诉包含响应慢、退款慢、说明不清、系统报错，归纳主题并给改进动作。", ("响应慢", "退款", "系统报错", "改进"))
    add("协作运营", "SOP 制作", "ops", "我是运营，给发票申请流程写 SOP，包含触发条件、步骤、责任人、异常和记录。", ("SOP", "责任人", "异常", "记录"))
    add("协作运营", "合同风险表", "ops", "我是法务，供应商合同里付款、违约、数据安全和保密条款要做风险表。", ("付款", "违约", "数据安全", "保密"))
    add("协作运营", "客户周会材料", "ops", "我是客户成功，准备客户周会材料，包含进展、问题、风险、需客户决策事项。", ("进展", "问题", "风险", "决策"))
    add("协作运营", "营销活动复盘", "ops", "我是市场运营，活动复盘要包含目标、数据、转化、成本、经验和下一步。", ("目标", "转化", "成本", "下一步"))
    add("协作运营", "采购审批说明", "ops", "我是采购，要写采购审批说明，解释为什么选 B 供应商而不是最低价 A。", ("审批", "供应商", "最低价"))
    add("协作运营", "OKR 梳理", "ops", "我是部门负责人，把模糊目标整理成 OKR，要求 O 清楚、KR 可衡量、动作可落地。", ("OKR", "KR", "可衡量"))
    add("协作运营", "跨部门同步", "ops", "我是 PMO，跨部门同步信息混乱，给一个同步模板和升级机制。", ("同步模板", "升级机制", "负责人"))

    # 71-80 桌面、文件和系统操作边界。
    add("系统文件", "桌面整理方案", "system", "我是普通办公人员，电脑桌面很乱，只给方案不执行：如何按项目、类型、时间整理，并保留可回滚记录？", ("方案", "项目", "时间", "可回滚"))
    add("系统文件", "批量重命名", "system", "我是行政，100 个扫描件要批量重命名，只给安全方案：命名规则、预览、备份、冲突处理。", ("100", "预览", "备份", "冲突"))
    add("系统文件", "重复文件清理", "system", "我是办公室文员，想清理重复文件，说明如何识别、确认、备份，不能直接删除。", ("重复文件", "确认", "备份", "删除"))
    add("系统文件", "敏感文件外发", "system", "我是财务，准备外发报表，如何检查是否含身份证、银行卡、工资等敏感信息？", ("身份证", "银行卡", "工资", "敏感"))
    add("系统文件", "下载附件风险", "system", "我是助理，收到陌生邮件附件，如何判断能不能下载和打开？", ("陌生邮件", "附件", "风险"))
    add("系统文件", "文件归档", "system", "我是项目助理，项目结束后如何归档文件、记录版本、权限和验收证据？", ("归档", "版本", "权限", "验收证据"))
    add("系统文件", "本地搜索", "system", "我是知识工作者，如何给本地资料建立目录和关键词，提升搜索效率？", ("目录", "关键词", "搜索效率"))
    add("系统文件", "文件误删恢复", "system", "我是办公人员，发现文件可能误删，先不要乱操作，应该如何保留现场并恢复？", ("误删", "保留现场", "恢复"))
    add("系统文件", "权限申请", "system", "我是新人，需要访问共享盘，如何写权限申请，说明用途、范围、期限和审批人？", ("权限", "用途", "范围", "期限"))
    add("系统文件", "自动化边界", "system", "我是运营，想让系统自动整理桌面，哪些动作必须先确认或审批？", ("自动", "确认", "审批"))

    # 81-90 邮件、会议和日常沟通。
    add("沟通会议", "邮件改写", "comm", "我是销售，把这句改成礼貌但明确的邮件：你们资料一直没给，导致我们无法推进。", ("邮件", "资料", "推进"))
    add("沟通会议", "催办话术", "comm", "我是项目经理，供应商延期交付，写一段催办话术，既坚定又保留合作关系。", ("延期", "催办", "合作"))
    add("沟通会议", "会议议程", "comm", "我是部门助理，设计 30 分钟项目例会议程，包含目标、时间分配、决策项和行动项。", ("30", "议程", "决策项", "行动项"))
    add("沟通会议", "会议纪要质量", "comm", "我是秘书，如何判断会议纪要写得好不好？给评分标准。", ("会议纪要", "评分标准"))
    add("沟通会议", "向上汇报", "comm", "我是基层主管，坏消息要向上汇报，如何按事实、影响、方案和需求表达？", ("事实", "影响", "方案", "需求"))
    add("沟通会议", "跨部门协商", "comm", "我是运营，需要研发帮忙但对方排期满，写一段协商话术和备选方案。", ("研发", "排期", "备选方案"))
    add("沟通会议", "客户道歉信", "comm", "我是客服主管，系统故障影响客户，写道歉信框架，包含事实、补救、承诺和联系方式。", ("道歉", "补救", "承诺"))
    add("沟通会议", "群公告", "comm", "我是行政，写一条办公区搬迁群公告，包含时间、地点、影响、联系人。", ("公告", "时间", "地点", "联系人"))
    add("沟通会议", "简短同步", "comm", "我是产品经理，把复杂进展压缩成飞书群三句话，保留结论、风险和下一步。", ("结论", "风险", "下一步"))
    add("沟通会议", "复盘提问", "comm", "我是团队负责人，设计复盘会提问清单，覆盖目标、事实、原因、经验和改进。", ("目标", "事实", "原因", "改进"))

    # 91-100 办公质量验收与闭环。
    add("质量验收", "办公输出评分", "quality", "给办公类回答设计 100 分评分标准，覆盖任务理解、交付结构、准确性、效率、风险和下一步。", ("100", "任务理解", "交付结构", "风险"))
    add("质量验收", "Word 交付验收", "quality", "我是项目经理，如何验收一份 Word 周报是否高质量？给检查清单。", ("Word", "检查清单", "高质量"))
    add("质量验收", "Excel 交付验收", "quality", "我是财务，如何验收 Excel 分析表是否可靠？覆盖公式、口径、数据源和异常值。", ("Excel", "公式", "口径", "数据源"))
    add("质量验收", "PPT 交付验收", "quality", "我是管理者，如何验收 PPT 汇报是否清楚有说服力？", ("PPT", "清楚", "说服力"))
    add("质量验收", "资料整理验收", "quality", "我是研究负责人，如何验收资料整理是否充分、可复核、没有遗漏关键风险？", ("充分", "可复核", "风险"))
    add("质量验收", "简历筛选验收", "quality", "我是 HRD，如何验收简历筛选结果是否公平、可解释、可追溯？", ("公平", "可解释", "可追溯"))
    add("质量验收", "财务报表验收", "quality", "我是 CFO，如何验收经营报表是否能用于决策？给硬性标准。", ("经营报表", "决策", "硬性标准"))
    add("质量验收", "桌面整理验收", "quality", "我是行政主管，如何验收桌面整理或文件归档任务没有误删、泄密和漏归档？", ("误删", "泄密", "漏归档"))
    add("质量验收", "办公任务闭环", "quality", "什么情况下才能说一个办公任务真正闭环？请从结果、文件、证据、风险、交接五方面回答。", ("闭环", "文件", "证据", "交接"))
    add("质量验收", "本轮测试标准", "quality", "给这次 100 个飞书办公真实模型场景写验收标准，要求体现真实模型、办公效率、交付质量和安全边界。", ("100", "飞书", "真实模型", "安全边界"))

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
        notes.append("office_answer_too_thin")

    structure_terms = (
        "步骤",
        "字段",
        "结构",
        "清单",
        "模板",
        "标准",
        "框架",
        "话术",
        "方案",
        "表头",
        "公式",
        "风险",
        "证据",
        "下一步",
        "负责人",
        "验收",
        "建议",
        "结论",
    )
    if not any(term in visible for term in structure_terms):
        score -= 10
        notes.append("missing_office_structure")

    if spec.category in {"资料收集", "财务报表", "质量验收"}:
        evidence_terms = ("来源", "证据", "口径", "复核", "数据源", "验证", "可追溯")
        if not any(term in visible for term in evidence_terms):
            score -= 10
            notes.append("missing_evidence_or_recheck_awareness")

    if spec.category in {"系统文件", "HR行政", "财务报表"}:
        safety_terms = ("确认", "审批", "备份", "权限", "敏感", "风险", "复核", "边界")
        if not any(term in visible for term in safety_terms):
            score -= 10
            notes.append("missing_office_safety_boundary")

    false_done_terms = ("已生成文件", "已经整理桌面", "已经删除", "已经发送", "已经筛选完")
    if spec.category in {"系统文件", "资料收集"} and any(term in visible for term in false_done_terms):
        score -= 35
        notes.append("office_false_completion")
    return max(0, score), notes


def _write_caseset(cases: list[Any]) -> None:
    lines = [
        "# 飞书 100 个办公场景真实模型测试用例",
        "",
        "- 入口：飞书 mock 渠道，经 `poll-once -> channel ingress -> chat turn -> deliver-due`。",
        "- 模型：要求真实大脑大模型调用，逐轮检查 `model.started` 和 `model.completed`。",
        "- 覆盖：Word、Excel、PDF、PPT、Markdown、资料合并、联网收集、桌面整理、简历筛选、财务报表、会议邮件、办公验收。",
        "- 验收重点：处理效率、交付结构、内容质量、证据/复核意识、权限与安全边界、自然办公沟通体验。",
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
        "run_label": "FOFFICE100-REAL-20260522",
        "entry": "feishu_mock_channel",
        "real_model_required": True,
        "model_endpoint": base.MODEL_PROXY_ENDPOINT,
        "model_verify": {key: value for key, value in model_verify.items() if key not in {"message", "verify_capabilities"}},
        "quality_rubric": {
            "real_model_and_delivery": 25,
            "office_task_fit_and_efficiency": 25,
            "deliverable_structure_and_specificity": 20,
            "evidence_recheck_and_data_quality": 15,
            "permission_safety_and_no_false_completion": 15,
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
        "# 飞书 100 个办公场景真实模型测试执行报告",
        "",
        f"- 结果：{passed} pass / {warned} warn / {failed} fail。",
        f"- 平均分：{summary['score_avg']}。",
        f"- 模型端点：`{base.MODEL_PROXY_ENDPOINT}`。",
        "- 评分标准：真实模型与投递 25，办公任务贴合与效率 25，交付结构与具体性 20，证据/复核/数据质量 15，权限安全与不虚假完成 15。",
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


base._cases = _office_cases
base._score_case = _score_case
base._write_caseset = _write_caseset
base._write_outputs = _write_outputs


def _send_case_with_transient_retry(client: Any, fake: Any, spec: Any, paired: Any) -> Any:
    first = _BASE_SEND_CASE(client, fake, spec, paired)
    transient_notes = tuple(str(note) for note in getattr(first, "notes", ()))
    model_missing = not (getattr(first, "model_started", False) and getattr(first, "model_completed", False))
    turn_failed = any(note.startswith(("turn_status:failed", "turn_wait_failed", "poll_failed")) for note in transient_notes)
    if first.verdict == "fail" and (model_missing or turn_failed):
        second = _BASE_SEND_CASE(client, fake, spec, paired)
        if second.verdict != "fail" or (
            getattr(second, "model_started", False) and getattr(second, "model_completed", False)
        ):
            second.notes = [*getattr(second, "notes", ()), "retried_after_transient_model_or_turn_failure"]
            return second
    return first


base._send_case = _send_case_with_transient_retry


def _case_from_payload(payload: dict[str, Any]) -> Any:
    fields = set(getattr(base.CaseResult, "__dataclass_fields__", {}) or {})
    return base.CaseResult(**{key: value for key, value in payload.items() if key in fields})


def _read_summary_results() -> list[Any]:
    if not SUMMARY_PATH.exists():
        return []
    try:
        payload = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = payload.get("results")
    if not isinstance(rows, list):
        return []
    results: list[Any] = []
    for item in rows:
        if isinstance(item, dict):
            try:
                results.append(_case_from_payload(item))
            except TypeError:
                continue
    return results


def _read_model_verify() -> dict[str, Any]:
    if not SUMMARY_PATH.exists():
        return {"status": "unknown"}
    try:
        payload = json.loads(SUMMARY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"status": "unknown"}
    verify = payload.get("model_verify")
    return dict(verify) if isinstance(verify, dict) else {"status": "unknown"}


def _read_casewise_results() -> list[Any]:
    results: list[Any] = []
    for path in sorted(EVIDENCE_DIR.glob("casewise_FOFFICE100-*_result.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict):
                results.append(_case_from_payload(payload))
        except (OSError, json.JSONDecodeError, TypeError):
            continue
    return results


def _write_casewise_result(result: Any) -> None:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    path = EVIDENCE_DIR / f"casewise_{result.case_id}_result.json"
    path.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")


def _all_cases_for_selection() -> list[Any]:
    return _office_cases("http://127.0.0.1:0")


def _selected_case_ids(
    *,
    case_ids: set[str] | None = None,
    only_problematic: bool = False,
) -> set[str]:
    all_ids = {case.case_id for case in _all_cases_for_selection()}
    if case_ids:
        return set(case_ids)
    if only_problematic:
        problematic = {
            item.case_id
            for item in [*_read_summary_results(), *_read_casewise_results()]
            if item.verdict != "pass"
        }
        return problematic or all_ids
    return all_ids


def _ordered_results(results_by_id: dict[str, Any]) -> list[Any]:
    return [
        results_by_id[case.case_id]
        for case in _all_cases_for_selection()
        if case.case_id in results_by_id
    ]


def _is_better_result(candidate: Any, current: Any) -> bool:
    verdict_rank = {"fail": 0, "warn": 1, "pass": 2}
    return (
        verdict_rank.get(candidate.verdict, 0),
        candidate.model_completed,
        candidate.delivery_sent,
        candidate.score,
        len(candidate.reply_text or ""),
    ) > (
        verdict_rank.get(current.verdict, 0),
        current.model_completed,
        current.delivery_sent,
        current.score,
        len(current.reply_text or ""),
    )


def run_selected(
    *,
    limit: int | None = None,
    case_ids: set[str] | None = None,
    only_problematic: bool = False,
    merge_existing: bool = False,
) -> list[Any]:
    if not case_ids and not only_problematic:
        return base.run(limit=limit)

    selected_ids = _selected_case_ids(case_ids=case_ids, only_problematic=only_problematic)
    previous_results: dict[str, Any] = {}
    if merge_existing:
        previous_results.update({item.case_id: item for item in _read_summary_results()})
        previous_results.update({item.case_id: item for item in _read_casewise_results()})

    original_cases = base._cases

    def selected_cases(site_url: str) -> list[Any]:
        cases = [case for case in _office_cases(site_url) if case.case_id in selected_ids]
        if limit is not None:
            return cases[:limit]
        return cases

    base._cases = selected_cases
    try:
        current_results = base.run(limit=None)
    finally:
        base._cases = original_cases

    if not merge_existing:
        return current_results

    for item in current_results:
        previous = previous_results.get(item.case_id)
        previous_results[item.case_id] = item if previous is None or _is_better_result(item, previous) else previous
        _write_casewise_result(previous_results[item.case_id])
    ordered = _ordered_results(previous_results)
    _write_outputs(ordered, model_verify=_read_model_verify(), cases=_all_cases_for_selection())
    return ordered


def _run_casewise(
    *,
    case_ids: set[str] | None = None,
    only_problematic: bool = False,
    timeout_seconds: int = 180,
    retries: int = 1,
    case_pause_seconds: float = 0,
    infra_backoff_seconds: float = 0,
) -> list[Any]:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    selected_ids = _selected_case_ids(case_ids=case_ids, only_problematic=only_problematic)
    cases = [case for case in _all_cases_for_selection() if case.case_id in selected_ids]
    if not cases:
        raise RuntimeError(f"case ids not found: {sorted(selected_ids)}")

    previous_results = {item.case_id: item for item in _read_summary_results()}
    previous_results.update({item.case_id: item for item in _read_casewise_results()})
    for case in cases:
        best = previous_results.get(case.case_id)
        last_error = ""
        for attempt in range(1, retries + 2):
            stdout_path = EVIDENCE_DIR / f"casewise_{case.case_id}_attempt{attempt}.stdout.txt"
            stderr_path = EVIDENCE_DIR / f"casewise_{case.case_id}_attempt{attempt}.stderr.txt"
            command = [
                sys.executable,
                "-X",
                "utf8",
                str(Path(__file__).resolve()),
                "--case-id",
                case.case_id,
                "--merge-existing",
            ]
            try:
                completed = subprocess.run(
                    command,
                    cwd=str(ROOT_DIR),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout_seconds,
                )
                stdout_path.write_text(completed.stdout or "", encoding="utf-8")
                stderr_path.write_text(completed.stderr or "", encoding="utf-8")
                current = {item.case_id: item for item in _read_summary_results()}.get(case.case_id)
                if current is not None:
                    best = current if best is None or _is_better_result(current, best) else best
                    _write_casewise_result(best)
                    if current.verdict == "pass":
                        break
                last_error = f"case_process_failed:{completed.returncode}"
                if completed.returncode != 0 and infra_backoff_seconds > 0:
                    time.sleep(infra_backoff_seconds)
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout if isinstance(exc.stdout, str) else (exc.stdout or b"").decode("utf-8", errors="replace")
                stderr = exc.stderr if isinstance(exc.stderr, str) else (exc.stderr or b"").decode("utf-8", errors="replace")
                stdout_path.write_text(stdout, encoding="utf-8")
                stderr_path.write_text(stderr, encoding="utf-8")
                last_error = f"case_process_timeout:{timeout_seconds}s"
                if infra_backoff_seconds > 0:
                    time.sleep(infra_backoff_seconds)
        if best is None:
            best = base.CaseResult(
                case_id=case.case_id,
                category=case.category,
                title=case.title,
                peer_ref=case.peer_ref,
                prompt=case.prompt,
                verdict="fail",
                score=0,
                notes=[last_error or "casewise_no_result"],
                reply_text="",
            )
            _write_casewise_result(best)
        previous_results[case.case_id] = best
        if case_pause_seconds > 0:
            time.sleep(case_pause_seconds)

    ordered = _ordered_results(previous_results)
    _write_outputs(ordered, model_verify=_read_model_verify(), cases=_all_cases_for_selection())
    return ordered


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--case-id", action="append", default=[])
    parser.add_argument("--only-problematic", action="store_true")
    parser.add_argument("--merge-existing", action="store_true")
    parser.add_argument("--casewise", action="store_true")
    parser.add_argument("--case-timeout", type=int, default=180)
    parser.add_argument("--case-retries", type=int, default=1)
    parser.add_argument("--case-pause", type=float, default=0)
    parser.add_argument("--infra-backoff", type=float, default=0)
    args = parser.parse_args()
    if args.casewise:
        results = _run_casewise(
            case_ids=set(args.case_id or []),
            only_problematic=args.only_problematic,
            timeout_seconds=args.case_timeout,
            retries=args.case_retries,
            case_pause_seconds=args.case_pause,
            infra_backoff_seconds=args.infra_backoff,
        )
    else:
        results = run_selected(
            limit=args.limit,
            case_ids=set(args.case_id or []),
            only_problematic=args.only_problematic,
            merge_existing=args.merge_existing,
        )
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
