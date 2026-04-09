"""
config.py — Centralised configuration loader.

Reads environment variables from .env and exposes them as constants.
"""

import os
from dotenv import load_dotenv

# Load .env file from the project root
load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Anthropic / Claude ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = "claude-sonnet-4-5-20251001"

# ── Claude request settings ───────────────────────────────────────────────────
CLAUDE_TIMEOUT: int = 30          # seconds before we give up waiting for Claude
CLAUDE_MAX_TOKENS: int = 2048     # generous but bounded output

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = "trade_bot.db"

# ── Trading defaults ──────────────────────────────────────────────────────────
DEFAULT_RISK: str = "moderate"    # conservative | moderate | aggressive

# ── Validation ────────────────────────────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN:
    raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set in the environment.")
if not ANTHROPIC_API_KEY:
    raise EnvironmentError("ANTHROPIC_API_KEY is not set in the environment.")
