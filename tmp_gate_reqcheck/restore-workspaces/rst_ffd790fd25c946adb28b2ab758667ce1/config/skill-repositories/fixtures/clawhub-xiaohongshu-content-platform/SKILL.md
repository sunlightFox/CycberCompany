# Xiaohongshu Content Platform

## 用途
为第 99 阶段的小红书内容发布任务生成投放草稿并写入任务工件。

## 何时使用
当用户需要将标题、正文、标签和首条评论组装为小红书发布草稿时使用。

## 输入
`task_id`、`title`、`body`、`tags`、`comment_text`、`publish_surface`、`platform_key`、`selected_asset_id` 和 `media_artifact_ids`。

## 输出
在 `outputs/xiaohongshu-content-platform.md` 生成 Markdown 草稿工件，包含标题、正文、标签、媒体引用和首条评论。

## 步骤
1. 根据输入组装发布草稿。
2. 使用 `file.write` 将草稿写入任务工件。

## 禁止
不得读取未授权资产，不得直接发布到外部平台，不得执行未声明的工具。
