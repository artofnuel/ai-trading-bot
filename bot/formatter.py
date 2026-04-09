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
    """
    Render a trade plan dict as a human-readable Telegram message string.

    Args:
        plan    : Validated trade plan dict from ai/analyst.py.
        balance : User's account balance (for the account section).

    Returns:
        A formatted multi-line string safe to send via Telegram (MarkdownV2
        is intentionally avoided — plain text is used for reliability).
    """
    sep = "━━━━━━━━━━━━━━━━━━━━"
    direction = plan.get("direction", "N/A").upper()
    pair = plan.get("pair", "N/A").upper()
    confluence = plan.get("confluence_score", "?")
    session = plan.get("session", "N/A")
    execution = plan.get("execution", "N/A")

    # ── Header ────────────────────────────────────────────────────────────────
    lines = [
        f"📊 TRADE PLAN — {pair}",
        sep,
        f"Direction   : {_direction_emoji(direction)} {direction}",
        f"Execution   : {execution}",
        f"Session     : {session}",
        f"Confluence  : ⭐ {confluence}/10",
        sep,
    ]

    # ── Account ───────────────────────────────────────────────────────────────
    risk_amount = plan.get("risk_amount", "N/A")
    risk_pct = plan.get("risk_percent", "N/A")
    lines += [
        "💰 ACCOUNT",
        f"Balance     : ${balance:,.2f}",
        f"At Risk     : {risk_amount} ({risk_pct}%)",
        sep,
    ]

    # ── Levels ────────────────────────────────────────────────────────────────
    entry = plan.get("entry", "N/A")
    sl = plan.get("stop_loss", "N/A")
    sl_pips = plan.get("stop_loss_pips", "N/A")
    lines += [
        "📍 LEVELS",
        f"Entry       : {entry}",
        f"Stop Loss   : {sl} ({sl_pips} pips)",
        sep,
    ]

    # ── Take Profits ──────────────────────────────────────────────────────────
    lines.append("🎯 TAKE PROFITS")
    take_profits: list = plan.get("take_profits", [])
    est_tp2 = plan.get("estimated_profit_at_tp2", "")
    est_tp3 = plan.get("estimated_profit_at_tp3", "")

    for tp in take_profits:
        label = tp.get("label", "TP?")
        price = tp.get("price", "N/A")
        rr = tp.get("rr", "N/A")
        partial = tp.get("partial_close", "N/A")
        suffix = ""
        if label == "TP2" and est_tp2:
            suffix = f" → Est. profit: {est_tp2}"
        elif label == "TP3" and est_tp3:
            suffix = f" → Est. profit: {est_tp3}"
        lines.append(f"{label} → {price} | {rr:>5} | Close {partial}{suffix}")

    lines.append(sep)

    # ── Trailing Stop ─────────────────────────────────────────────────────────
    ts = plan.get("trailing_stop", {})
    if ts.get("recommended"):
        activate = ts.get("activate_at", "TP1")
        trail = ts.get("trail_distance", "N/A")
        rationale = ts.get("rationale", "")
        lines += [
            "🔁 TRAILING STOP",
            f"Activate at {activate} → Trail {trail}",
            rationale,
            sep,
        ]

    # ── Analysis ──────────────────────────────────────────────────────────────
    rationale: str = plan.get("rationale", "")
    if rationale:
        # Wrap long rationale lines at word boundaries for mobile readability
        lines += ["📖 ANALYSIS", _wrap(rationale, width=50), sep]

    # ── Caution ───────────────────────────────────────────────────────────────
    caution: str = plan.get("caution", "")
    if caution:
        lines += [f"⚠️  CAUTION", caution, sep]

    # ── Timestamp ─────────────────────────────────────────────────────────────
    ts_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines.append(f"⏱ Generated: {ts_str}")

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
