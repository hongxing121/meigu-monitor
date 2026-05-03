# OpenClaw Skill: meigu-monitor 操作（user-triggered）

This skill exposes **all user-triggered operations** on meigu-monitor. The
cron-driven tick lives in `scheduled-prompt.md` and stays separate (different
invocation mode — system schedule vs. user message).

Server base URL: `http://localhost:8765`

## Capabilities at a glance

| 类别 | 操作 | 端点 |
|---|---|---|
| Memos | 查询今天/逾期 | `GET /api/memos/today` |
| Memos | 创建 | `POST /api/memos` |
| Memos | 标记完成 | `PUT /api/memos/{id}` `{status:"done"}` |
| Memos | 删除 | `DELETE /api/memos/{id}` |
| Memos | 查询全部活跃/即将到 | `GET /api/memos?filter=active|upcoming` |
| Watchlist | 查询当前规则 | `GET /api/watchlist` |
| Watchlist | 新增规则 | `POST /api/watchlist` |
| Watchlist | 暂停/激活/归档 | `PUT /api/watchlist/{id}` `{status:...}` |
| Watchlist | 删除 | `DELETE /api/watchlist/{id}` |
| Pick | 综合推荐"今天买什么" | `GET /api/watchlist` + per-ticker `GET /api/snapshot/{t}` + your LLM |
| Snapshot | 单只标的实时数据 | `GET /api/snapshot/{ticker}` |

## Trigger phrases → intent

Don't pattern-match rigidly. Use intent. Examples below help calibrate.

### Memo intents

- **查今天的备忘**: 今天有什么投资备忘 / 今天有备忘吗 / 投资备忘 / /memo / /memos
- **创建备忘**: 提醒我 X / 记一下 X / 备忘一下 X / X 别忘了 / /memo add X
- **完成备忘**: X 已经做了 / 标记 X 完成 / 把 X 备忘标记完成 / /memo done X
- **删除备忘**: 删了 X 那条备忘 / X 备忘不要了 / /memo delete X
- **查所有备忘**: 所有备忘 / 即将到的备忘 / 这周备忘

### Watchlist intents

- **查当前监控**: 现在监控了哪些 / 看下 watchlist / 监控列表 / /watchlist
- **新增监控**: 把 X 加到监控 / 帮我盯 X / 监控一下 X / /watch X
- **暂停/归档**: 把 X 暂停 / 暂停监控 X / 归档 X / archive X
- **激活**: 重新启用 X / 把 X 激活回来

### Pick intent

- **/pick 类查询**: 今天买什么 / 现在该买啥 / 推荐一只 / 选哪个 / 买什么好 / /pick

### Ambiguous → ask back

如果消息既像备忘又像监控（"提醒我盯一下 NVDA 财报"），直接问用户：

> 这个想做成"一次性提醒"还是"持续监控"？
> - 选 1：备忘（盯到日期就提醒一次，删了就完了）
> - 选 2：watchlist（每个 tick 评估一次，触发条件命中才推送）

不要默认选其中一个。多问一句比错记好。

---

## 详细动作规范

### A. 查今天的备忘

```
GET http://localhost:8765/api/memos/today
```

响应：

```json
{
  "today": "2026-05-04",
  "count": 2,
  "items": [
    {"id": 3, "title": "买 ADBE 浅 ITM Jan 2027 call",
     "note": "...", "ticker": "ADBE", "remind_on": "2026-05-04",
     "status": "pending", "is_overdue": false}
  ],
  "summary_for_user": "今天 2026-05-04 有 2 条投资备忘待处理"
}
```

回复格式（Telegram-friendly）：

**count == 0:**
> 今天 {today} 没有待处理的投资备忘。

**count > 0:**
> 今天 {today} 有 {count} 条投资备忘：
>
> 1. **{ticker if any}** {title}
>    {note 截前 2 行，超过加 …}
>    *{remind_on}*{ "（逾期）" if is_overdue}
> 2. ...
>
> 处理：在 dashboard 标完成或删除 → http://localhost:8765/

不要加额外评论 / 分析。备忘内容已含用户预设动作。

---

### B. 创建备忘 (write path)

**输入解析（用户消息 → memo 字段）：**

| 字段 | 来源 | 备注 |
|---|---|---|
| `title` | 第一句或核心动作短语，≤ 50 字 | 应能"动作可执行" |
| `note` | 用户消息原文 | 整段保留，含上下文 |
| `ticker` | 自动检测大写 2-5 字母 token，对照常见 ticker | 找不到留空 |
| `remind_on` | 解析日期表达 (见下表) | 默认今天 |

**日期解析表**（用今天 = `T`，星期数 0=Mon..6=Sun）：

| 用户表达 | 解析为 |
|---|---|
| 今天 / 现在 / 当下 | T |
| 明天 / 明早 / 明晚 | T+1 |
| 后天 | T+2 |
| 大后天 | T+3 |
| 这周五 / 周五 (T 是 Mon-Thu) | 本周五 |
| 周五 (T 是 Fri-Sun) | 下周五 |
| 下周一 / 下星期一 | T 之后的下一个周一（如 T 已是周一，就是 T+7） |
| 下周末 | T 之后的下一个周六 |
| 5月12 / 5/12 / 五月十二 | 2026-05-12（已过则 2027-05-12）|
| 12号 | 当月 12 日（已过则下个月 12 日） |
| 三天后 / 3 天后 | T+3 |
| 一周后 | T+7 |
| 财报当天 + ticker 提供 | 调 `GET /api/snapshot/{ticker}`，取 `next_earnings_date` |

如果日期完全没出现 → `remind_on = today`，但回复时**显式确认**："默认今天提醒，需要改时间告诉我"。

**Ticker 提取**：
- 2-5 个连续大写字母（NVDA、ADBE、META、COHR）
- 排除常见英文单词（"AI"、"CEO"、"USD"、"RSI"、"NEW"、"OK"）
- 找不到留空，不强求

**API 调用**：

```
POST http://localhost:8765/api/memos
Body:
{
  "title": "<parsed>",
  "note": "<原文>",
  "ticker": "<extracted or '' >",
  "remind_on": "<YYYY-MM-DD>"
}
```

**回复**：

> 已记入备忘 #{id}：
> **{title}**
> {ticker if any} · 提醒日期 {remind_on}
> 改日期或删除：dashboard → Memos

如果用户后续说"改成下周三"——按 PUT 走 (见 G)。

---

### C. 标记完成

1. `GET /api/memos?filter=active` 拿全部 active 备忘
2. fuzzy match 用户描述到一条 (按 title 包含、或 ticker 匹配)
3. 多条匹配时 → 反问"哪一条？"列出候选
4. `PUT /api/memos/{id}` `{"status": "done"}`
5. 回复：`已标记完成: {title}`

---

### D. 删除备忘

同 C 流程，最后改成：
`DELETE /api/memos/{id}`
回复：`已删除: {title}`

如果用户说"全删了" / "清空备忘"——**反问确认**，不要默默全删。

---

### E. 查 watchlist

```
GET http://localhost:8765/api/watchlist
```

回复：列表，每条一行：

> **{ticker}** · {title}
> 状态: {status} · 最近判断: {last_judgment.urgency or '无'} ({last_judgment.created_at[:10] if exists})

按 active 在前，archived/paused 折叠（除非用户说"看全部"）。

---

### F. 新增 watchlist

watchlist 比 memo 字段多，**需要交互式收集**。如果用户消息只说"把 NVDA 加到监控"，按以下顺序追问（一次问完）：

> 好，要把 NVDA 加到监控。我需要确认几件事：
> 1. **监控主题/触发条件**（一句话）：例如"等财报后看 RSI 是否回到 50 以上"
> 2. **目标动作**（可选）：例如"信号触发买 1/3 仓现货"
> 3. **冷却时间**（小时，默认 12）：触发后多久内不再重复提醒？

收齐后 POST：

```
POST http://localhost:8765/api/watchlist
Body:
{
  "ticker": "NVDA",
  "title": "<用户给的主题>",
  "context": "<用户给的完整描述 + 任何相关上下文>",
  "action_hint": "<目标动作 or ''>",
  "cooldown_hours": <number, default 12>
}
```

**特殊情况**：用户消息**直接包含完整论点**（明显粘贴自其他 AI 的投研对话），不要追问，直接：

```
title: 从前两行提取
context: 整段用户消息
action_hint: ''  (留空，让用户后续补)
cooldown_hours: 12
```

回复：

> 已加入 watchlist #{id}：**{ticker}** {title}
> 触发后 {cooldown_hours}h 内不会重复提醒。
> 下次定时 tick 时（{下次预估时间}）会自动评估。
> 修改：dashboard → Watchlist 找 {ticker}

---

### G. 暂停 / 激活 / 归档 / 删除 watchlist

1. `GET /api/watchlist`，按 ticker fuzzy match
2. 多个匹配 → 反问
3. `PUT /api/watchlist/{id}` `{"status": "paused"|"active"|"archived"}` 或 `DELETE`

回复：`{ticker} 已 {status}` / `{ticker} 已删除`

---

### H. /pick 综合推荐

1. `GET /api/watchlist` 拿所有 active 规则（含 last_judgment）
2. 对每条 ticker，并发 `GET /api/snapshot/{ticker}` 拿最新数据
3. 把 (规则 context + 当前 snapshot + last_judgment) 整批喂给你的 LLM

**Pick prompt（system + user，建议）：**

System:
```
你是用户的私人投资助手。下面是用户当前完整的 watchlist 和实时市场数据。

回答："现在最值得行动的标的是哪一两只？哪些应观望？"

要求：
- 分两组回复：
  1. 「短期信号已触发 (Path A)」：列出今天有 Path A 触发条件命中的标的
  2. 「LEAPS 仍在可建底窗口 (Path B)」：列出当前满足入选 5/5 但无 Path A 触发的标的
- 每只配 1-2 句理由，引用具体数据点（价格、距 MA、财报日期等）
- 标注风险/前置条件
- 中文，简洁，可执行
- 仅回 1-3 只，不是整张名单
```

User: 拼接 watchlist + 每只的 snapshot + last_judgment 的 JSON。

LLM 回的内容直接转给用户。

---

## 通用规则

1. **每个动作以确认信息收尾**——用户应该总能知道发生了什么
2. **拿不准的不默默猜**：日期歧义、ticker 含糊、操作影响多条 → 反问
3. **不要重复读**：用户问"今天备忘"不需要先 GET watchlist
4. **保持响应速度**：纯 read 操作 < 1s 反馈，写操作 < 2s
5. **失败要说清**：API 返 4xx/5xx 时把 error 字段贴给用户，不要静默
6. **不主动加分析**：除了 /pick，其他动作只做用户要的事，不"顺便建议"
7. **不重复确认无害操作**：单条 memo 删除直接做，不"你确定吗"。批量删除/清空、归档 watchlist 这种**多步可恢复**的才确认

## 失败模式 & 解决

| 症状 | 原因 | 处理 |
|---|---|---|
| `connection refused` | server 没起 | 告诉用户："服务器没在跑，是不是要 ssh 重启 uvicorn?" |
| 写操作 422 unprocessable | payload 字段缺 | 检查 title / cooldown_hours 类型；按 schema 重试 |
| `/api/memos/today` 返 200 但 count 长期 0 | 用户都不记备忘 | 不是错误，正常返回"今天没有备忘" |
| watchlist tick 没在跑 | scheduled-prompt 没配 / 网关断 | 不归本 skill 管，但可提醒用户检查 cron |
