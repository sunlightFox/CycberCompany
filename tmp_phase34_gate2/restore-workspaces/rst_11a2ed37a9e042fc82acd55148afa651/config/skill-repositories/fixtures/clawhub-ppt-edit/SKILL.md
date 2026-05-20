# ClawHub PPT Edit

## 用途
编辑任务 artifact 中已有的 PPT pptx 文件，生成新的编辑后演示稿。

## 何时使用
用户要求修改 PPT、追加页面、替换文本或继续完善演示稿时使用。

## 输入
source_artifact_id：同一任务中的源 PPT artifact。content：新增页面或替换说明。

## 输出
新的 `.pptx` PowerPoint artifact，原文件不被覆盖。

## 步骤
1. 校验源 artifact 属于当前任务。
2. 调用 `office.ppt.edit` 追加页面或替换文本。
3. 输出新版本 pptx artifact。

## 禁止
不得覆盖原文件，不得跨任务读取 artifact，不得调用终端命令。
