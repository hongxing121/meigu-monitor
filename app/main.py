"""FastAPI app: watchlist CRUD, indicator snapshots, tick orchestration, dashboard."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import db, fetcher
from .models import JudgmentResult, TickResultPost, WatchlistCreate, WatchlistUpdate
from .prompts import SYSTEM_PROMPT, build_user_prompt

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("meigu")

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title="meigu monitor", version="0.1.0")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


@app.on_event("startup")
def _startup() -> None:
    db.init_db()
    log.info("db ready at %s", db.DB_PATH)


# --- dashboard ---


@app.get("/", response_class=HTMLResponse)
def dashboard(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "index.html")


# --- watchlist CRUD ---


@app.get("/api/watchlist")
def api_list_watchlist(include_archived: bool = False) -> dict[str, Any]:
    items = db.list_watchlist(include_archived=include_archived)
    enriched = []
    for it in items:
        last = db.latest_judgment(it["id"])
        enriched.append({**it, "last_judgment": last})
    return {"items": enriched}


@app.post("/api/watchlist")
def api_create_watchlist(payload: WatchlistCreate) -> dict[str, Any]:
    new_id = db.create_watchlist(
        ticker=payload.ticker,
        title=payload.title,
        context=payload.context,
        action_hint=payload.action_hint,
        cooldown_hours=payload.cooldown_hours,
    )
    return {"id": new_id, "item": db.get_watchlist(new_id)}


@app.get("/api/watchlist/{item_id}")
def api_get_watchlist(item_id: int) -> dict[str, Any]:
    item = db.get_watchlist(item_id)
    if item is None:
        raise HTTPException(404, "watchlist item not found")
    judgments = db.list_judgments_for(item_id, limit=20)
    return {"item": item, "judgments": judgments}


@app.put("/api/watchlist/{item_id}")
def api_update_watchlist(item_id: int, payload: WatchlistUpdate) -> dict[str, Any]:
    if db.get_watchlist(item_id) is None:
        raise HTTPException(404, "watchlist item not found")
    db.update_watchlist(
        item_id,
        ticker=payload.ticker,
        title=payload.title,
        context=payload.context,
        action_hint=payload.action_hint,
        cooldown_hours=payload.cooldown_hours,
        status=payload.status,
    )
    return {"item": db.get_watchlist(item_id)}


@app.delete("/api/watchlist/{item_id}")
def api_delete_watchlist(item_id: int) -> dict[str, Any]:
    if not db.delete_watchlist(item_id):
        raise HTTPException(404, "watchlist item not found")
    return {"ok": True}


# --- snapshots & tick ---


@app.get("/api/snapshot/{ticker}")
def api_snapshot(ticker: str, refresh: bool = False) -> dict[str, Any]:
    return fetcher.fetch_snapshot(ticker, use_cache=not refresh)


@app.get("/api/tick-payload")
def api_tick_payload(request: Request, skip_cooldown: bool = False) -> dict[str, Any]:
    """Called by OpenClaw at every scheduled tick.

    Returns everything the LLM needs to judge each active rule, plus the
    URL to POST results back to. OpenClaw's job is purely:
      1) loop items
      2) call its LLM with system_prompt + item.user_prompt
      3) parse JSON response
      4) POST aggregated results to result_post_url
      5) send the user a message for any item with triggered=true
    """
    rules = db.list_active_watchlist()
    run_id = db.start_tick_run(source="openclaw", note="payload requested")

    items = []
    for r in rules:
        cooling = db.in_cooldown(r["id"], r["cooldown_hours"])
        if cooling and not skip_cooldown:
            # Skip judgment but surface in payload so OpenClaw sees it.
            items.append(
                {
                    "watchlist_id": r["id"],
                    "ticker": r["ticker"],
                    "title": r["title"],
                    "skip": True,
                    "skip_reason": f"in cooldown ({r['cooldown_hours']}h)",
                }
            )
            continue

        snap = fetcher.fetch_snapshot(r["ticker"])
        items.append(
            {
                "watchlist_id": r["id"],
                "ticker": r["ticker"],
                "title": r["title"],
                "skip": False,
                "snapshot": snap,
                "user_prompt": build_user_prompt(r, snap),
                "expected_response_schema": {
                    "trigger": "bool",
                    "urgency": "'low' | 'med' | 'high'",
                    "reason": "string (1-2 sentences in Chinese)",
                    "action": "string (action recommendation in Chinese)",
                },
            }
        )

    base = str(request.base_url).rstrip("/")
    return {
        "tick_run_id": run_id,
        "system_prompt": SYSTEM_PROMPT,
        "items": items,
        "result_post_url": f"{base}/api/tick-result",
        "instructions": (
            "For each item where skip=false, send {system_prompt, user_prompt} "
            "to your LLM and expect a JSON response matching expected_response_schema. "
            "Collect all responses and POST to result_post_url as "
            "{tick_run_id, results: [...]}. Items where skip=true should be "
            "passed through as-is (no LLM call) but still included in the result post "
            "with triggered=false and reason='cooldown'."
        ),
    }


@app.post("/api/tick-result")
def api_tick_result(payload: TickResultPost) -> dict[str, Any]:
    triggered_count = 0
    inserted_ids: list[int] = []
    triggered_items: list[dict[str, Any]] = []

    for r in payload.results:
        if db.get_watchlist(r.watchlist_id) is None:
            log.warning("skipping result for unknown watchlist_id=%s", r.watchlist_id)
            continue
        jid = db.insert_judgment(
            watchlist_id=r.watchlist_id,
            triggered=r.triggered,
            urgency=r.urgency,
            reason=r.reason,
            action=r.action,
            snapshot=r.snapshot,
            llm_raw=r.llm_raw,
            source=payload.source,
        )
        inserted_ids.append(jid)
        if r.triggered:
            triggered_count += 1
            wl = db.get_watchlist(r.watchlist_id)
            assert wl is not None
            triggered_items.append(
                {
                    "watchlist_id": r.watchlist_id,
                    "ticker": wl["ticker"],
                    "title": wl["title"],
                    "urgency": r.urgency,
                    "reason": r.reason,
                    "action": r.action,
                }
            )

    if payload.tick_run_id is not None:
        db.finish_tick_run(payload.tick_run_id, len(payload.results), triggered_count)

    return {
        "ok": True,
        "inserted_judgment_ids": inserted_ids,
        "triggered_count": triggered_count,
        "triggered": triggered_items,  # OpenClaw forwards these to user
    }


# --- read-only views for dashboard ---


@app.get("/api/judgments")
def api_judgments(limit: int = 50, only_triggered: bool = False) -> dict[str, Any]:
    return {
        "items": db.list_recent_judgments(limit=limit, only_triggered=only_triggered),
    }


@app.get("/api/status")
def api_status() -> dict[str, Any]:
    last_run = db.latest_tick_run()
    active = db.list_active_watchlist()
    triggered = db.list_recent_judgments(limit=200, only_triggered=True)
    # Recent (last 24h) triggers, computed in SQL for accuracy:
    from datetime import datetime, timedelta, timezone

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    triggered_24h = [
        t for t in triggered
        if datetime.fromisoformat(t["created_at"]).replace(tzinfo=timezone.utc) >= cutoff
    ]
    return {
        "last_tick_run": last_run,
        "active_count": len(active),
        "triggered_24h": len(triggered_24h),
    }


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"error": str(exc)})
