# ClawHub Word Report

## 用途
生成真实 Word docx 办公文档，例如项目周报、会议报告、复盘报告和管理层摘要。

## 何时使用
用户明确要求生成 Word、docx、文档、报告、周报或项目周报时使用。

## 输入
goal：用户目标。content：报告素材、项目背景、进展、风险和下一步计划。

## 输出
任务 artifact 中的 `.docx` Word 文档。

## 步骤
1. 将输入整理为标题、摘要、章节、要点和表格。
2. 调用 `office.word.generate` 生成真实 docx artifact。
3. 只把文件写入任务 artifact 目录。

## 禁止
不得调用终端命令，不得读取任意本地路径，不得外发文档内容。
