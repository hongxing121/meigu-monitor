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
