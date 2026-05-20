# OpenClaw Xiaohongshu Publish Skill

## 用途
为外部平台发布任务生成可审阅草稿，并输出结构化浏览器 workflow spec。

## 何时使用
当任务需要把标题、正文、图片上传、发布确认和首评流程整理成可执行浏览器步骤时使用。

## 输入
`title`、`body`、`comment_text`、`media_artifact_ids`，以及登录页、发布页、评论页 URL 和 selector 覆盖。

## 输出
在 `outputs/` 目录生成一份 Markdown 草稿和一份 `.workflow.json` 结构化流程工件。

## 步骤
1. 生成人类可读的小红书发布草稿。
2. 生成结构化 workflow spec，描述登录、发布、发布后身份捕获、评论和复检策略。

## 禁止
不得直接发布到外部平台，不得读取未授权资产，不得执行未声明工具。
