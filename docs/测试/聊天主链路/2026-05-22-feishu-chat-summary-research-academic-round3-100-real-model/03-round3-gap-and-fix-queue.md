# 闲聊与知识类真实模型缺口与修复队列

- 非 pass 场景：31
- 修复原则：优先修通用可见回复质量、自然语气、知识结构和边界表达，不做 case-by-case 硬编码。

## 缺口聚类

- `missing_expected_terms`：25
- `reply_too_short_or_thin`：11
- `missing_clear_structure_or_usefulness`：4
- `missing_evidence_awareness`：4
- `real_model_not_completed`：2
- `turn_status`：1

## 明细

- `FCSR3-001` 闲聊陪伴/午后低电量 warn/92：missing_expected_terms:下午
- `FCSR3-003` 闲聊陪伴/开会前紧张 warn/84：missing_expected_terms:五分钟,开会
- `FCSR3-005` 闲聊陪伴/想逃避消息 warn/75：reply_too_short_or_thin
- `FCSR3-006` 闲聊陪伴/空白晚上 fail/67：reply_too_short_or_thin, missing_expected_terms:选择
- `FCSR3-009` 闲聊陪伴/想被接住 fail/67：reply_too_short_or_thin, missing_expected_terms:人
- `FCSR3-011` 自然沟通/改口不尴尬 warn/92：missing_expected_terms:群
- `FCSR3-012` 自然沟通/拒绝借钱 fail/67：reply_too_short_or_thin, missing_expected_terms:借钱
- `FCSR3-014` 自然沟通/对方误会 warn/84：missing_expected_terms:误会,解释
- `FCSR3-015` 自然沟通/催反馈 warn/84：missing_expected_terms:反馈,催
- `FCSR3-018` 自然沟通/伴侣道歉 warn/84：missing_expected_terms:伴侣,道歉
- `FCSR3-023` 归纳整理/网页学习归纳 warn/84：missing_expected_terms:Finding,Evidence
- `FCSR3-031` 总结压缩/向上汇报 warn/84：missing_expected_terms:进展,周五
- `FCSR3-032` 总结压缩/保留边界 fail/59：reply_too_short_or_thin, missing_expected_terms:小样本,可能
- `FCSR3-033` 总结压缩/邮件短化 fail/63：reply_too_short_or_thin, missing_clear_structure_or_usefulness
- `FCSR3-035` 总结压缩/只留判断 warn/75：reply_too_short_or_thin
- `FCSR3-036` 总结压缩/纪要压缩 warn/92：missing_expected_terms:理由
- `FCSR3-043` 研究框架/网页论文提取 warn/88：missing_evidence_awareness
- `FCSR3-047` 研究框架/样本限制 warn/92：missing_expected_terms:不夸大
- `FCSR3-048` 研究框架/资料过期 warn/72：missing_expected_terms:一年前,补证, missing_clear_structure_or_usefulness
- `FCSR3-058` 学术解释/文献缺口 warn/92：missing_expected_terms:文献缺口
- `FCSR3-061` 知识问答/长期记忆边界 fail/7：reply_too_short_or_thin, real_model_not_completed, missing_expected_terms:长期记忆
- `FCSR3-066` 知识问答/引用质量 warn/84：missing_expected_terms:可信,线索
- `FCSR3-073` 学习辅导/英语复述 warn/84：missing_expected_terms:英语,累
- `FCSR3-074` 学习辅导/学习复盘 fail/59：reply_too_short_or_thin, missing_expected_terms:两小时,复盘
- `FCSR3-081` 观点讨论/陪伴边界 fail/47：reply_too_short_or_thin, missing_expected_terms:反对,判断, missing_clear_structure_or_usefulness
- `FCSR3-091` 事实核查/热搜断言 fail/0：reply_too_short_or_thin, real_model_not_completed, turn_status:failed, missing_expected_terms:50,截图, missing_clear_structure_or_usefulness, missing_evidence_awareness
- `FCSR3-093` 事实核查/来源冲突 warn/84：missing_expected_terms:Forum,Verification
- `FCSR3-094` 事实核查/医疗传言 warn/88：missing_evidence_awareness
- `FCSR3-097` 事实核查/法律边界 warn/92：missing_expected_terms:违法
- `FCSR3-098` 事实核查/旧资料 warn/88：missing_evidence_awareness
- `FCSR3-100` 事实核查/测试验收 warn/92：missing_expected_terms:重跑