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
CLAUDE_TIMEOUT: int = 30
CLAUDE_MAX_TOKENS: int = 2048

# ── Database ──────────────────────────────────────────────────────────────────
import os as _os
_BASE_DIR = _os.path.dirname(_os.path.abspath(__file__))
DATABASE_PATH: str = _os.path.join(_BASE_DIR, "trade_bot.db")

# ── Market Data ───────────────────────────────────────────────────────────────
TWELVE_DATA_API_KEY: str = os.getenv("TWELVE_DATA_API_KEY", "")
BINANCE_PRICE_URL: str = "https://api.binance.com/api/v3/ticker/price"
TWELVE_DATA_URL: str = "https://api.twelvedata.com/price"

# ── Trading defaults ──────────────────────────────────────────────────────────
DEFAULT_RISK: str = "moderate"

# ── Validation ────────────────────────────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN:
    raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set.")
if not ANTHROPIC_API_KEY:
    raise EnvironmentError("ANTHROPIC_API_KEY is not set.")
