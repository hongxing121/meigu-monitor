"""SQLite storage. One file, no ORM — schema is small and queries are explicit."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "monitor.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS watchlist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker          TEXT    NOT NULL,
    title           TEXT    NOT NULL,
    context         TEXT    NOT NULL,
    action_hint     TEXT    DEFAULT '',
    cooldown_hours  INTEGER NOT NULL DEFAULT 6,
    status          TEXT    NOT NULL DEFAULT 'active',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_watchlist_status ON watchlist(status);

CREATE TABLE IF NOT EXISTS judgments (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    watchlist_id  INTEGER NOT NULL,
    triggered     INTEGER NOT NULL,
    urgency       TEXT    DEFAULT '',
    reason        TEXT    DEFAULT '',
    action        TEXT    DEFAULT '',
    snapshot      TEXT    DEFAULT '{}',
    llm_raw       TEXT    DEFAULT '',
    source        TEXT    DEFAULT 'openclaw',
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (watchlist_id) REFERENCES watchlist(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_judgments_watchlist ON judgments(watchlist_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_judgments_triggered ON judgments(triggered, created_at DESC);

CREATE TABLE IF NOT EXISTS tick_runs (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at      TEXT    NOT NULL DEFAULT (datetime('now')),
    finished_at     TEXT    DEFAULT NULL,
    total_rules     INTEGER NOT NULL DEFAULT 0,
    triggered_count INTEGER NOT NULL DEFAULT 0,
    source          TEXT    DEFAULT 'openclaw',
    note            TEXT    DEFAULT ''
);

CREATE TABLE IF NOT EXISTS memos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    title       TEXT    NOT NULL,
    note        TEXT    NOT NULL DEFAULT '',
    ticker      TEXT    NOT NULL DEFAULT '',
    remind_on   TEXT    NOT NULL DEFAULT (date('now')),
    status      TEXT    NOT NULL DEFAULT 'pending',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memos_status_due ON memos(status, remind_on);
"""


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def conn() -> Iterator[sqlite3.Connection]:
    c = _connect()
    try:
        yield c
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


# --- watchlist ---


def list_watchlist(include_archived: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM watchlist"
    if not include_archived:
        sql += " WHERE status != 'archived'"
    sql += " ORDER BY status='active' DESC, updated_at DESC"
    with conn() as c:
        rows = c.execute(sql).fetchall()
    return [dict(r) for r in rows]


def list_active_watchlist() -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM watchlist WHERE status='active' ORDER BY updated_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


def get_watchlist(item_id: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM watchlist WHERE id=?", (item_id,)).fetchone()
    return _row_to_dict(row)


def create_watchlist(
    ticker: str,
    title: str,
    context: str,
    action_hint: str = "",
    cooldown_hours: int = 6,
) -> int:
    with conn() as c:
        cur = c.execute(
            """
            INSERT INTO watchlist (ticker, title, context, action_hint, cooldown_hours)
            VALUES (?, ?, ?, ?, ?)
            """,
            (ticker.upper().strip(), title.strip(), context, action_hint, cooldown_hours),
        )
        return int(cur.lastrowid)


def update_watchlist(
    item_id: int,
    *,
    ticker: str | None = None,
    title: str | None = None,
    context: str | None = None,
    action_hint: str | None = None,
    cooldown_hours: int | None = None,
    status: str | None = None,
) -> bool:
    fields: list[str] = []
    values: list[Any] = []
    if ticker is not None:
        fields.append("ticker=?")
        values.append(ticker.upper().strip())
    if title is not None:
        fields.append("title=?")
        values.append(title.strip())
    if context is not None:
        fields.append("context=?")
        values.append(context)
    if action_hint is not None:
        fields.append("action_hint=?")
        values.append(action_hint)
    if cooldown_hours is not None:
        fields.append("cooldown_hours=?")
        values.append(cooldown_hours)
    if status is not None:
        fields.append("status=?")
        values.append(status)
    if not fields:
        return False
    fields.append("updated_at=datetime('now')")
    values.append(item_id)
    with conn() as c:
        cur = c.execute(
            f"UPDATE watchlist SET {', '.join(fields)} WHERE id=?",
            values,
        )
        return cur.rowcount > 0


def delete_watchlist(item_id: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM watchlist WHERE id=?", (item_id,))
        return cur.rowcount > 0


# --- judgments ---


def insert_judgment(
    watchlist_id: int,
    triggered: bool,
    urgency: str,
    reason: str,
    action: str,
    snapshot: dict[str, Any] | None,
    llm_raw: str = "",
    source: str = "openclaw",
) -> int:
    with conn() as c:
        cur = c.execute(
            """
            INSERT INTO judgments
                (watchlist_id, triggered, urgency, reason, action, snapshot, llm_raw, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                watchlist_id,
                1 if triggered else 0,
                urgency,
                reason,
                action,
                json.dumps(snapshot or {}, ensure_ascii=False),
                llm_raw,
                source,
            ),
        )
        return int(cur.lastrowid)


def latest_triggered_judgment(watchlist_id: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute(
            """
            SELECT * FROM judgments
            WHERE watchlist_id=? AND triggered=1
            ORDER BY created_at DESC LIMIT 1
            """,
            (watchlist_id,),
        ).fetchone()
    return _row_to_dict(row)


def latest_judgment(watchlist_id: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM judgments WHERE watchlist_id=? ORDER BY created_at DESC LIMIT 1",
            (watchlist_id,),
        ).fetchone()
    return _row_to_dict(row)


def list_recent_judgments(limit: int = 50, only_triggered: bool = False) -> list[dict[str, Any]]:
    sql = """
        SELECT j.*, w.ticker, w.title
        FROM judgments j
        JOIN watchlist w ON w.id = j.watchlist_id
    """
    if only_triggered:
        sql += " WHERE j.triggered = 1"
    sql += " ORDER BY j.created_at DESC LIMIT ?"
    with conn() as c:
        rows = c.execute(sql, (limit,)).fetchall()
    return [dict(r) for r in rows]


def list_judgments_for(watchlist_id: int, limit: int = 20) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            """
            SELECT * FROM judgments WHERE watchlist_id=?
            ORDER BY created_at DESC LIMIT ?
            """,
            (watchlist_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def in_cooldown(watchlist_id: int, cooldown_hours: int) -> bool:
    """True if the most recent triggered judgment is within the cooldown window."""
    last = latest_triggered_judgment(watchlist_id)
    if last is None:
        return False
    last_ts = datetime.fromisoformat(last["created_at"]).replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - last_ts < timedelta(hours=cooldown_hours)


# --- tick_runs ---


def start_tick_run(source: str = "openclaw", note: str = "") -> int:
    with conn() as c:
        cur = c.execute(
            "INSERT INTO tick_runs (source, note) VALUES (?, ?)",
            (source, note),
        )
        return int(cur.lastrowid)


def finish_tick_run(run_id: int, total_rules: int, triggered_count: int) -> None:
    with conn() as c:
        c.execute(
            """
            UPDATE tick_runs
               SET finished_at=datetime('now'),
                   total_rules=?,
                   triggered_count=?
             WHERE id=?
            """,
            (total_rules, triggered_count, run_id),
        )


def latest_tick_run() -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute(
            "SELECT * FROM tick_runs ORDER BY started_at DESC LIMIT 1"
        ).fetchone()
    return _row_to_dict(row)


# --- memos ---


def list_memos(
    *,
    filter_kind: str = "active",  # active | today | upcoming | done | all
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM memos"
    where: list[str] = []
    params: list[Any] = []
    if filter_kind == "active":
        where.append("status = 'pending'")
    elif filter_kind == "today":
        where.append("status = 'pending' AND remind_on <= date('now')")
    elif filter_kind == "upcoming":
        where.append("status = 'pending' AND remind_on > date('now')")
    elif filter_kind == "done":
        where.append("status = 'done'")
    elif filter_kind == "all":
        pass
    else:
        raise ValueError(f"unknown filter_kind: {filter_kind}")
    if where:
        sql += " WHERE " + " AND ".join(where)
    # Earlier remind_on first within active/today (overdue surfaces top); newest first within done.
    if filter_kind in ("active", "today", "upcoming"):
        sql += " ORDER BY remind_on ASC, created_at DESC"
    else:
        sql += " ORDER BY updated_at DESC"
    with conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def get_memo(memo_id: int) -> dict[str, Any] | None:
    with conn() as c:
        row = c.execute("SELECT * FROM memos WHERE id=?", (memo_id,)).fetchone()
    return _row_to_dict(row)


def create_memo(
    title: str,
    note: str = "",
    ticker: str = "",
    remind_on: str | None = None,
) -> int:
    with conn() as c:
        if remind_on:
            cur = c.execute(
                "INSERT INTO memos (title, note, ticker, remind_on) VALUES (?, ?, ?, ?)",
                (title.strip(), note, ticker.upper().strip(), remind_on),
            )
        else:
            cur = c.execute(
                "INSERT INTO memos (title, note, ticker) VALUES (?, ?, ?)",
                (title.strip(), note, ticker.upper().strip()),
            )
        return int(cur.lastrowid)


def update_memo(
    memo_id: int,
    *,
    title: str | None = None,
    note: str | None = None,
    ticker: str | None = None,
    remind_on: str | None = None,
    status: str | None = None,
) -> bool:
    fields: list[str] = []
    values: list[Any] = []
    if title is not None:
        fields.append("title=?")
        values.append(title.strip())
    if note is not None:
        fields.append("note=?")
        values.append(note)
    if ticker is not None:
        fields.append("ticker=?")
        values.append(ticker.upper().strip())
    if remind_on is not None:
        fields.append("remind_on=?")
        values.append(remind_on)
    if status is not None:
        fields.append("status=?")
        values.append(status)
    if not fields:
        return False
    fields.append("updated_at=datetime('now')")
    values.append(memo_id)
    with conn() as c:
        cur = c.execute(
            f"UPDATE memos SET {', '.join(fields)} WHERE id=?",
            values,
        )
        return cur.rowcount > 0


def delete_memo(memo_id: int) -> bool:
    with conn() as c:
        cur = c.execute("DELETE FROM memos WHERE id=?", (memo_id,))
        return cur.rowcount > 0
