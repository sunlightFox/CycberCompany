# ClawHub MailSlurp Email Registration

## 用途
Automatically provision a real test email inbox through MailSlurp so the user can use the generated address to register and receive verification messages on other platforms.

## 何时使用
Use this when the user asks to automatically register or create a test mailbox, get a disposable but private email address, or prepare an inbox for third-party signup verification without manual webmail registration.

## 输入
`handle_id`: an Asset Broker handle for a MailSlurp API key asset authorized for `use_api_key`.
`content`: optional task label or registration purpose.
`name`: optional inbox name.
`expires_at`: optional ISO timestamp for inbox expiration if supported by the provider.

## 输出
Write a task artifact containing the generated MailSlurp inbox id and email address. The API key remains in the Asset Broker boundary and is never written to artifacts, traces, or output.

## 步骤
1. Resolve the MailSlurp API key only through the provided Asset Broker handle.
2. Call the official MailSlurp inbox creation API.
3. Save the generated inbox id and email address as a task artifact.
4. Return the artifact id and inbox summary for downstream platform registration tests.

## 禁止
Do not accept plaintext API keys in skill input, bypass provider quotas or terms, bulk-create accounts, store verification codes in plaintext artifacts, or claim that a third-party platform account has been created before its verification email is received.
