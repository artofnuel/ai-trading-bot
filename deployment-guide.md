# VPS Deployment Guide — Updating the Bot

> **Context:** Your VPS runs the bot as a `systemd` service (`tradingbot.service`).
> This guide covers pushing the v2 upgrade from your local machine to GitHub,
> then pulling and restarting on the Google Cloud VPS.

---

## Step 1: Commit & Push to GitHub (Local)

```bash
# Stage all changed files (excluding generated docs / .env)
git add config.py main.py market/prices.py ai/analyst.py bot/handlers.py bot/keyboards.py bot/formatter.py db/database.py

# Commit
git commit -m "v2 upgrade: Forex-only, ICT prompts, ATR/PDH enrichment, pip table, math validation"

# Push
git push origin main
```

> **Note:** If `codebase-documentation.md` or similar generated files are present,
> make sure they are **not** staged (add them to `.gitignore` if needed).

---

## Step 2: SSH Into the VPS

```bash
ssh inuajnr@YOUR_VPS_IP
```

Replace `YOUR_VPS_IP` with your actual Google Cloud VM external IP.

---

## Step 3: Pull the Latest Code

```bash
cd /home/inuajnr/trade-bot
git pull origin main
```

---

## Step 4: Install Any New Dependencies

The `requirements.txt` hasn't changed in v2, but run this to be safe:

```bash
source venv/bin/activate
pip install -r requirements.txt
```

---

## Step 5: Update `.env` File

**New in v2:** `TWELVE_DATA_API_KEY` is now **required** (the bot validates it at startup).

Edit the `.env` file on the VPS:

```bash
nano /home/inuajnr/trade-bot/.env
```

Ensure it has all three keys:

```
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key
TWELVE_DATA_API_KEY=your_twelve_data_api_key
```

> **Get a Twelve Data API key:** Sign up at [twelvedata.com/apikey](https://twelvedata.com/apikey) (free tier: 8 req/min).

---

## Step 6: Restart the systemd Service

```bash
sudo systemctl restart tradingbot
```

---

## Step 7: Verify the Update

```bash
# Check service is running
sudo systemctl status tradingbot

# Tail live logs — look for "Starting AI Trade Planner Bot…"
sudo journalctl -u tradingbot -f
```

Expected log line on successful startup:

```
2026-05-10 HH:MM:SS | INFO    | __main__ — Bot is running. Press Ctrl+C to stop.
```

---

## Rollback (If Needed)

If something goes wrong, revert to the previous version:

```bash
cd /home/inuajnr/trade-bot
git log --oneline -10          # find the previous commit hash
git checkout <PREVIOUS_HASH>   # replace with actual hash
sudo systemctl restart tradingbot
sudo journalctl -u tradingbot -f
```

---

## Quick Reference — Common systemd Commands

| Command | Purpose |
|---|---|
| `sudo systemctl restart tradingbot` | Restart the bot |
| `sudo systemctl stop tradingbot` | Stop the bot |
| `sudo systemctl start tradingbot` | Start the bot |
| `sudo systemctl status tradingbot` | Show status & recent logs |
| `sudo journalctl -u tradingbot -f` | Follow live logs |
| `sudo journalctl -u tradingbot --since "5 min ago"` | Show last 5 minutes |
