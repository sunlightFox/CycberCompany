# ClawHub GitHub PR Workflow

## 用途
为 GitHub 代码托管协作生成受控的远程执行摘要和任务工件。

## 何时使用
用户要求读取 GitHub 状态、发起 PR、请求 review、同步分支或整理 release 信息时使用。

## 输入
code_hosting_request_type、remote_repo_ref、base_branch、target_branch、pr_ref、review_action、release_kind。

## 输出
Markdown 结果工件。

## 步骤
调用声明的文件工具写入任务工件。

## 禁止
不得读取或回显任何敏感凭据。
