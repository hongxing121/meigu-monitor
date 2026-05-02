"""Pydantic request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class WatchlistCreate(BaseModel):
    ticker: str = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=200)
    context: str = Field(min_length=1)
    action_hint: str = ""
    cooldown_hours: int = Field(default=6, ge=0, le=168)


class WatchlistUpdate(BaseModel):
    ticker: str | None = None
    title: str | None = None
    context: str | None = None
    action_hint: str | None = None
    cooldown_hours: int | None = Field(default=None, ge=0, le=168)
    status: Literal["active", "paused", "archived"] | None = None


class JudgmentResult(BaseModel):
    """One LLM verdict for one watchlist item, posted back by OpenClaw."""

    watchlist_id: int
    triggered: bool
    urgency: Literal["low", "med", "high"] = "low"
    reason: str = ""
    action: str = ""
    snapshot: dict[str, Any] | None = None
    llm_raw: str = ""


class TickResultPost(BaseModel):
    tick_run_id: int | None = None
    source: str = "openclaw"
    results: list[JudgmentResult]
