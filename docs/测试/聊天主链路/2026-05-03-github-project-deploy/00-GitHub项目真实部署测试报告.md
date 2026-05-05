# GitHub 项目真实部署测试报告

- 测试时间: 2026-05-03T09:38:57.215177+00:00 -> 2026-05-03T09:39:08.504696+00:00
- 结果: passed
- GitHub 源: `https://github.com/mdn/beginner-html-site-styled.git`
- Deployment: `dep_3a86c349050041a9b12694c4e5cc7bc6`
- Approval: `apr_32c60a07b9f84952b7d73ce8ebe5d89e`
- Endpoint: `http://127.0.0.1:5588`
- Backend: `wsl`，degraded_isolation=False
- Managed process: `mpr_81519791ad6648e8ad7002f02dafcf9f` / status=`running` / pid=`None`
- Port lease(run snapshot): `port_ef5ee0d5874846e9b99c08e37715e293` / port=`5588` / status=`active`
- Port lease(final): `port_ef5ee0d5874846e9b99c08e37715e293` / status=`released`
- Stop status: `stopped`
- 备注: 当前选择器返回 `wsl`，但预览进程日志显示实际启动命令为本地 Python `http.server`。本次静态项目真实部署通过；若需要严格 WSL/容器隔离，后续应补齐对应 executor，或在未接入 executor 前降级标注为 `local_workspace`。

## 阶段耗时

| 阶段 | 状态 | 客户端墙钟 ms | 服务端 trace ms | trace_id |
|---|---:|---:|---:|---|
| API 健康检查 | ok | 14.81 | 15 | trc_d5caba2473f44b22b499fd032dff787a |
| 创建部署计划 | waiting_approval | 136.1 | 127 | trc_754bcccd141b498694bc5904fc2c7008 |
| 未审批执行门禁 | waiting_approval | 18.46 | 15 | trc_0d2820e7994b4684bbb95be699c12ced |
| 读取待审批记录 | pending | 12.71 | 5 | trc_9e2e8c8c14854992833d95de09ad82da |
| 审批部署计划 | completed | 180.03 | 161 | trc_99076a9ffaaa496fad108157beb2a84b |
| 执行部署并健康检查 | healthy | 5631.82 | 5611 | trc_b82762978e6146b4bebd3a22165290b9 |
| 访问预览页 | ok | 2.89 |  |  |
| 读取部署日志 | completed | 20.93 | 8 | trc_47e6eb89a21f47c58ff8804f0349bfc0 |
| 停止预览进程 | stopped | 255.25 | 233 | trc_e3f71f8c5bb3452882871508e243b12b |
| 读取停止后详情 | stopped | 39.49 | 0 | trc_ac1a4f655dc143828371aef6f059d025 |
| 停止后端口探测 | stopped | 2000.69 |  |  |

## 预览页验证

- HTTP ok: `True`
- HTTP status: `200`
- Content-Type: `text/html`

```html
<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <title>My test page</title>
    <link href="http://fonts.googleapis.com/css?family=Open+Sans" rel="stylesheet" type="text/css">
    <link href="styles/style.css" rel="stylesheet" type="text/css">
  </head>
  <body>
    <h1>Mozilla is cool</h1>
    <img src="images/firefox-icon.png" alt="The Firefox logo: a flaming fox surrounding the Earth.">

    <p>At Mozilla, we’re a global community of</p>

    <ul> <!-- c
```

## 部署日志预览

```text
clone=completed
stack=static backend=wsl
process_pid=13348
process_command=[REDACTED_LOCAL_PATH] -m http.server 5588 --bind 127.0.0.1
process_endpoint=http://127.0.0.1:5588
install_deps=skipped_static
build=skipped
run=process_started endpoint=http://127.0.0.1:5588
health_check=passed status=200
```
