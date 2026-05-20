# ClawHub Excel Analysis Workbook

## 用途
生成真实 Excel xlsx 工作簿，用于经营数据、指标分析、销售统计和汇报表格。

## 何时使用
用户要求生成 Excel、xlsx、表格、数据分析表、经营数据表或销售表时使用。

## 输入
goal：用户目标。content：数据背景、字段说明、业务问题和分析口径。

## 输出
任务 artifact 中的 `.xlsx` Excel 工作簿。

## 步骤
1. 将输入整理成 sheet、表头、数据行和摘要。
2. 调用 `office.excel.generate` 生成真实 xlsx artifact。
3. 保留摘要 sheet、基础公式和可读列宽。

## 禁止
不得凭空伪造为真实数据，不得外发数据，不得调用终端命令。
