
# AI Penny Scanner — GitHub Actions (Discord Alerts)

This repo runs a daily scanner and posts a concise list of penny stock candidates to a Discord channel.

## Quick Start
1. Create a new GitHub repo (private is fine).
2. Add these files:
   - `scanner.py`
   - `requirements.txt`
   - `.github/workflows/scan.yml`
3. In your repo: **Settings → Secrets and variables → Actions → New repository secret**
   - Name: `DISCORD_WEBHOOK_URL`
   - Value: your Discord webhook URL
4. Commit and push. The workflow will run at **14:35 UTC** Monday–Friday and post to Discord.
   - You can also run it anytime via **Actions → Penny Scan to Discord → Run workflow**.

## Adjustments
- Edit thresholds in `scan.yml` env block or inside `scanner.py`.
- CSV artifacts are attached to each workflow run under **Actions → (run) → Artifacts**.
