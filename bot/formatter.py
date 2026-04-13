"""
bot/formatter.py — Convert a Claude trade plan dict to a Telegram message.

Produces a clean, emoji-rich trade card ready to send via Telegram.
"""

from datetime import datetime, timezone
from typing import Optional


# ── Direction emoji helper ────────────────────────────────────────────────────

def _direction_emoji(direction: str) -> str:
    return "🟢" if direction.upper() == "BUY" else "🔴"


# ── Main formatter ────────────────────────────────────────────────────────────

def format_trade_plan(plan: dict, balance: float) -> str:
    direction = plan.get("direction", "")
    dir_emoji = "🟢" if direction == "BUY" else "🔴"
    pair = plan.get("pair", "N/A")
    lot_size = float(plan.get("lot_size", 0))
    min_lot_warning = plan.get("minimum_lot_warning", False)
    market = "Crypto" if any(c in pair for c in ["BTC", "ETH", "SOL", "BNB"]) else "Forex"

    lines = []

    # ── Header ────────────────────────────────────────────────
    lines.append(f"📊 TRADE PLAN — {pair}")
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
    if min_lot_warning:
        lines.append(
            f"⚠️ True lot size was below 0.01 minimum. Rounded up to 0.01 — "
            f"actual risk is {plan.get('risk_percent')}% of balance, "
            f"higher than your intended risk. Consider a larger account balance."
        )

    # ── Current Price + Levels ────────────────────────────────
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
        profit = plan.get(tp_profit_keys[i], "N/A")
        lines.append(
            f"{tp['label']} → {tp['price']} | {tp['rr']:>5} "
            f"| Close {tp['partial_close']} → Est. profit: {profit}"
        )
    lines.append(f"Total if all TPs hit  → {plan.get('total_potential_profit', 'N/A')}")

    # ── Trailing Stop ─────────────────────────────────────────
    ts = plan.get("trailing_stop", {})
    if ts and ts.get("recommended"):
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🔁 TRAILING STOP")
        lines.append(
            f"Activate at {ts.get('activate_at')} → Trail {ts.get('trail_distance')}"
        )
        lines.append(ts.get("rationale", ""))

    # ── MT5 Setup (Forex only) ────────────────────────────────
    mt5 = plan.get("mt5_setup")
    if mt5 and market == "Forex":
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append("🖥 MT5 SETUP")
        lines.append(f"Symbol      : {mt5.get('symbol')}")
        lines.append(f"Order Type  : {mt5.get('order_type')}")
        lines.append(f"Volume      : {mt5.get('volume')}")
        lines.append(f"Price       : {mt5.get('price')}")
        lines.append(f"Stop Loss   : {mt5.get('sl')}")
        lines.append(f"Take Profit : {mt5.get('tp')} (TP1)")
        lines.append(f"Comment     : {mt5.get('comment')}")
        lines.append("━━━━━━━━━━━━━━━━━━━━")
        lines.append(f"📌 {mt5.get('note')}")

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
    from datetime import datetime, timezone
    lines.append(f"⏱ Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC")

    return "\n".join(lines)


def format_history_entry(trade: dict, index: int) -> str:
    """
    Format a single trade history row (from DB) into a compact summary.

    Args:
        trade : Dict row from db.get_trade_history().
        index : 1-based position in the history list.
    """
    pair = (trade.get("pair") or "N/A").upper()
    direction = (trade.get("direction") or "N/A").upper()
    entry = trade.get("entry") or "N/A"
    sl = trade.get("stop_loss") or "N/A"
    risk = trade.get("risk_amount") or "N/A"
    score = trade.get("confluence_score") or "?"
    created = trade.get("created_at") or "N/A"

    emoji = _direction_emoji(direction)
    return (
        f"{index}. {emoji} {direction} {pair}\n"
        f"   Entry: {entry}  SL: {sl}  Risk: {risk}  ⭐{score}/10\n"
        f"   📅 {created}"
    )


# ── Utility ───────────────────────────────────────────────────────────────────

def _wrap(text: str, width: int = 50) -> str:
    """Simple word-wrap for long strings (avoids broken words)."""
    words = text.split()
    lines, current = [], []
    length = 0
    for word in words:
        if length + len(word) + 1 > width and current:
            lines.append(" ".join(current))
            current, length = [], 0
        current.append(word)
        length += len(word) + 1
    if current:
        lines.append(" ".join(current))
    return "\n".join(lines)
