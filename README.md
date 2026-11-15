# Shortsmith
1. Install ffmpeg (https://ffmpeg.org) and Python 3.10+
2. `pip install -r requirements.txt`
3. Optional, for smarter clip picking: `export ANTHROPIC_API_KEY=...`
4. `python app.py` then open http://localhost:5000

Env vars: WHISPER_MODEL (tiny/base/small), CLAUDE_MODEL.
Only process videos you own or have rights to reuse.

## Deploy (VPS + Docker + free HTTPS)
1. Create a 4 GB RAM Ubuntu server, point a DNS A record (e.g. shorts.yourdomain.com) at its IP.
2. `curl -fsSL https://get.docker.com | sh`
3. Upload this folder, `cp .env.example .env` and edit DOMAIN / ANTHROPIC_API_KEY.
4. If YouTube blocks downloads ("Sign in to confirm you're not a bot"), export cookies.txt from a
   logged-in browser (a throwaway account) and save it as `cookies/cookies.txt`.
5. `docker compose up -d --build`  -> https://your-domain
Update yt-dlp regularly: `docker compose build --no-cache && docker compose up -d`
