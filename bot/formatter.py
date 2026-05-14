"""
bot/formatter.py — Compact single-message formatter for v3 plan schema.

TRADE    → clean signal card, one message, under Telegram's 4096 char limit
NO_TRADE → brief explanation card
"""

import re

from datetime import datetime, timezone


def _dir_emoji(d: str) -> str:
    return "🟢" if d.upper() == "BUY" else "🔴"


def _e(text: str) -> str:
    """Escape Markdown special chars in dynamic/LLM-sourced text."""
    return re.sub(r"([_*\[\]()~`>#+\-=|{}.!])", r"\\\1", text)


def format_trade_plan(plan: dict, balance: float) -> str:
    """Route to correct formatter based on plan type."""
    if plan.get("type") == "NO_TRADE":
        return _format_no_trade(plan)
    return _format_trade(plan, balance)


def _format_trade(plan: dict, balance: float) -> str:
    direction = plan.get("direction", "")
    pair      = plan.get("pair", "N/A")
    style     = plan.get("style", "swing").upper()
    se        = "⚡" if style == "SCALP" else "📈"
    de        = _dir_emoji(direction)
    kz        = "✅ Kill Zone" if plan.get("kill_zone") else "⏳ Outside KZ"
    score     = plan.get("confluence", "?")

    lines = []

    # Header
    lines.append(f"📊 *{pair}*  {se} {style}  {de} *{direction}*")
    lines.append(f"Session: {plan.get('session','N/A')}  {kz}  ⭐ {score}/10")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # Levels
    lines.append(
        f"Market : `{plan.get('live_price','N/A')}`   "
        f"Execution: {plan.get('execution','N/A')}"
    )
    lines.append(f"Entry  : `{plan.get('entry','N/A')}`")
    lines.append(f"SL     : `{plan.get('sl','N/A')}`  ({plan.get('sl_pips','N/A')} pips)")

    # Key levels
    pdh = plan.get("pdh")
    pdl = plan.get("pdl")
    if pdh or pdl:
        lines.append(f"PDH    : `{pdh}`   PDL : `{pdl}`")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # Risk
    lines.append(
        f"Lot: {plan.get('lot','N/A')}  "
        f"Pip: {plan.get('pip_value','N/A')}  "
        f"Risk: {plan.get('risk_usd','N/A')}"
    )
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # Take profits
    lines.append("🎯 *Take Profits*")
    for tp in plan.get("tps", []):
        lines.append(
            f"{tp.get('label','TP')}  `{tp.get('price','N/A')}`  "
            f"{tp.get('rr','N/A')}  close {tp.get('close','N/A')}  "
            f"→ {tp.get('profit','N/A')}"
        )
    lines.append(f"Total → *{plan.get('total_profit','N/A')}*")

    # Trailing stop
    trail = plan.get("trail", {})
    if trail and trail.get("active"):
        lines.append(f"🔁 Trail {trail.get('pips','N/A')} pips from {trail.get('from','TP1')}")
    lines.append("━━━━━━━━━━━━━━━━━━━━")

    # AMD context
    amd = plan.get("amd")
    if amd:
        lines.append(f"🔬 *AMD*: {_e(amd)}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")

    # Rationale
    lines.append(f"📖 {_e(plan.get('rationale','N/A'))}")

    # Caution
    caution = plan.get("caution")
    if caution:
        lines.append(f"⚠️ {_e(caution)}")

    # Timestamp
    lines.append(
        f"\n⏱ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC"
        f"  |  ⚠️ _Advisory only._"
    )

    return "\n".join(lines)


def _format_no_trade(plan: dict) -> str:
    lines = [
        "🚫 *NO TRADE — Conditions Not Met*",
        "━━━━━━━━━━━━━━━━━━━━",
        f"📋 {_e(plan.get('reason', 'No valid setup found at this time.'))}",
    ]
    suggestion = plan.get("suggestion")
    if suggestion:
        lines.append(f"💡 {_e(suggestion)}")
    lines.append(
        f"\n⏱ {datetime.now(timezone.utc).strftime('%H:%M UTC')}  |  Use /trade to try again."
    )
    return "\n".join(lines)


def format_history_entry(trade: dict, index: int) -> str:
    pair      = (trade.get("pair") or "N/A").upper()
    direction = (trade.get("direction") or "N/A").upper()
    entry     = trade.get("entry") or "N/A"
    sl        = trade.get("stop_loss") or "N/A"
    risk      = trade.get("risk_amount") or "N/A"
    score     = trade.get("confluence_score") or "?"
    created   = trade.get("created_at") or "N/A"
    de        = _dir_emoji(direction)
    return (
        f"{index}. {de} {direction} {pair}\n"
        f"   Entry: {entry}  SL: {sl}  Risk: {risk}  ⭐{score}/10\n"
        f"   📅 {created}"
    )
