"""
config.py — Centralised configuration loader.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Anthropic / Claude ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = "claude-sonnet-4-6"
CLAUDE_TIMEOUT: int = 50        # enriched prompt needs time
CLAUDE_MAX_TOKENS: int = 900       # compact output; NO_TRADE fits in ~120 tokens

# ── Database ──────────────────────────────────────────────────────────────────
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH: str = os.path.join(BASE_DIR, "trade_bot.db")

# ── Market Data ───────────────────────────────────────────────────────────────
TWELVE_DATA_API_KEY: str = os.getenv("TWELVE_DATA_API_KEY", "")
TWELVE_DATA_BASE: str = "https://api.twelvedata.com"

# ── Trading defaults ──────────────────────────────────────────────────────────
DEFAULT_RISK: str = "moderate"

# ── Validation ────────────────────────────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN:
    raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set.")
if not ANTHROPIC_API_KEY:
    raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
if not TWELVE_DATA_API_KEY:
    raise EnvironmentError("TWELVE_DATA_API_KEY is not set.")
