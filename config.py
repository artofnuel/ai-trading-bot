"""
config.py — Centralised configuration loader.

Reads environment variables from .env and exposes them as constants.
"""

import os
from dotenv import load_dotenv

# Absolute path to the directory containing this file (project root)
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))

# Load .env file from the project root
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Anthropic / Claude ────────────────────────────────────────────────────────
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL: str = "claude-3-5-sonnet-20241022"

# ── Claude request settings ───────────────────────────────────────────────────
CLAUDE_TIMEOUT: int = 30          # seconds before we give up waiting for Claude
CLAUDE_MAX_TOKENS: int = 2048     # generous but bounded output

# ── Database ──────────────────────────────────────────────────────────────────
DATABASE_PATH: str = os.path.join(BASE_DIR, "trade_bot.db")

# ── Trading defaults ──────────────────────────────────────────────────────────
DEFAULT_RISK: str = "moderate"    # conservative | moderate | aggressive

# ── Validation ────────────────────────────────────────────────────────────────
if not TELEGRAM_BOT_TOKEN:
    raise EnvironmentError("TELEGRAM_BOT_TOKEN is not set in the environment.")
if not ANTHROPIC_API_KEY:
    raise EnvironmentError("ANTHROPIC_API_KEY is not set in the environment.")
