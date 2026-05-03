# meigu monitor

A local web app that watches your US stock watchlist and lets a remote LLM (via
**OpenClaw**) decide when to alert you.

```
                ┌─────────────────────────────────────────┐
                │           OpenClaw (other Mac)          │
                │  ┌──────────┐         ┌──────────────┐  │
                │  │   cron   │ ──────► │   built-in   │  │
                │  │ schedule │  judge  │     LLM      │  │
                │  └────┬─────┘         └──────────────┘  │
                │       │ HTTP                            │
                └───────┼─────────────────────────────────┘
                        ▼                       ▲ POST results
              GET /api/tick-payload             │
                        │                       │
                ┌───────┴───────────────────────┴────────┐
                │           meigu monitor                │
                │  • Watchlist CRUD (web UI)             │
                │  • yfinance indicators                 │
                │  • SQLite store                        │
                │  • Dashboard at /                      │
                └────────────────────────────────────────┘
```

This program does **not** call any LLM itself. OpenClaw is the brain; this app is
the data + storage + UI layer.

## Quick start (on the Mac that will host the server)

```sh
cd /path/to/meigu
python3 -m venv .venv
source .venv/bin/activate
pip install -e .

# run the server (listens on 0.0.0.0 so OpenClaw on another machine can reach it)
python -m uvicorn app.main:app --host 0.0.0.0 --port 8765
```

Open http://localhost:8765/ — empty dashboard. Click **+ New rule**, paste the AI
investment chat into Context, save.

The server stores everything in `./data/monitor.db` (SQLite, no migrations to run).

### Run as a launchd service (optional)

Save this to `~/Library/LaunchAgents/com.meigu.monitor.plist`, swapping the paths:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>           <string>com.meigu.monitor</string>
    <key>WorkingDirectory</key><string>/Users/YOU/path/to/meigu</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOU/path/to/meigu/.venv/bin/python</string>
        <string>-m</string><string>uvicorn</string>
        <string>app.main:app</string>
        <string>--host</string><string>0.0.0.0</string>
        <string>--port</string><string>8765</string>
    </array>
    <key>RunAtLoad</key>       <true/>
    <key>KeepAlive</key>       <true/>
    <key>StandardOutPath</key> <string>/tmp/meigu.out.log</string>
    <key>StandardErrorPath</key><string>/tmp/meigu.err.log</string>
</dict>
</plist>
```

Then `launchctl load ~/Library/LaunchAgents/com.meigu.monitor.plist`.

## Wiring OpenClaw

Two integrations live in `openclaw/`, separated by **invocation mode**:

- [`scheduled-prompt.md`](openclaw/scheduled-prompt.md) — **system-triggered** cron
  tick that runs the watchlist judgment loop. Server hands OpenClaw the system +
  user prompts so prompt tuning stays server-side.
- [`meigu-skill.md`](openclaw/meigu-skill.md) — **user-triggered** interactive
  skill. Covers all on-demand operations: memo CRUD, watchlist CRUD, snapshot
  lookups, and the `/pick` "今天买什么" synthesis. Single dispatch point for
  any user message addressed to OpenClaw about meigu-monitor.

The cron is the active watcher; the interactive skill is the conversational
front door.

## Daily usage cheat sheet

Three surfaces, one system:

- **Cron tick** runs on its own. Pushes a Telegram message only when something
  in your watchlist actually triggers. Silent the rest of the time.
- **Telegram + OpenClaw** for talking to it on the go. Natural language,
  no commands to remember.
- **Dashboard at `http://localhost:8765/`** for managing things in bulk
  or when you want to see the whole picture.

### Via Telegram (OpenClaw)

| Want to... | Say something like | What happens |
| --- | --- | --- |
| Stash a future action | `提醒我下周一买 ADBE 浅 ITM call` | Parses date + ticker → POST `/api/memos` → "已记入备忘 #N，2026-05-11 提醒你" |
| Same with paste | `5月12号看 NVDA 财报后量能：vol_ratio ≥ 1.5 才动手` | Title + note + remind_on auto-extracted |
| See today's queue | `今天有什么投资备忘` | List of pending + overdue, with ticker badges |
| Mark done | `ADBE 那条备忘做完了` | Fuzzy match → PUT `status: done` |
| Delete | `删了 NVDA 那条备忘` | Fuzzy match → DELETE |
| Add a watch rule | Paste the AI investment chat + `把这个加到 watchlist` | Auto-detect "粘贴的论点"，title 取第一行，整段进 context |
| New rule, blank slate | `把 PYPL 加到监控` | Asks back: 主题 / 动作 / 冷却时间，一次问完 |
| See current watchlist | `现在监控了哪些` | Active rules + 最近一次 judgment urgency |
| Pause / archive | `把 NOW 暂停` / `归档 MELI` | PUT `status: paused/archived` |
| Get a "what to buy" read | `今天买什么` / `/pick` | LLM 综合所有 watchlist + 实时数据，分两组回复："已触发 Path A" + "LEAPS 仍在窗口" |
| Snapshot one ticker | `查下 ADBE 现在数据` | GET `/api/snapshot/ADBE`，返关键字段表格 |

**OpenClaw will never push you unsolicited from this skill** — only the cron
tick does that. Conversation is pull-only.

### Via dashboard

Open `http://localhost:8765/` and:

- **Watchlist tab** — every active rule, expandable to show full context +
  recent judgments. `+ New rule` for ad-hoc additions. Pause/edit/delete inline.
- **Memos tab** — three sections: 今天/已逾期 (amber, top of mind), 即将到,
  已完成 (collapsed). The header badge counts items requiring your attention today.
- **Activity tab** — every judgment ever made, filterable to triggers only.
  Useful for "did the model warn me about this last week?" 复盘.

### What the cron tick does (no action needed from you)

Configured in `openclaw/scheduled-prompt.md`. Every scheduled tick:

1. OpenClaw GETs `/api/tick-payload` — server returns each active rule's
   current snapshot + the ready-to-use system + user prompt.
2. OpenClaw forwards prompts to its own LLM, gets back JSON judgments.
3. POSTs results to `/api/tick-result`. Server stores them, applies cooldown
   for triggered rules.
4. Server's response includes a `triggered: [...]` list — OpenClaw turns each
   into a Telegram message:

   > 🔔 [HIGH] COHR — 5/6 财报
   > 财报临近(5/7)，RSI 59.84，距 200MA +84%。
   > 建议：关注 5/7 财报当晚的利润率指引；若超预期再考虑加仓
   > 详情：http://localhost:8765/

   Empty list → silent. No "everything's fine" pings.

## API

| Method | Path                          | Purpose                                  |
| ---    | ---                           | ---                                      |
| GET    | `/`                           | Dashboard                                |
| GET    | `/api/watchlist`              | List rules                               |
| POST   | `/api/watchlist`              | Create rule                              |
| GET    | `/api/watchlist/{id}`         | Rule detail + recent judgments           |
| PUT    | `/api/watchlist/{id}`         | Update / pause / archive                 |
| DELETE | `/api/watchlist/{id}`         | Delete                                   |
| GET    | `/api/snapshot/{ticker}`      | Live indicators (yfinance)               |
| GET    | `/api/tick-payload`           | **OpenClaw entrypoint** — full job spec  |
| POST   | `/api/tick-result`            | OpenClaw posts judgments back here       |
| GET    | `/api/judgments`              | Recent judgments (for dashboard)         |
| GET    | `/api/status`                 | Last tick + counts (incl. memos_today)   |
| GET    | `/api/memos`                  | List memos (filter=active/today/upcoming/done/all) |
| POST   | `/api/memos`                  | Create memo                              |
| GET    | `/api/memos/today`            | OpenClaw entrypoint — today + overdue    |
| GET    | `/api/memos/{id}`             | One memo                                 |
| PUT    | `/api/memos/{id}`             | Update / mark done / dismiss             |
| DELETE | `/api/memos/{id}`             | Delete                                   |

## Indicators captured per snapshot

`price`, `prev_close`, `day_change_pct`, `ma20/50/200`, `pct_from_ma20/50/200`,
`high_52w`, `low_52w`, `pct_from_52w_high/low`, `volume`, `avg_volume_30d`,
`volume_ratio`, `next_earnings_date`, `last_earnings_date`,
`last_earnings_eps_actual / estimate`, `last_earnings_surprise_pct`, `rsi14`.

Anything yfinance can't supply (e.g. earnings calendar gaps for some tickers)
becomes `null` — the LLM is told to fall back to `triggered=false` when the
relevant field is missing.

## Cooldown

Each watchlist rule has a `cooldown_hours` (default 6). After a triggered
judgment, the rule is skipped from the next tick payloads until the window
elapses, so you don't get re-paged for the same event.

## Notes / gotchas

- **yfinance is unofficial.** It scrapes Yahoo and occasionally breaks. Per-indicator
  errors are caught and reported in `snapshot.errors`; one bad field doesn't kill
  the snapshot.
- **No auth.** Run on a trusted LAN, or front it with `tailscale serve` / a reverse
  proxy if exposing.
- **Single user.** SQLite, no multi-tenant concerns. WAL mode is enabled.
- **TZ.** All timestamps stored as UTC ISO strings. Dashboard renders relative.
