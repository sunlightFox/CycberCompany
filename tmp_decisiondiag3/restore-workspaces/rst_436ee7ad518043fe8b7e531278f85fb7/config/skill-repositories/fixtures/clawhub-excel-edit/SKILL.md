# ClawHub Excel Edit

## 用途
编辑任务 artifact 中已有的 Excel xlsx 文件，生成新的编辑后工作簿。

## 何时使用
用户要求修改 Excel、追加行、新增 sheet、修正单元格或继续完善表格时使用。

## 输入
source_artifact_id：同一任务中的源 Excel artifact。content：新增或修改说明。

## 输出
新的 `.xlsx` Excel artifact，原文件不被覆盖。

## 步骤
1. 校验源 artifact 属于当前任务。
2. 调用 `office.excel.edit` 追加行、新增 sheet 或设置单元格。
3. 输出新版本 xlsx artifact。

## 禁止
不得覆盖原文件，不得跨任务读取 artifact，不得调用终端命令。
