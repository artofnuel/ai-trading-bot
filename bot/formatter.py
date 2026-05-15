"""
bot/formatter.py — Clean, readable Telegram signal card for v3.

Design principles:
  - Prices in monospace (backticks) so they stand out
  - Bold section labels
  - Consistent spacing — scannable in under 5 seconds
  - Single message, always under 4096 chars
  - NO_TRADE is brief and unambiguous
"""

from datetime import datetime, timezone


def _dir_emoji(d: str) -> str:
    return "🟢" if d.upper() == "BUY" else "🔴"


def format_trade_plan(plan: dict, balance: float) -> str:
    if plan.get("type") == "NO_TRADE":
        return _format_no_trade(plan)
    return _format_trade(plan, balance)


def _format_trade(plan: dict, balance: float) -> str:
    direction = plan.get("direction", "")
    pair      = plan.get("pair", "N/A")
    style     = plan.get("style", "swing").upper()
    se        = "⚡" if style == "SCALP" else "📈"
    de        = _dir_emoji(direction)
    kz        = "✅ Kill Zone" if plan.get("kill_zone") else "⚠️ Outside KZ"
    score     = plan.get("confluence", "?")

    # ── Header ──────────────────────────────────────────────
    msg  = f"{de} *{direction} {pair}* — {se} {style}\n"
    msg += f"📍 {plan.get('session', 'N/A')}  •  {kz}  •  ⭐ *{score}/10*\n"
    msg += "─────────────────────\n"

    # ── Levels ──────────────────────────────────────────────
    msg += f"*Price*    `{plan.get('live_price', 'N/A')}`\n"
    msg += f"*Entry*    `{plan.get('entry', 'N/A')}`  _{plan.get('execution', '')}_\n"
    msg += f"*SL*       `{plan.get('sl', 'N/A')}`  ({plan.get('sl_pips', '?')} pips)\n"

    # PDH / PDL on same line if both present
    pdh = plan.get("pdh")
    pdl = plan.get("pdl")
    if pdh and pdl:
        msg += f"*PDH/PDL*  `{pdh}` / `{pdl}`\n"
    msg += "─────────────────────\n"

    # ── Risk ────────────────────────────────────────────────
    msg += (
        f"*Lot* {plan.get('lot','N/A')}  "
        f"*Pip* {plan.get('pip_value','N/A')}  "
        f"*Risk* {plan.get('risk_usd','N/A')}\n"
    )
    msg += "─────────────────────\n"

    # ── Take Profits ────────────────────────────────────────
    msg += "*🎯 Take Profits*\n"
    for tp in plan.get("tps", []):
        msg += (
            f"`{tp.get('label','TP')}` "
            f"`{tp.get('price','N/A')}`  "
            f"{tp.get('rr','N/A')}  "
            f"({tp.get('close','N/A')} → {tp.get('profit','N/A')})\n"
        )
    msg += f"*Total* → {plan.get('total_profit','N/A')}\n"

    # Trailing stop
    trail = plan.get("trail", {})
    if trail and trail.get("active"):
        msg += f"🔁 Trail *{trail.get('pips','?')} pips* from {trail.get('from','TP1')}\n"
    msg += "─────────────────────\n"

    # ── AMD + Rationale ─────────────────────────────────────
    amd = plan.get("amd")
    if amd:
        msg += f"*📐 Structure*\n{amd}\n\n"

    msg += f"*📖 Rationale*\n{plan.get('rationale','N/A')}\n"

    # ── Caution ─────────────────────────────────────────────
    caution = plan.get("caution")
    if caution:
        msg += f"\n⚠️ _{caution}_\n"

    # ── Footer ──────────────────────────────────────────────
    msg += f"\n`{datetime.now(timezone.utc).strftime('%H:%M UTC')}` • _Advisory only_"

    return msg


def _format_no_trade(plan: dict) -> str:
    msg  = "🚫 *No Trade — Conditions Not Met*\n"
    msg += "─────────────────────\n"
    msg += f"{plan.get('reason', 'No valid setup found at this time.')}\n"
    suggestion = plan.get("suggestion")
    if suggestion:
        msg += f"\n💡 _{suggestion}_\n"
    msg += f"\n`{datetime.now(timezone.utc).strftime('%H:%M UTC')}` • /trade to try again"
    return msg


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
        f"{index}. {de} *{direction} {pair}*\n"
        f"   Entry `{entry}`  SL `{sl}`  Risk {risk}  ⭐{score}/10\n"
        f"   _{created}_"
    )
