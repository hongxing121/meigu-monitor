# OpenClaw Skill: 投资备忘查询

A lightweight on-demand skill: when the user asks about today's investment
memos via Telegram (or anywhere OpenClaw listens), pull from
`/api/memos/today` and reply.

## Trigger phrases

Match any of:

- "今天有什么投资备忘"
- "今天有备忘吗"
- "查一下投资备忘"
- "有什么 memo"
- "今天要做什么"
- "/memo"
- "/memos"
- "投资备忘"

## Behavior

1. `GET http://localhost:8765/api/memos/today`
2. Response shape:
   ```json
   {
     "today": "2026-05-04",
     "count": 2,
     "items": [
       {
         "id": 3,
         "title": "买 ADBE 浅 ITM Jan 2027 call",
         "note": "财报前一周建仓；strike 230，delta ~0.68；仓位 3%",
         "ticker": "ADBE",
         "remind_on": "2026-05-04",
         "status": "pending",
         "is_overdue": false,
         "created_at": "2026-05-03 09:14:22",
         "updated_at": "2026-05-03 09:14:22"
       },
       ...
     ],
     "summary_for_user": "今天 2026-05-04 有 2 条投资备忘待处理"
   }
   ```
3. Format the reply for Telegram:

   **If `count == 0`**:
   > 今天 {today} 没有待处理的投资备忘。

   **If `count > 0`**:
   > 今天 {today} 有 {count} 条投资备忘：
   >
   > 1. **[ticker if any]** {title}
   >    {note (first 2 lines, truncate with … if longer)}
   >    *{remind_on}{ "（逾期）" if is_overdue}*
   >
   > 2. ...
   >
   > 处理完直接在 dashboard 上点"完成"或"删除"：http://localhost:8765/

4. Don't add commentary or analysis. Just relay. The memos themselves
   already contain the user's intended action.

## Optional: 创建备忘 (write path)

If you want to support creating memos from Telegram too:

- Trigger: "记一下：xxx" / "提醒我 xxx" / "/memo add xxx"
- Parse: title (everything after the trigger), optional date hints ("下周一", "5/12", "周五"), optional ticker (if uppercase 2-5 letter token)
- POST to `/api/memos`:
  ```json
  {
    "title": "<parsed title>",
    "note": "<original message text>",
    "ticker": "<extracted or empty>",
    "remind_on": "<YYYY-MM-DD parsed from date hint, or today if missing>"
  }
  ```
- Reply: "已记入备忘 #{id}，{remind_on} 提醒你。"

This write-path is optional — the simplest first version is just the
read-only "今天有什么备忘" query. Build it up if useful.
