"""LLM prompt templates the API hands to OpenClaw.

The server owns the prompt design. OpenClaw is just a forwarder: it takes the
system_prompt + per-item user_prompt from `/api/tick-payload`, sends them to
its LLM, parses the JSON response, and posts results back.

Why this lives server-side instead of in OpenClaw config: changing prompt
phrasing is a server-side change, no need to touch the scheduler.
"""

from __future__ import annotations

import json
from typing import Any

SYSTEM_PROMPT = """你是一个谨慎、克制的美股交易监控助手。

给定一条 watchlist 规则（包含用户与其他 AI 的原始投研对话）和当前实时市场快照，判断**此刻**是否应该提醒用户。

输出**严格 JSON**，且**仅输出 JSON**，结构如下：
{
  "trigger": true | false,
  "urgency": "low" | "med" | "high",
  "reason": "1-2 句中文，引用具体数据点解释判断依据",
  "action": "可执行建议：买/卖/加仓/减仓/观察 + 简短理由；不该行动写 '继续观察'"
}

判断准则：
- trigger=true 仅在规则中描述的关键事件/价格条件**已经发生或正在发生**时给出。"接近"、"有可能"、"未来几天"都不是触发条件。
- 财报类规则：财报当日及之后 24 小时内、或财报已发布且数据可见时才考虑触发。
- 价格/均线类规则：以快照中的 price、ma200、pct_from_ma200 等为准；明确穿越或确认才算。
- 数据缺失（snapshot 中相关字段为 null）时优先 trigger=false，理由说明缺失项。
- urgency=high 仅用于强信号 + 时间紧迫场景；多数情况下 low/med。

不要解释思路，不要 markdown，不要 ```json 围栏。直接返回 JSON 对象。
"""


def build_user_prompt(rule: dict[str, Any], snapshot: dict[str, Any]) -> str:
    return (
        f"规则 ID: {rule['id']}\n"
        f"标的: {rule['ticker']}\n"
        f"标题: {rule['title']}\n"
        f"冷却期(小时): {rule['cooldown_hours']}\n"
        f"动作提示: {rule.get('action_hint') or '(无)'}\n\n"
        f"--- 用户的原始 AI 投研对话 / 触发规则 ---\n"
        f"{rule['context']}\n\n"
        f"--- 当前市场快照 (UTC: {snapshot.get('fetched_at')}) ---\n"
        f"{json.dumps(snapshot, ensure_ascii=False, indent=2)}\n"
    )
