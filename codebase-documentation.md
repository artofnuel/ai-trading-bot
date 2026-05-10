# AI Trading Bot — Full Codebase Documentation

> Generated on 2026-05-10

## Project Overview

An AI-powered Forex & Crypto trade planning Telegram bot. Built with Python, python-telegram-bot v21, Anthropic Claude, and SQLite. The bot analyses markets in real-time and returns structured trade plans with entry, stop loss, take profit levels, trailing stop guidance, and risk-adjusted sizing.

**Architecture:** Telegram Bot → Live Price Fetcher → Claude AI Analyst → Formatted Trade Plan → Persisted to SQLite

---

## Project Structure

```
ai-trading-bot/
├── config.py                # Centralised configuration loader
├── main.py                  # Entry point
├── README.md                # Project documentation
├── requirements.txt         # Python dependencies
├── tradingbot.service       # systemd service unit for VPS deployment
├── ai/
│   ├── __init__.py
│   └── analyst.py           # Claude API integration & prompt logic
├── bot/
│   ├── __init__.py
│   ├── handlers.py          # All Telegram command & conversation handlers
│   ├── keyboards.py         # Inline keyboard definitions
│   └── formatter.py         # Trade plan → Telegram message renderer
├── db/
│   ├── __init__.py
│   └── database.py          # Async SQLite layer (aiosqlite)
└── market/
    ├── __init__.py
    └── prices.py             # Live market price fetcher (Binance + Twelve Data)
```

---

## File: `config.py`

Centralised configuration loader. Reads environment variables via `python-dotenv`.

```python
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
CLAUDE_MAX_TOKENS: int = 1024  # JSON response rarely exceeds 800 tokens

# ── Database ──────────────────────────────────────────────────────────────────
BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH: str = os.path.join(BASE_DIR, "trade_bot.db")

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
```

---

## File: `main.py`

Entry point — initialises the database, builds the Telegram Application, registers handlers, and starts polling.

```python
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

    # Build the Telegram Application
    # post_init(init_db) ensures the DB is set up INSIDE the bot's event loop
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).post_init(init_db).build()

    # Attach all command / message handlers
    register_handlers(app)

    logger.info("Bot is running. Press Ctrl+C to stop.")

    # run_polling() manages its own event loop — do NOT await it
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
```

---

## File: `requirements.txt`

```text
python-telegram-bot==21.6
anthropic==0.40.0
aiosqlite==0.20.0
python-dotenv==1.0.1
httpx==0.27.0
```

---

## File: `tradingbot.service`

Systemd service unit for deploying the bot on a Linux VPS.

```ini
[Unit]
Description=AI Trade Planner Telegram Bot
After=network.target

[Service]
Type=simple
# Replace with your actual VPS username
User=your_vps_username
WorkingDirectory=/home/your_vps_username/trade-bot
EnvironmentFile=/home/your_vps_username/trade-bot/.env
ExecStart=/home/your_vps_username/trade-bot/venv/bin/python main.py
Restart=always
RestartSec=10
# Capture stdout/stderr to journald
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

---

## File: `ai/__init__.py`

```python
# ai/__init__.py
```

---

## File: `ai/analyst.py`

Claude AI integration for generating trade plans. Fetches live market prices before calling Claude, validates the JSON response, and supports both swing and scalp trade styles.

```python
"""
ai/analyst.py — Claude AI integration for generating trade plans.

Fetches live market prices before calling Claude so all levels
are anchored to real current market conditions.

Trade styles supported:
  - swing  : multi-session trades, larger TP targets
  - scalp  : intra-session trades, tight SL/TP, quick R:R
"""

import asyncio
import json
import logging
import re
from typing import Optional

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL, CLAUDE_MAX_TOKENS, CLAUDE_TIMEOUT
from market.prices import fetch_live_price, fetch_all_prices

logger = logging.getLogger(__name__)


# ── Custom exception ──────────────────────────────────────────────────────────

class AnalystError(Exception):
    """Raised when the AI analyst cannot produce a valid trade plan."""


# ── System prompt — lean and non-redundant ────────────────────────────────────

SYSTEM_PROMPT = """\
You are an elite Forex and Crypto trade analyst specialising in Smart Money Concepts \
(SMC), ICT methodology, and disciplined risk management.

Return STRICT JSON only — no markdown, no explanation outside the JSON object.

EXECUTION TYPE — derive from live price vs entry:
  BUY:  live > entry → Buy Limit | live < entry → Buy Stop | live = entry → Buy Market
  SELL: live < entry → Sell Limit | live > entry → Sell Stop | live = entry → Sell Market

LOT SIZE — use EXACTLY what the trader specifies. Do not change it.
  Pip values per standard lot: USD pairs $10/pip | JPY pairs ~$8/pip | XAU/USD $10/pip per 0.1 lot
  Crypto PnL = price_difference × lot_size
  All dollar figures must be mathematically exact. No approximations.

TRADE STYLE:
  swing : 3 TPs, wider SL, multi-session outlook, confluence score ≥ 7
  scalp : 2 TPs only, SL ≤ 15 pips (Forex) or ≤ 0.3% (Crypto), quick R:R ≥ 1:1.5, \
entry near current price, best during active session killzones

JSON schema (follow exactly):
{
  "pair": "XAU/USD",
  "direction": "BUY",
  "trade_style": "scalp",
  "current_market_price": "2345.50",
  "execution": "Buy Stop",
  "entry": "2346.00",
  "stop_loss": "2343.50",
  "stop_loss_pips": 25,
  "lot_size": "0.01",
  "pip_value": "$0.10 per pip",
  "risk_amount": "$2.50",
  "risk_percent": 2.50,
  "take_profits": [
    {"label": "TP1", "price": "2349.50", "pips": 35, "rr": "1:1.4", "partial_close": "50%"},
    {"label": "TP2", "price": "2353.00", "pips": 70, "rr": "1:2.8", "partial_close": "50%"}
  ],
  "estimated_profit_at_tp1": "$1.75",
  "estimated_profit_at_tp2": "$3.50",
  "total_potential_profit": "$5.25",
  "trailing_stop": {
    "recommended": true,
    "activate_at": "TP1",
    "trail_distance": "15 pips",
    "rationale": "Trail after TP1 to protect profit while targeting TP2."
  },
  "confluence_score": 8,
  "session": "London Open",
  "rationale": "Concise 2-3 sentence SMC/ICT analysis explaining the setup based on the live price.",
  "caution": "One sentence — specific invalidation level or key risk."
}

For swing trades include 3 take_profits. For scalp trades include 2 take_profits only.
"""


# ── Prompt builder ────────────────────────────────────────────────────────────

def _build_user_prompt(
    balance: float,
    market: str,
    pair: Optional[str],
    risk: str,
    trade_style: str,
    notes: str,
    live_price: Optional[str],
    pair_prices: Optional[dict],
    lot_size: str,
) -> str:
    risk_map = {"conservative": 1, "moderate": 2, "aggressive": 3}
    risk_pct = risk_map.get(risk.lower(), 2)
    lot = float(lot_size)
    pip_val = lot * 10  # USD pairs baseline

    # Price block
    if pair:
        pair_line = f"Pair     : {pair.upper()}"
        if live_price:
            price_block = (
                f"Live Price : {live_price} ← REAL price fetched now. "
                f"Anchor ALL levels to this. Derive execution from live vs entry."
            )
        else:
            price_block = (
                "Live Price : UNAVAILABLE — estimate from market knowledge. "
                "State estimate in current_market_price. Flag in caution."
            )
    else:
        pair_line = "Pair     : AI selects best opportunity"
        if pair_prices:
            prices_str = " | ".join(f"{p}={v}" for p, v in pair_prices.items())
            price_block = (
                f"Live Prices: {prices_str}\n"
                f"Pick the strongest setup. Use chosen pair's live price as anchor."
            )
        else:
            price_block = "Live Prices: UNAVAILABLE — use market knowledge."

    scalp_note = (
        "\nSCALP MODE: Keep SL ≤ 15 pips (Forex) / ≤ 0.3% (Crypto). "
        "Entry must be within 10 pips of live price. 2 TPs only. Fast setup."
        if trade_style == "scalp" else ""
    )

    return (
        f"Balance  : ${balance:,.2f}\n"
        f"Market   : {market}\n"
        f"{pair_line}\n"
        f"Risk     : {risk.capitalize()} ({risk_pct}% = ${balance * risk_pct / 100:,.2f})\n"
        f"Lot Size : {lot_size} (exact — do not change)\n"
        f"Pip Val  : ~${pip_val:.3f}/pip at this lot size (adjust for pair type)\n"
        f"Style    : {trade_style.upper()}{scalp_note}\n"
        f"Notes    : {notes or 'None'}\n\n"
        f"{price_block}\n\n"
        f"Return ONLY the JSON object."
    )


# ── Main function ─────────────────────────────────────────────────────────────

async def get_trade_plan(
    balance: float,
    market: str,
    pair: Optional[str] = None,
    risk: str = "moderate",
    trade_style: str = "swing",
    notes: str = "",
    lot_size: str = "0.01",
) -> dict:
    """
    Fetch live price then generate a trade plan with Claude.
    trade_style: 'swing' or 'scalp'
    """

    # Step 1 — Fetch live price(s)
    live_price: Optional[str] = None
    pair_prices: dict = {}

    if pair:
        logger.info("Fetching live price: %s", pair)
        live_price = await fetch_live_price(pair)
        if live_price:
            logger.info("Live price: %s = %s", pair, live_price)
        else:
            logger.warning("Price fetch failed for %s — Claude will estimate", pair)
    else:
        logger.info("Fetching all %s prices for AI selection", market)
        pair_prices = await fetch_all_prices(market)
        logger.info("Prices: %s", pair_prices)

    # Step 2 — Build prompt
    user_prompt = _build_user_prompt(
        balance=balance,
        market=market,
        pair=pair,
        risk=risk,
        trade_style=trade_style,
        notes=notes,
        live_price=live_price,
        pair_prices=pair_prices,
        lot_size=lot_size,
    )

    logger.info(
        "Claude call | model=%s pair=%s style=%s lot=%s live=%s",
        CLAUDE_MODEL, pair or "AI", trade_style, lot_size, live_price or "N/A"
    )

    # Step 3 — Call Claude (executor — SDK is sync)
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    try:
        loop = asyncio.get_event_loop()
        message = await loop.run_in_executor(
            None,
            lambda: client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=CLAUDE_MAX_TOKENS,
                timeout=CLAUDE_TIMEOUT,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            ),
        )
    except anthropic.APITimeoutError:
        raise AnalystError("Claude timed out. Please try again.")
    except anthropic.APIConnectionError as e:
        raise AnalystError(f"Connection error: {e}")
    except anthropic.RateLimitError:
        raise AnalystError("Rate limit hit. Wait a moment and try again.")
    except anthropic.APIStatusError as e:
        raise AnalystError(f"Claude API error {e.status_code}: {e.message}")

    # Step 4 — Parse
    raw: str = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        plan: dict = json.loads(raw)
    except json.JSONDecodeError as e:
        logger.error("JSON parse failed: %s", raw[:400])
        raise AnalystError(f"Unexpected format from Claude. Try again. ({e})")

    # Step 5 — Validate
    required = {
        "pair", "direction", "trade_style", "current_market_price",
        "execution", "entry", "stop_loss", "take_profits",
        "lot_size", "risk_amount", "total_potential_profit", "rationale",
    }
    missing = required - plan.keys()
    if missing:
        raise AnalystError(f"Claude response missing: {', '.join(missing)}")

    logger.info(
        "Plan ready | %s %s %s | entry=%s live=%s lot=%s",
        plan["trade_style"].upper(), plan["direction"], plan["pair"],
        plan["entry"], plan.get("current_market_price"), plan.get("lot_size"),
    )
    return plan
```

---

## File: `bot/__init__.py`

```python
# bot/__init__.py
```

---

## File: `bot/handlers.py`

All Telegram command and conversation handlers. Includes:
- `/start`, `/help`, `/setbalance`, `/history` commands
- A multi-step `/trade` conversation flow (Balance → Market → Pair → Style → Risk → Lot Size → Notes → Generate)
- Natural language parsing for free-text trade requests
- Trade generation via `ai.analyst.get_trade_plan()`
- Trade persistence via `db.database.log_trade()`

```python
"""
bot/handlers.py — All Telegram command and conversation handlers.

Conversation flow:
  /trade → Balance → Market → Pair → Trade Style → Risk → Lot Size → Notes → Generate

Natural language also supported:
  "I have $500, analyse EUR/USD, aggressive risk, scalp"
"""

import logging
import re
from typing import Optional

from telegram import Update, Message
from telegram.constants import ChatAction
from telegram.ext import (
    ContextTypes,
    ConversationHandler,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
)

from ai.analyst import get_trade_plan, AnalystError
from bot.formatter import format_trade_plan, format_history_entry
from bot.keyboards import (
    market_keyboard,
    trade_style_keyboard,
    risk_keyboard,
    forex_pair_keyboard,
    crypto_pair_keyboard,
    lot_size_keyboard,
    cancel_keyboard,
)
from db.database import get_user, upsert_user, log_trade, get_trade_history

logger = logging.getLogger(__name__)

# ── Conversation states ───────────────────────────────────────────────────────

(
    ASK_BALANCE,
    ASK_MARKET,
    ASK_PAIR,
    ASK_STYLE,
    ASK_RISK,
    ASK_LOT_SIZE,
    ASK_NOTES,
    GENERATE,
) = range(8)

TRADE_KEY = "trade_data"


# ── /start ────────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    await upsert_user(user.id, user.username or "")
    await update.message.reply_text(
        f"👋 Welcome, {user.first_name}!\n\n"
        "I'm your *AI Trade Planner* — powered by live market data and Claude AI.\n\n"
        "I analyse the market in real time and return a complete trade plan:\n"
        "• Entry, Stop Loss, Take Profits\n"
        "• Exact lot sizing and risk in dollars\n"
        "• Scalp or Swing setups\n"
        "• Live-price anchored levels\n\n"
        "Commands:\n"
        "/trade — Start a new trade analysis\n"
        "/history — View your last 5 trade plans\n"
        "/setbalance — Save your default balance\n"
        "/help — Show this message\n\n"
        "⚠️ *For educational purposes only. Always verify before trading.*",
        parse_mode="Markdown",
    )


# ── /help ─────────────────────────────────────────────────────────────────────

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await start(update, context)


# ── /setbalance ───────────────────────────────────────────────────────────────

async def set_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args
    if not args:
        await update.message.reply_text(
            "Usage: `/setbalance 500`", parse_mode="Markdown"
        )
        return
    try:
        balance = float(args[0].replace(",", "").replace("$", ""))
        if balance <= 0:
            raise ValueError
        user_id = update.effective_user.id
        await upsert_user(user_id, update.effective_user.username or "", default_balance=balance)
        await update.message.reply_text(
            f"✅ Default balance saved: *${balance:,.2f}*", parse_mode="Markdown"
        )
    except (ValueError, IndexError):
        await update.message.reply_text("❌ Invalid amount. Use: `/setbalance 500`", parse_mode="Markdown")


# ── /history ──────────────────────────────────────────────────────────────────

async def history(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    trades = await get_trade_history(user_id, limit=5)
    if not trades:
        await update.message.reply_text("📭 No trade history yet. Use /trade to get started.")
        return
    lines = ["📋 *Your last trade plans:*\n"]
    for i, trade in enumerate(trades, 1):
        lines.append(format_history_entry(trade, i))
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


# ── /trade — conversation entry ───────────────────────────────────────────────

async def trade_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data[TRADE_KEY] = {}
    user = await get_user(update.effective_user.id)
    default_balance = user.get("default_balance") if user else None

    if default_balance:
        context.user_data[TRADE_KEY]["balance"] = default_balance
        await update.message.reply_text(
            f"💰 Using saved balance: *${default_balance:,.2f}*\n\n"
            f"📊 Which *market* do you want to trade?",
            parse_mode="Markdown",
            reply_markup=market_keyboard(),
        )
        return ASK_MARKET

    await update.message.reply_text(
        "💰 What is your *account balance* in USD?\n\n"
        "_Example: 500 or 1000_",
        parse_mode="Markdown",
        reply_markup=cancel_keyboard(),
    )
    return ASK_BALANCE


async def received_balance(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip().replace(",", "").replace("$", "")
    try:
        balance = float(text)
        if balance <= 0:
            raise ValueError
        context.user_data[TRADE_KEY]["balance"] = balance
        await update.message.reply_text(
            f"✅ Balance: *${balance:,.2f}*\n\n📊 Which *market*?",
            parse_mode="Markdown",
            reply_markup=market_keyboard(),
        )
        return ASK_MARKET
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Enter a number like `500` or `1000`.",
            parse_mode="Markdown",
        )
        return ASK_BALANCE


async def received_market(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    market_map = {"market_forex": "Forex", "market_crypto": "Crypto"}
    market = market_map.get(query.data)
    if not market:
        return ASK_MARKET

    context.user_data[TRADE_KEY]["market"] = market
    kb = forex_pair_keyboard() if market == "Forex" else crypto_pair_keyboard()
    await query.edit_message_text(
        f"✅ Market: *{market}*\n\n🔀 Which *pair*?",
        parse_mode="Markdown",
        reply_markup=kb,
    )
    return ASK_PAIR


async def received_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    if query.data == "pair_auto":
        context.user_data[TRADE_KEY]["pair"] = None
        pair_label = "🤖 AI selects best pair"
    elif query.data == "pair_custom":
        await query.edit_message_text(
            "✏️ Type your pair (e.g. `XAU/USD`, `EUR/GBP`, `BTC/USDT`):",
            parse_mode="Markdown",
        )
        return ASK_PAIR
    else:
        raw = query.data.replace("pair_", "")
        if "/" not in raw:
            if len(raw) == 6:
                raw = raw[:3] + "/" + raw[3:]
            elif len(raw) == 7:
                raw = raw[:4] + "/" + raw[4:]
        context.user_data[TRADE_KEY]["pair"] = raw
        pair_label = raw

    await query.edit_message_text(
        f"✅ Pair: *{pair_label}*\n\n⚡ *Trade style?*",
        parse_mode="Markdown",
        reply_markup=trade_style_keyboard(),
    )
    return ASK_STYLE


async def received_pair_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Handle manually typed pair."""
    text = update.message.text.strip().upper().replace(" ", "")
    if "/" not in text:
        if len(text) == 6:
            text = text[:3] + "/" + text[3:]
        elif len(text) == 7:
            text = text[:4] + "/" + text[4:]
    context.user_data[TRADE_KEY]["pair"] = text
    await update.message.reply_text(
        f"✅ Pair: *{text}*\n\n⚡ *Trade style?*",
        parse_mode="Markdown",
        reply_markup=trade_style_keyboard(),
    )
    return ASK_STYLE


async def received_style(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    style_map = {"style_scalp": "scalp", "style_swing": "swing"}
    style = style_map.get(query.data, "swing")
    context.user_data[TRADE_KEY]["trade_style"] = style
    style_label = "⚡ Scalp" if style == "scalp" else "📈 Swing"
    await query.edit_message_text(
        f"✅ Style: *{style_label}*\n\n⚖️ *Risk appetite?*",
        parse_mode="Markdown",
        reply_markup=risk_keyboard(),
    )
    return ASK_RISK


async def received_risk(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    risk_map = {
        "risk_conservative": "conservative",
        "risk_moderate": "moderate",
        "risk_aggressive": "aggressive",
    }
    risk = risk_map.get(query.data)
    if not risk:
        return ASK_RISK
    context.user_data[TRADE_KEY]["risk"] = risk
    risk_emoji = {"conservative": "🛡", "moderate": "⚖️", "aggressive": "🔥"}
    await query.edit_message_text(
        f"✅ Risk: *{risk_emoji[risk]} {risk.capitalize()}*\n\n"
        f"📐 *Lot size?*\n_0.01 is the broker minimum on most platforms._",
        parse_mode="Markdown",
        reply_markup=lot_size_keyboard(),
    )
    return ASK_LOT_SIZE


async def received_lot_size_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    if query.data == "lot_custom":
        await query.edit_message_text(
            "✏️ Type your lot size (e.g. `0.03`, `0.15`, `2.00`):",
            parse_mode="Markdown",
        )
        return ASK_LOT_SIZE
    lot = query.data.replace("lot_", "")
    context.user_data[TRADE_KEY]["lot_size"] = lot
    await query.edit_message_text(
        f"✅ Lot size: *{lot}*\n\n"
        f"📝 Any *additional notes*? (e.g. 'bearish bias', 'avoid NFP hour')\n"
        f"Or type *skip* to generate now.",
        parse_mode="Markdown",
    )
    return ASK_NOTES


async def received_lot_size_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    try:
        lot = float(text)
        if lot <= 0:
            raise ValueError
        if lot < 0.01:
            await update.message.reply_text(
                "⚠️ Minimum lot size is *0.01*. Please enter 0.01 or higher.",
                parse_mode="Markdown",
            )
            return ASK_LOT_SIZE
        lot_str = f"{lot:.2f}"
        context.user_data[TRADE_KEY]["lot_size"] = lot_str
        await update.message.reply_text(
            f"✅ Lot size: *{lot_str}*\n\n"
            f"📝 Any *additional notes*? Or type *skip* to generate.",
            parse_mode="Markdown",
        )
        return ASK_NOTES
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid. Enter a number like `0.01`, `0.10`, or `1.00`.",
            parse_mode="Markdown",
        )
        return ASK_LOT_SIZE


async def received_notes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    text = update.message.text.strip()
    notes = "" if text.lower() == "skip" else text
    context.user_data[TRADE_KEY]["notes"] = notes
    return await _generate_and_send(update.message, context)


# ── Natural language handler ──────────────────────────────────────────────────

def _parse_natural_language(text: str) -> Optional[dict]:
    """
    Parse a free-text trade request.
    Handles: balance, market, pair, risk, trade style.
    """
    text_lower = text.lower()

    # Balance
    balance_match = re.search(
        r'\$?([\d,]+(?:\.\d{1,2})?)\s*(?:dollars?|usd)?', text_lower
    )
    if not balance_match:
        return None
    try:
        balance = float(balance_match.group(1).replace(",", ""))
    except ValueError:
        return None

    # Market
    if any(w in text_lower for w in ["crypto", "bitcoin", "btc", "eth", "sol", "usdt"]):
        market = "Crypto"
    else:
        market = "Forex"

    # Pair — look for known pattern like EUR/USD, XAUUSD, BTC/USDT etc.
    pair_match = re.search(
        r'\b([A-Za-z]{2,4}[/]?[A-Za-z]{2,4})\b', text
    )
    pair = None
    if pair_match:
        raw = pair_match.group(1).upper()
        # Filter out words that match but aren't pairs
        non_pairs = {"HAVE", "WANT", "NEED", "WITH", "FOR", "AND", "THE"}
        if raw not in non_pairs and len(raw) >= 5:
            if "/" not in raw and len(raw) == 6:
                raw = raw[:3] + "/" + raw[3:]
            pair = raw

    # Risk
    if "conservative" in text_lower or "low risk" in text_lower:
        risk = "conservative"
    elif "aggressive" in text_lower or "high risk" in text_lower:
        risk = "aggressive"
    else:
        risk = "moderate"

    # Trade style
    if "scalp" in text_lower or "quick" in text_lower or "fast" in text_lower:
        trade_style = "scalp"
    else:
        trade_style = "swing"

    return {
        "balance": balance,
        "market": market,
        "pair": pair,
        "risk": risk,
        "trade_style": trade_style,
        "lot_size": "0.01",
        "notes": "",
    }


async def handle_natural_language(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    text = update.message.text.strip()
    parsed = _parse_natural_language(text)
    if not parsed:
        await update.message.reply_text(
            "I couldn't parse your request. Try:\n"
            "_\"I have $500, analyse EUR/USD, aggressive risk, scalp\"_\n\n"
            "Or use /trade to go step by step.",
            parse_mode="Markdown",
        )
        return

    context.user_data[TRADE_KEY] = parsed
    pair_label = parsed["pair"] or "AI picks best pair"
    style_icon = "⚡" if parsed["trade_style"] == "scalp" else "📈"
    await update.message.reply_text(
        f"📋 *Parsed request:*\n"
        f"Balance : ${parsed['balance']:,.2f}\n"
        f"Market  : {parsed['market']}\n"
        f"Pair    : {pair_label}\n"
        f"Risk    : {parsed['risk'].capitalize()}\n"
        f"Style   : {style_icon} {parsed['trade_style'].capitalize()}\n\n"
        f"⏳ Fetching live data and generating plan…",
        parse_mode="Markdown",
    )
    await _generate_and_send(update.message, context)


# ── Trade generation ──────────────────────────────────────────────────────────

async def _generate_and_send(
    message: Message, context: ContextTypes.DEFAULT_TYPE
) -> int:
    pending = context.user_data.get(TRADE_KEY, {})
    balance     = pending.get("balance", 0)
    market      = pending.get("market", "Forex")
    pair        = pending.get("pair")
    risk        = pending.get("risk", "moderate")
    trade_style = pending.get("trade_style", "swing")
    lot_size    = pending.get("lot_size", "0.01")
    notes       = pending.get("notes", "")
    user_id     = message.from_user.id

    if not balance or balance <= 0:
        await message.reply_text("⚠️ Balance missing. Use /trade to start again.")
        return ConversationHandler.END

    thinking = await message.reply_text("⏳ Fetching live market data…")
    await message.chat.send_action(ChatAction.TYPING)

    try:
        plan = await get_trade_plan(
            balance=balance,
            market=market,
            pair=pair,
            risk=risk,
            trade_style=trade_style,
            notes=notes,
            lot_size=lot_size,
        )
    except AnalystError as exc:
        await thinking.delete()
        await message.reply_text(f"❌ Analysis failed:\n{exc}\n\nTry again with /trade.")
        return ConversationHandler.END
    except Exception:
        logger.exception("Unexpected error generating plan")
        await thinking.delete()
        await message.reply_text("❌ Unexpected error. Please try /trade again.")
        return ConversationHandler.END

    try:
        await log_trade(user_id, plan)
    except Exception:
        logger.exception("Failed to log trade to DB")

    await thinking.delete()
    await message.reply_text(format_trade_plan(plan, balance))
    context.user_data.pop(TRADE_KEY, None)
    return ConversationHandler.END


# ── Cancel ────────────────────────────────────────────────────────────────────

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(TRADE_KEY, None)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text("❌ Trade analysis cancelled.")
    else:
        await update.message.reply_text("❌ Cancelled. Use /trade to start again.")
    return ConversationHandler.END


# ── Register all handlers ─────────────────────────────────────────────────────

def register_handlers(app) -> None:
    # Simple commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("setbalance", set_balance))
    app.add_handler(CommandHandler("history", history))

    # Trade conversation
    trade_conv = ConversationHandler(
        entry_points=[CommandHandler("trade", trade_start)],
        states={
            ASK_BALANCE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_balance)
            ],
            ASK_MARKET: [
                CallbackQueryHandler(received_market, pattern="^market_")
            ],
            ASK_PAIR: [
                CallbackQueryHandler(received_pair, pattern="^pair_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_pair_text),
            ],
            ASK_STYLE: [
                CallbackQueryHandler(received_style, pattern="^style_")
            ],
            ASK_RISK: [
                CallbackQueryHandler(received_risk, pattern="^risk_")
            ],
            ASK_LOT_SIZE: [
                CallbackQueryHandler(received_lot_size_callback, pattern="^lot_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_lot_size_text),
            ],
            ASK_NOTES: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, received_notes)
            ],
        },
        fallbacks=[
            CallbackQueryHandler(cancel, pattern="^cancel$"),
            CommandHandler("cancel", cancel),
        ],
        allow_reentry=True,
    )
    app.add_handler(trade_conv)

    # Natural language fallback — catches any free-text message outside conversation
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_natural_language)
    )
```

---

## File: `bot/keyboards.py`

All inline keyboard definitions for Telegram interactive menus.

```python
"""
bot/keyboards.py — All inline keyboard definitions.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def market_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("💱 Forex", callback_data="market_forex"),
        InlineKeyboardButton("₿ Crypto", callback_data="market_crypto"),
    ]])


def trade_style_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Scalp  (quick, tight SL/TP)", callback_data="style_scalp")],
        [InlineKeyboardButton("📈 Swing  (multi-session, wider targets)", callback_data="style_swing")],
    ])


def risk_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛡 Conservative (1%)", callback_data="risk_conservative"),
            InlineKeyboardButton("⚖️ Moderate (2%)", callback_data="risk_moderate"),
        ],
        [
            InlineKeyboardButton("🔥 Aggressive (3%)", callback_data="risk_aggressive"),
        ],
    ])


def forex_pair_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("EUR/USD", callback_data="pair_EURUSD"),
            InlineKeyboardButton("GBP/USD", callback_data="pair_GBPUSD"),
        ],
        [
            InlineKeyboardButton("USD/JPY", callback_data="pair_USDJPY"),
            InlineKeyboardButton("GBP/JPY", callback_data="pair_GBPJPY"),
        ],
        [
            InlineKeyboardButton("XAU/USD (Gold)", callback_data="pair_XAUUSD"),
            InlineKeyboardButton("XAG/USD (Silver)", callback_data="pair_XAGUSD"),
        ],
        [InlineKeyboardButton("✏️ Type any pair", callback_data="pair_custom")],
        [InlineKeyboardButton("🤖 AI picks best pair", callback_data="pair_auto")],
    ])


def crypto_pair_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("BTC/USDT", callback_data="pair_BTCUSDT"),
            InlineKeyboardButton("ETH/USDT", callback_data="pair_ETHUSDT"),
        ],
        [
            InlineKeyboardButton("SOL/USDT", callback_data="pair_SOLUSDT"),
            InlineKeyboardButton("BNB/USDT", callback_data="pair_BNBUSDT"),
        ],
        [InlineKeyboardButton("✏️ Type any pair", callback_data="pair_custom")],
        [InlineKeyboardButton("🤖 AI picks best pair", callback_data="pair_auto")],
    ])


def lot_size_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("0.01 (micro)", callback_data="lot_0.01"),
            InlineKeyboardButton("0.02", callback_data="lot_0.02"),
        ],
        [
            InlineKeyboardButton("0.05", callback_data="lot_0.05"),
            InlineKeyboardButton("0.10", callback_data="lot_0.10"),
        ],
        [
            InlineKeyboardButton("0.25", callback_data="lot_0.25"),
            InlineKeyboardButton("0.50", callback_data="lot_0.50"),
        ],
        [InlineKeyboardButton("1.00 (standard)", callback_data="lot_1.00")],
        [InlineKeyboardButton("✏️ Type custom lot size", callback_data="lot_custom")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    ]])
```

---

## File: `bot/formatter.py`

Converts a Claude trade plan dict into a clean Telegram message. Supports both scalp (2 TPs) and swing (3 TPs) layouts. Includes history formatting.

```python
"""
bot/formatter.py — Convert a Claude trade plan dict into a clean Telegram message.

Changes from previous version:
  - MT5 setup section removed
  - Scalp vs swing layout awareness (scalp = 2 TPs, swing = 3 TPs)
  - Fixed crypto detection (covers XRP, DOGE, ADA, USDT pairs)
  - Single datetime import at top
  - Removed redundant _wrap utility (unused)
"""

from datetime import datetime, timezone


# ── Helpers ───────────────────────────────────────────────────────────────────

def _direction_emoji(direction: str) -> str:
    return "🟢" if direction.upper() == "BUY" else "🔴"


CRYPTO_KEYWORDS = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "USDT"}

def _is_crypto(pair: str) -> bool:
    return any(kw in pair.upper() for kw in CRYPTO_KEYWORDS)


# ── Main formatter ────────────────────────────────────────────────────────────

def format_trade_plan(plan: dict, balance: float) -> str:
    direction   = plan.get("direction", "")
    pair        = plan.get("pair", "N/A")
    trade_style = plan.get("trade_style", "swing").upper()
    dir_emoji   = _direction_emoji(direction)
    style_emoji = "⚡" if trade_style == "SCALP" else "📈"

    lines = []

    # ── Header ────────────────────────────────────────────────
    lines.append(f"📊 TRADE PLAN — {pair}  {style_emoji} {trade_style}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"Direction   : {dir_emoji} {direction}")
    lines.append(f"Execution   : {plan.get('execution', 'N/A')}")
    lines.append(f"Session     : {plan.get('session', 'N/A')}")
    lines.append(f"Confluence  : ⭐ {plan.get('confluence_score', 'N/A')}/10")

    # ── Account ───────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("💰 ACCOUNT")
    lines.append(f"Balance     : ${balance:,.2f}")
    lines.append(f"At Risk     : {plan.get('risk_amount', 'N/A')} ({plan.get('risk_percent', 'N/A')}%)")

    # ── Position Size ─────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📐 POSITION SIZE")
    lines.append(f"Lot Size    : {plan.get('lot_size', 'N/A')}")
    lines.append(f"Pip Value   : {plan.get('pip_value', 'N/A')}")

    # ── Levels ────────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📍 LEVELS")
    lines.append(f"Market Price: {plan.get('current_market_price', 'N/A')}")
    lines.append(f"Entry       : {plan.get('entry', 'N/A')}")
    lines.append(
        f"Stop Loss   : {plan.get('stop_loss', 'N/A')} "
        f"({plan.get('stop_loss_pips', 'N/A')} pips)"
    )

    # ── Take Profits ──────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("🎯 TAKE PROFITS")

    tp_profit_keys = [
        "estimated_profit_at_tp1",
        "estimated_profit_at_tp2",
        "estimated_profit_at_tp3",
    ]
    for i, tp in enumerate(plan.get("take_profits", [])):
        profit = plan.get(tp_profit_keys[i], "N/A") if i < len(tp_profit_keys) else "N/A"
        lines.append(
            f"{tp['label']} → {tp['price']} | {tp.get('rr', 'N/A'):>6} "
            f"| Close {tp.get('partial_close', 'N/A')} → Est: {profit}"
        )
    lines.append(f"Total if all TPs hit → {plan.get('total_potential_profit', 'N/A')}")

    # ── Trailing Stop ─────────────────────────────────────────
    ts = plan.get("trailing_stop", {})
    if ts and ts.get("recommended"):
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔁 TRAILING STOP")
        lines.append(
            f"Activate at {ts.get('activate_at')} → Trail {ts.get('trail_distance')}"
        )
        lines.append(ts.get("rationale", ""))

    # ── Analysis ──────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("📖 ANALYSIS")
    lines.append(plan.get("rationale", "N/A"))

    # ── Caution ───────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append("⚠️  CAUTION")
    lines.append(plan.get("caution", "N/A"))

    # ── Timestamp ─────────────────────────────────────────────
    lines.append("━━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"⏱ Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
    )

    return "\n".join(lines)


# ── History formatter ─────────────────────────────────────────────────────────

def format_history_entry(trade: dict, index: int) -> str:
    """Format a single trade history row into a compact Telegram summary."""
    pair      = (trade.get("pair") or "N/A").upper()
    direction = (trade.get("direction") or "N/A").upper()
    entry     = trade.get("entry") or "N/A"
    sl        = trade.get("stop_loss") or "N/A"
    risk      = trade.get("risk_amount") or "N/A"
    score     = trade.get("confluence_score") or "?"
    created   = trade.get("created_at") or "N/A"
    style     = (trade.get("trade_style") or "swing").upper()

    emoji = _direction_emoji(direction)
    style_icon = "⚡" if style == "SCALP" else "📈"
    return (
        f"{index}. {emoji} {direction} {pair} {style_icon}{style}\n"
        f"   Entry: {entry}  SL: {sl}  Risk: {risk}  ⭐{score}/10\n"
        f"   📅 {created}"
    )
```

---

## File: `db/__init__.py`

```python
# db/__init__.py
```

---

## File: `db/database.py`

Async SQLite database layer using `aiosqlite`. Manages two tables — `users` (telegram_id, username, default_balance, default_risk) and `trades` (full trade history with raw JSON).

```python
"""
db/database.py — Async SQLite database layer using aiosqlite.

Handles all persistence: users and trade history.
"""

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import aiosqlite

from config import DATABASE_PATH

if TYPE_CHECKING:
    from telegram.ext import Application

logger = logging.getLogger(__name__)


# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id   INTEGER PRIMARY KEY,
    username      TEXT,
    default_balance REAL,
    default_risk  TEXT DEFAULT 'moderate',
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

CREATE_TRADES_TABLE = """
CREATE TABLE IF NOT EXISTS trades (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id     INTEGER,
    pair            TEXT,
    direction       TEXT,
    entry           TEXT,
    stop_loss       TEXT,
    risk_amount     TEXT,
    confluence_score INTEGER,
    raw_json        TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (telegram_id) REFERENCES users(telegram_id)
);
"""


# ── Initialisation ────────────────────────────────────────────────────────────

async def init_db(app: Optional["Application"] = None) -> None:
    """Create tables if they do not already exist."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(CREATE_USERS_TABLE)
        await db.execute(CREATE_TRADES_TABLE)
        await db.commit()
    logger.info("Database initialised at %s", DATABASE_PATH)


# ── User helpers ──────────────────────────────────────────────────────────────

async def upsert_user(telegram_id: int, username: Optional[str]) -> None:
    """Insert a new user or update their username on conflict."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
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
    """Return a user row as a dict, or None if not found."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None


async def set_user_balance(telegram_id: int, balance: float) -> None:
    """Store / update the user's default trading balance."""
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
    """Store / update the user's default risk appetite."""
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


# ── Trade helpers ─────────────────────────────────────────────────────────────

async def log_trade(telegram_id: int, plan: dict) -> None:
    """Persist a completed trade plan dict to the trades table."""
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
                json.dumps(plan),
            ),
        )
        await db.commit()


async def get_trade_history(telegram_id: int, limit: int = 5) -> list[dict]:
    """Return the last `limit` trades for a user, newest first."""
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
```

---

## File: `market/__init__.py`

```python

```

(Empty file — serves as package marker)

---

## File: `market/prices.py`

Live market price fetcher. Routes crypto pairs to Binance public API (no key needed) and everything else (Forex, metals, indices) to Twelve Data API.

```python
"""
market/prices.py — Live market price fetcher.

Supports ANY valid trading pair — not limited to a predefined list.

Routing:
  Crypto keywords (USDT, BTC, ETH, etc.) → Binance public API (no key needed)
  Everything else (Forex, metals, indices) → Twelve Data API (free tier)
"""

import logging
from typing import Optional

import httpx

from config import BINANCE_PRICE_URL, TWELVE_DATA_URL, TWELVE_DATA_API_KEY

logger = logging.getLogger(__name__)

# ── Pair classification ───────────────────────────────────────────────────────

CRYPTO_KEYWORDS = {"USDT", "BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA"}


def is_crypto(pair: str) -> bool:
    return any(kw in pair.upper() for kw in CRYPTO_KEYWORDS)


def normalise_binance(pair: str) -> str:
    """BTC/USDT → BTCUSDT"""
    return pair.replace("/", "").upper()


def normalise_twelve(pair: str) -> str:
    """Ensure slash format for Twelve Data. XAUUSD → XAU/USD"""
    pair = pair.upper().replace(" ", "")
    if "/" in pair:
        return pair
    if len(pair) == 6:
        return pair[:3] + "/" + pair[3:]
    if len(pair) == 7:
        return pair[:4] + "/" + pair[4:]
    return pair


# ── Binance ───────────────────────────────────────────────────────────────────

async def _fetch_binance(pair: str) -> Optional[str]:
    symbol = normalise_binance(pair)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(BINANCE_PRICE_URL, params={"symbol": symbol})
            r.raise_for_status()
            data = r.json()
            price = data.get("price")
            if price:
                p = float(price)
                if p >= 10000:
                    fmt = f"{p:.2f}"
                elif p >= 100:
                    fmt = f"{p:.3f}"
                elif p >= 1:
                    fmt = f"{p:.4f}"
                else:
                    fmt = f"{p:.6f}"
                logger.info("Binance | %s = %s", symbol, fmt)
                return fmt
            logger.warning("Binance no price for %s: %s", symbol, data)
    except httpx.TimeoutException:
        logger.warning("Binance timeout: %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Binance HTTP %s: %s", e.response.status_code, symbol)
    except Exception as e:
        logger.exception("Binance error %s: %s", symbol, e)
    return None


# ── Twelve Data ───────────────────────────────────────────────────────────────

async def _fetch_twelve_data(pair: str) -> Optional[str]:
    if not TWELVE_DATA_API_KEY:
        logger.warning("TWELVE_DATA_API_KEY not set — skipping %s", pair)
        return None

    symbol = normalise_twelve(pair)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            r = await client.get(
                TWELVE_DATA_URL,
                params={"symbol": symbol, "apikey": TWELVE_DATA_API_KEY},
            )
            r.raise_for_status()
            data = r.json()
            if data.get("status") == "error":
                logger.warning("Twelve Data error %s: %s", symbol, data.get("message"))
                return None
            price = data.get("price")
            if price:
                p = float(price)
                if p >= 1000:
                    fmt = f"{p:.2f}"     # Gold: 2345.67
                elif p >= 100:
                    fmt = f"{p:.3f}"     # JPY: 157.832
                else:
                    fmt = f"{p:.5f}"     # EUR/USD: 1.08432
                logger.info("TwelveData | %s = %s", symbol, fmt)
                return fmt
            logger.warning("Twelve Data no price for %s: %s", symbol, data)
    except httpx.TimeoutException:
        logger.warning("Twelve Data timeout: %s", symbol)
    except httpx.HTTPStatusError as e:
        logger.warning("Twelve Data HTTP %s: %s", e.response.status_code, symbol)
    except Exception as e:
        logger.exception("Twelve Data error %s: %s", symbol, e)
    return None


# ── Public interface ──────────────────────────────────────────────────────────

async def fetch_live_price(pair: str) -> Optional[str]:
    """Fetch live price for any valid pair. Auto-routes to correct API."""
    if is_crypto(pair):
        return await _fetch_binance(pair)
    return await _fetch_twelve_data(pair)


async def fetch_all_prices(market: str) -> dict:
    """
    Fetch live prices for recommended pairs when no pair is specified.
    Claude uses these real prices to select the best opportunity.
    """
    pairs = (
        ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
        if market == "Crypto"
        else ["EUR/USD", "GBP/USD", "USD/JPY", "GBP/JPY", "XAU/USD"]
    )
    prices = {}
    for pair in pairs:
        price = await fetch_live_price(pair)
        if price:
            prices[pair] = price
        else:
            logger.warning("No price for %s", pair)
    logger.info("Prices fetched %d/%d: %s", len(prices), len(pairs), prices)
    return prices
```

---

## Data Flow Summary

```
User (Telegram) → /trade command or free-text message
       ↓
bot/handlers.py — Parses input, builds context
       ↓
market/prices.py — Fetches live price from Binance (crypto) or Twelve Data (forex)
       ↓
ai/analyst.py — Sends prompt + live price to Claude API
       ↓
ai/analyst.py — Parses & validates JSON response
       ↓
db/database.py — Persists trade plan to SQLite
       ↓
bot/formatter.py — Formats plan into styled Telegram message
       ↓
User receives structured trade plan
```
