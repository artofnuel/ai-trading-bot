"""
main.py — Entry point for the AI Trade Planner Telegram Bot.

Initialises the database, builds the Application, registers all handlers,
and starts polling.
"""

import asyncio
import logging

from telegram.ext import Application

from config import TELEGRAM_BOT_TOKEN
from db.database import init_db
from bot.handlers import register_handlers

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    level=logging.INFO,
)
# Suppress verbose third-party logs
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

logger = logging.getLogger(__name__)


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    """Initialise the DB and start the bot."""
    logger.info("Starting AI Trade Planner Bot…")

    # Ensure SQLite tables exist (run synchronously before the bot loop starts)
    asyncio.run(init_db())

    # Build the Telegram Application
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    # Attach all command / message handlers
    register_handlers(app)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # run_polling() manages its own event loop — do NOT await it
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
