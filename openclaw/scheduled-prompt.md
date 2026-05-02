# OpenClaw Scheduled Job Prompt

Paste this into your OpenClaw scheduled-job config. The cron tick fires this prompt against
OpenClaw's built-in LLM. The LLM does the work end-to-end via HTTP: pulls the payload,
judges each rule, posts results back, then messages you on triggers.

## Config

| Field          | Value |
|---             |---    |
| Schedule       | `0,30 21-23 * * 1-5` (Beijing time, US market hours weekdays) — adjust as needed |
| Server URL     | `http://<your-mac-host>:8765` (LAN IP of the Mac running this server) |
| Notify channel | Whatever OpenClaw uses to message you (Lark / Telegram / etc.) |

## Prompt

```
You are an automated stock-watch executor. Do exactly the following, no more.

1. GET {{SERVER_URL}}/api/tick-payload
   The response has shape:
     {
       "tick_run_id": <int>,
       "system_prompt": "<string>",
       "items": [
         {
           "watchlist_id": <int>,
           "ticker": "<string>",
           "title": "<string>",
           "skip": <bool>,
           "skip_reason": "<string, when skip=true>",
           "snapshot": {...},                  // when skip=false
           "user_prompt": "<string>",          // when skip=false
           "expected_response_schema": {...}   // when skip=false
         }, ...
       ],
       "result_post_url": "<string>"
     }

2. For each item where skip == false:
     Call your LLM with messages:
       system: payload.system_prompt
       user:   item.user_prompt
     The LLM MUST return strict JSON shaped:
       { "trigger": bool, "urgency": "low"|"med"|"high", "reason": "...", "action": "..." }
     Parse it. If parsing fails, fall back to:
       { "trigger": false, "urgency": "low", "reason": "LLM parse error: <err>", "action": "" }

   For each item where skip == true:
     Treat as { "trigger": false, "urgency": "low", "reason": item.skip_reason, "action": "" }

3. POST {{SERVER_URL}}/api/tick-result
   Body:
     {
       "tick_run_id": <from step 1>,
       "source": "openclaw",
       "results": [
         {
           "watchlist_id": <int>,
           "triggered": <bool>,
           "urgency": "<string>",
           "reason": "<string>",
           "action": "<string>",
           "snapshot": <item.snapshot or null>,
           "llm_raw": "<the raw LLM response text, for debugging>"
         }, ...
       ]
     }
   The response includes "triggered": [...] — items the user should be notified about.

4. For every item in the response's "triggered" array, send the user a message:
     [<urgency upper>] <ticker> — <title>
     <reason>
     建议: <action>
     详情: {{SERVER_URL}}/

   If "triggered" is empty, do NOT send any message. Stay silent.

5. Done. Do not retry. Do not loop. Do not chain follow-up calls.
```

## Manual test (without scheduling)

You can hit the endpoints directly with `curl` to verify the server side before wiring
OpenClaw:

```sh
# 1. Pull payload
curl -s http://localhost:8765/api/tick-payload | jq

# 2. Fake a triggered judgment for watchlist_id=1
curl -s -X POST http://localhost:8765/api/tick-result \
  -H 'Content-Type: application/json' \
  -d '{
    "tick_run_id": 1,
    "results": [
      {
        "watchlist_id": 1,
        "triggered": true,
        "urgency": "high",
        "reason": "测试触发",
        "action": "什么也别做，这是测试"
      }
    ]
  }'

# 3. Refresh dashboard at http://localhost:8765/ — the judgment should be visible.
```
