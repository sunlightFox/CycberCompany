# ClawHub PPT Briefing

## 用途
生成真实 PowerPoint pptx 演示稿，用于项目汇报、路演、复盘和会议简报。

## 何时使用
用户要求生成 PPT、pptx、PowerPoint、演示稿、汇报材料或 briefing 时使用。

## 输入
goal：用户目标。content：汇报背景、受众、核心结论、数据和下一步。

## 输出
任务 artifact 中的 `.pptx` PowerPoint 演示稿。

## 步骤
1. 将输入整理成标题页、分章节 slide、要点和备注。
2. 调用 `office.ppt.generate` 生成真实 pptx artifact。
3. 保持每页要点简洁清晰。

## 禁止
不得调用终端命令，不得外发材料，不得声称生成了未写入 artifact 的文件。
