# ClawHub Word Edit

## 用途
编辑任务 artifact 中已有的 Word docx 文件，生成新的编辑后文档。

## 何时使用
用户要求修改 Word、追加章节、替换文字、增加风险说明或继续完善文档时使用。

## 输入
source_artifact_id：同一任务中的源 Word artifact。content：要追加或替换的内容。

## 输出
新的 `.docx` Word artifact，原文件不被覆盖。

## 步骤
1. 校验源 artifact 属于当前任务。
2. 调用 `office.word.edit` 追加章节、替换文字或添加表格。
3. 输出新版本 docx artifact。

## 禁止
不得覆盖原文件，不得跨任务读取 artifact，不得调用终端命令。
