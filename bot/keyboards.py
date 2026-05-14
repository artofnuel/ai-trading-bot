"""
bot/keyboards.py — Inline keyboard definitions. Forex only.
"""

from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def trade_style_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("⚡ Scalp  (quick, tight SL/TP)", callback_data="style_scalp")],
        [InlineKeyboardButton("📈 Swing  (multi-session, wider targets)", callback_data="style_swing")],
    ])


def risk_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🛡 Conservative (1%)", callback_data="risk_conservative"),
            InlineKeyboardButton("⚖️ Moderate (2%)",    callback_data="risk_moderate"),
        ],
        [InlineKeyboardButton("🔥 Aggressive (3%)", callback_data="risk_aggressive")],
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
        [
            InlineKeyboardButton("AUD/USD", callback_data="pair_AUDUSD"),
            InlineKeyboardButton("EUR/GBP", callback_data="pair_EURGBP"),
        ],
        [InlineKeyboardButton("✏️ Type any Forex pair", callback_data="pair_custom")],
        [InlineKeyboardButton("🤖 AI picks best opportunity", callback_data="pair_auto")],
    ])


def lot_size_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("0.01 (micro)", callback_data="lot_0.01"),
            InlineKeyboardButton("0.02",         callback_data="lot_0.02"),
        ],
        [
            InlineKeyboardButton("0.05",         callback_data="lot_0.05"),
            InlineKeyboardButton("0.10",         callback_data="lot_0.10"),
        ],
        [
            InlineKeyboardButton("0.25",         callback_data="lot_0.25"),
            InlineKeyboardButton("0.50",         callback_data="lot_0.50"),
        ],
        [InlineKeyboardButton("1.00 (standard)", callback_data="lot_1.00")],
        [InlineKeyboardButton("✏️ Type custom lot size", callback_data="lot_custom")],
    ])


def cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("❌ Cancel", callback_data="cancel")
    ]])
