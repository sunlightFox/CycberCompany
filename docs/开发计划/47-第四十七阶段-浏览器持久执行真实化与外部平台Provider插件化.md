# 第四十七阶段 - 浏览器持久执行真实化与外部平台 Provider 插件化

## 阶段背景

第三十七阶段完成了浏览器 profile/session/evidence 的资产化契约，第四十二阶段完成了外部平台动作编排和 fake provider E2E。但当前浏览器交互更多是“记录交互证据”，外部平台 target 也包含生产 service 内的 `fake_platform` seed。要进入真实可用阶段，需要把浏览器执行器和 provider 插件化，去掉测试目标对核心服务的污染。

## 核心目标

```text
浏览器 profile 能映射到受控 Playwright context
browser.open/snapshot/click/fill/submit/screenshot/download 具备真实执行路径
登录态加载、撤销、过期和清理由 BrowserSessionService 管理
外部平台 provider 从核心 service 拆成 provider registry
fake provider 只作为测试/fixture provider，不是产品默认特例
外部平台计划可选择 browser executor 或 provider executor
```

## 阶段原则

1. 浏览器登录态是敏感资产，不进入模型上下文。
2. Playwright context 必须受 profile policy、URL safety、download quarantine 管住。
3. Provider 是插件或配置，不在核心编排里写平台 if/else。
4. fake provider 只服务测试和本地验收。
5. 发布、提交、上传、账号修改必须审批。
6. 所有真实网页内容默认 untrusted。
7. 不新增 UI。

## 本阶段必须完成

```text
BrowserExecutor interface
PlaywrightBrowserExecutor 本地实现
profile -> browser context 生命周期管理
session_handle -> context storage state 加载，不暴露 cookie 明文
真实 click/fill/type/submit/screenshot/download 执行和 evidence 记录
ExternalPlatformProvider interface
FakeExternalPlatformProvider 迁到 tests fixture 或 provider module
provider registry 支持从配置/Skill/MCP 注册 provider
external platform plan executor 可按 execution_mode 选择 browser/provider
```

## 本阶段不做

```text
不接管用户真实日常 Chrome profile
不绕过验证码、风控、付费墙或网站条款
不接入真实生产平台密钥
不自动发布外部内容
不新增浏览器 UI 或桌面窗口
```

## 小阶段总览

| 小阶段 | 名称 | 核心交付 |
|---:|---|---|
| 47.1 | BrowserExecutor 契约 | executor interface、输入输出和 evidence schema |
| 47.2 | Playwright context 管理 | profile/session/storage state/撤销清理 |
| 47.3 | 真实浏览器工具执行 | open/snapshot/fill/click/submit/screenshot/download |
| 47.4 | Evidence 强化 | network/console/download/screenshot/redirect chain |
| 47.5 | Provider registry | 外部平台 provider interface 与注册表 |
| 47.6 | fake provider 隔离 | fake 平台迁出核心产品路径 |
| 47.7 | External platform executor | plan step 选择 browser 或 provider 执行 |
| 47.8 | 安全和回归 | URL safety、审批、DLP、trace、replay |

## 验收标准

```text
browser.fill/click/submit 对本地 fixture 页面有真实 DOM 操作证据
browser.screenshot 生成真实截图 artifact
session handle resolve 不返回 cookie 明文
profile revoke 后 Playwright context 和 handle 均失效
external platform fake provider 不再硬编码在核心编排 service
多 provider 可通过 registry 查询和执行
发布类 step 仍需要 approval
trace、audit、diagnostic 无 secret 泄漏
```

## 文件影响范围

| 模块 | 文件范围 |
|---|---|
| Browser | `browser_sessions.py`、`tools.py`、新增 `browser_executor.py` |
| External Platform | `external_platform_actions.py`、新增 provider registry |
| Config | `config/providers.yaml` 或后续 provider 配置 |
| Tests | `apps/local-api/tests/test_phase47_browser_provider_execution.py` |

## 与后续阶段关系

第四十八阶段会把 Skill、通知和 checkpoint 的治理闭环统一到真实执行路径上。第四十七阶段先把“真实浏览器执行”和“外部平台 provider 可替换”做稳。

