# claude-relay-check

检测一个 Claude / Claude Code 中转（API relay、反代）背后是不是 Anthropic 官方 API。一个 Python 文件，只用标准库，跑一次花几美分。

[English](#english) · [原理详解](https://corecode.live/docs/verify/) · [中转站怎么选](https://corecode.live/docs/choose/)

```sh
curl -fsSLO https://raw.githubusercontent.com/CoreCodeAI/claude-relay-check/main/check-relay.py
python3 check-relay.py --base-url https://中转地址 --key sk-你的密钥 --model claude-sonnet-5-5
```

```text
https://中转地址/v1/messages  model=claude-sonnet-5-5
[PASS] shape     Messages 格式正确，返回模型 claude-sonnet-5-5，输入 143 token
[PASS] cache     首次写入 18165 token，第 2 次读取 18165 token
[PASS] signature 篡改签名被拒（HTTP 400），原样回传通过
结论：三项都像 Anthropic 官方
```

## 为什么不能问模型“你是谁”

系统提示里写一句“你是 Claude Opus”，任何模型都会这么回答；真的 Claude 也可能说错自己的版本号。问身份、问知识截止日期、看语气，都只能当参考。

这个脚本看的是只有 Anthropic 官方 API 才有、转换层仿造不了的三种行为：

| 检测 | 做法 | 说明什么 |
|---|---|---|
| `shape` | 发一条普通请求，检查 `type`、`id` 前缀 `msg_`、`usage` 字段 | 是 Anthropic Messages 格式。只能筛掉最粗糙的转换，是后两项的前提 |
| `cache` | 发一段约 18,000 token、带 `cache_control` 的系统提示两次 | 第一次写入、第二次读回同样多的 token，说明提示缓存真实可用，用量不是中转自己算的。写入了却读不到，通常是请求每次落在不同账号上 |
| `signature` | 拿到带签名的思考块和工具调用，把签名改一个字符传回去，再原样传回去 | Anthropic 用签名验证思考块由 Claude 生成，被篡改的签名[一律返回 400](https://platform.claude.com/docs/en/build-with-claude/preserved-thinking)。改过的签名也能通过，就说明没人在校验，上游不是 Anthropic |

## 用法

| 参数 | 默认值 | 说明 |
|---|---|---|
| `--base-url` | `ANTHROPIC_BASE_URL` | 中转地址，脚本请求 `<base-url>/v1/messages` |
| `--key` | `ANTHROPIC_AUTH_TOKEN`，其次 `ANTHROPIC_API_KEY` | 密钥只发给 `--base-url`，不连接任何其他服务 |
| `--model` | `claude-sonnet-5-5` | 要检测的模型 |
| `--bearer` | 关 | 用 `Authorization: Bearer` 代替 `x-api-key` |
| `--only` | 全部 | 只跑某一项：`shape`、`cache`、`signature`，可重复 |

不填 `--base-url` 和 `--key` 时，读的就是 Claude Code 正在用的环境变量。输出语言跟随 `LANG`，`zh` 开头时显示中文。

| 结果 | 含义 | 退出码 |
|---|---|---|
| `PASS` | 符合 Anthropic 官方 API 的行为 | 0 |
| `SKIP` | 模型不接受思考参数，或这次自己决定不思考；再跑一次或换模型 | 0 |
| `WARN` | 有疑点但不能定性，比如缓存写入了却读不到 | 0 |
| `FAIL` | 上游不是 Anthropic 官方，或用量不是真实的 | 1 |
| `ERROR` | 请求本身失败（连不上、密钥无效、余额不足、客户端被拒），不说明真假 | 2 |

有的中转只放行 Claude Code 客户端，脚本的请求会被 403 拒绝，结果是 `ERROR`。这是客户端限制，和上游真假无关。

## 它证明不了什么

- **型号。** 它证明上游是 Anthropic，不证明答你的是你选的那一档：Sonnet 生成的思考块同样有合法签名。型号要结合返回的 `model` 字段、账单逐条记录的模型和单价来判断，或者用 [ModelTrace](https://github.com/xqy2006/ModelTrace) 这类指纹工具。
- **每一次请求。** 一次检测是一次抽样，中转可以只在部分时段换模型。隔一段时间再跑。
- **稳定性。** 成功率和延迟要看服务的历史记录。

## 开发

```sh
python3 test_check_relay.py
```

测试在本地起一个假上游，覆盖真上游通过、签名不校验、没有签名、没有缓存、缓存落在不同账号、非 Anthropic 格式和 401 这几种情况。

这个脚本由 [CoreCode](https://corecode.live) 维护，同一份文件也发布在 `https://corecode.live/docs/verify/check-relay.py`。它对任何中转都适用，包括 CoreCode 自己。

---

## English

Check whether a Claude or Claude Code relay (API proxy) is backed by Anthropic's own API. One Python file, standard library only, a few cents per run.

```sh
curl -fsSLO https://raw.githubusercontent.com/CoreCodeAI/claude-relay-check/main/check-relay.py
python3 check-relay.py --base-url https://relay.example --key sk-... --model claude-sonnet-5-5
```

Asking a model what it is proves nothing: one line of system prompt makes any model say it is Claude. The script checks three behaviors a translating proxy cannot easily fake:

- **shape**: the response is an Anthropic Messages response with real `usage` fields.
- **cache**: a ~18,000-token prefix with `cache_control` is written once and read back on the next call. No write and no read means no Anthropic prompt caching, or usage the proxy computes itself.
- **signature**: Anthropic signs thinking blocks and returns 400 for a tampered signature sent back. The script flips one character and expects a 400, then sends the genuine one and expects a 200.

Exit status is 0 when nothing failed, 1 when a check failed, and 2 when a request error stopped a check, which says nothing about the upstream. It proves the upstream is Anthropic, not which model tier answered. The full explanation is at [corecode.live/docs/en/verify](https://corecode.live/docs/en/verify/).

## License

MIT
