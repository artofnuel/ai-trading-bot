"""
db/database.py — Async SQLite database layer using aiosqlite.
No schema changes from v1. Supports all v2 fields via raw_json storage.
"""

import json
import logging
from typing import TYPE_CHECKING, Optional

import aiosqlite

from config import DATABASE_PATH

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id     INTEGER PRIMARY KEY,
    username        TEXT,
    default_balance REAL,
    default_risk    TEXT DEFAULT 'moderate',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id      INTEGER,
    pair             TEXT,
    direction        TEXT,
    entry            TEXT,
    stop_loss        TEXT,
    risk_amount      TEXT,
    confluence_score INTEGER,
    raw_json         TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);
"""


async def init_db(app: Optional["Application"] = None) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(CREATE_USERS_TABLE)
        await db.execute(CREATE_TRADES_TABLE)
        await db.commit()
    logger.info("Database initialised at %s", DATABASE_PATH)


async def upsert_user(
    telegram_id: int,
    username: Optional[str],
    default_balance: Optional[float] = None,
) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        if default_balance is not None:
            await db.execute(
                """
                INSERT INTO users (telegram_id, username, default_balance)
                VALUES (?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    default_balance = excluded.default_balance
                """,
                (telegram_id, username, default_balance),
            )
        else:
            await db.execute(
                """
                INSERT INTO users (telegram_id, username)
                VALUES (?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET username = excluded.username
                """,
                (telegram_id, username),
            )
        await db.commit()


async def get_user(telegram_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def set_user_balance(telegram_id: int, balance: float) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, default_balance)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET default_balance = excluded.default_balance
            """,
            (telegram_id, balance),
        )
        await db.commit()


async def set_user_risk(telegram_id: int, risk: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO users (telegram_id, default_risk)
            VALUES (?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET default_risk = excluded.default_risk
            """,
            (telegram_id, risk),
        )
        await db.commit()


async def log_trade(telegram_id: int, plan: dict) -> None:
    clean_plan = {k: v for k, v in plan.items() if not k.startswith("_")}
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """
            INSERT INTO trades
                (telegram_id, pair, direction, entry, stop_loss, risk_amount, confluence_score, raw_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                plan.get("pair"),
                plan.get("direction"),
                plan.get("entry"),
                plan.get("stop_loss"),
                plan.get("risk_amount"),
                plan.get("confluence_score"),
                json.dumps(clean_plan),
            ),
        )
        await db.commit()


async def get_trade_history(telegram_id: int, limit: int = 5) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT * FROM trades
            WHERE telegram_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (telegram_id, limit),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]
