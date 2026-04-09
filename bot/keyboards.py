"""
bot/keyboards.py — Inline and reply keyboard definitions.

Keeps all keyboard markup in one place so handlers stay clean.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup


# ── Market selection ──────────────────────────────────────────────────────────

def market_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing Forex or Crypto."""
    buttons = [
        [
            InlineKeyboardButton("💱 Forex", callback_data="market_forex"),
            InlineKeyboardButton("₿ Crypto", callback_data="market_crypto"),
        ]
    ]
    return InlineKeyboardMarkup(buttons)


# ── Risk appetite ─────────────────────────────────────────────────────────────

def risk_keyboard() -> InlineKeyboardMarkup:
    """Inline keyboard for choosing risk appetite."""
    buttons = [
        [
            InlineKeyboardButton("🛡 Conservative (1%)", callback_data="risk_conservative"),
            InlineKeyboardButton("⚖️ Moderate (2%)", callback_data="risk_moderate"),
        ],
        [
            InlineKeyboardButton("🔥 Aggressive (3%)", callback_data="risk_aggressive"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ── Pair selection (Forex) ────────────────────────────────────────────────────

def forex_pair_keyboard() -> InlineKeyboardMarkup:
    """Quick-pick inline keyboard for common Forex pairs."""
    buttons = [
        [
            InlineKeyboardButton("EUR/USD", callback_data="pair_EURUSD"),
            InlineKeyboardButton("GBP/USD", callback_data="pair_GBPUSD"),
        ],
        [
            InlineKeyboardButton("USD/JPY", callback_data="pair_USDJPY"),
            InlineKeyboardButton("GBP/JPY", callback_data="pair_GBPJPY"),
        ],
        [
            InlineKeyboardButton("🤖 AI picks best pair", callback_data="pair_auto"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ── Pair selection (Crypto) ───────────────────────────────────────────────────

def crypto_pair_keyboard() -> InlineKeyboardMarkup:
    """Quick-pick inline keyboard for common Crypto pairs."""
    buttons = [
        [
            InlineKeyboardButton("BTC/USDT", callback_data="pair_BTCUSDT"),
            InlineKeyboardButton("ETH/USDT", callback_data="pair_ETHUSDT"),
        ],
        [
            InlineKeyboardButton("SOL/USDT", callback_data="pair_SOLUSDT"),
            InlineKeyboardButton("BNB/USDT", callback_data="pair_BNBUSDT"),
        ],
        [
            InlineKeyboardButton("🤖 AI picks best pair", callback_data="pair_auto"),
        ],
    ]
    return InlineKeyboardMarkup(buttons)


# ── Cancel ────────────────────────────────────────────────────────────────────

def cancel_keyboard() -> InlineKeyboardMarkup:
    """Single cancel button for mid-flow cancellation."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data="cancel")]]
    )
