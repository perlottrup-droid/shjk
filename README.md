# Sidequest — Telegram social mini app

Sidequest is a small, mobile-first social app that opens inside Telegram. It has a shared feed, posts, likes, replies, follows, Telegram profile sign-in, and SQLite persistence. The backend uses Python's standard library; no Python packages are required.

## Run locally

1. Install Python 3.10 or newer.
2. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` to the token from [@BotFather](https://t.me/BotFather). Keep the token on the server and out of browser code.
3. In PowerShell, set the variables and run the app:

   ```powershell
   $env:TELEGRAM_BOT_TOKEN = "your-bot-token"
   $env:PORT = "8000"
   python server.py
   ```

4. The browser-only preview is at `http://localhost:8000`. It uses local demo data. Authenticated shared features work when opened through Telegram using an HTTPS URL.

## Deploy to Render

1. Push this app folder to a GitHub repository.
2. In Render, create a **Blueprint** from that repository and select `render.yaml`.
3. Set the secret `TELEGRAM_BOT_TOKEN` in the Render service environment. Do not commit the actual token.
4. Deploy. The service has a persistent disk mounted at `/var/data`, so its SQLite database survives restarts and deploys.
5. Copy the service's HTTPS URL for the BotFather setup below.

The provided Render configuration is a starting point. Other hosts work if they provide HTTPS, persistent storage for SQLite, and run `python server.py` with `TELEGRAM_BOT_TOKEN` and `DATABASE_PATH` set.

## Connect the Telegram bot

1. In Telegram, open [@BotFather](https://t.me/BotFather), create a bot with `/newbot`, and copy its token into the hosting provider's secret settings.
2. Run `/setmenubutton`, select the bot, choose a button title such as **Open Sidequest**, and set the URL to the deployed HTTPS URL.
3. Open the bot and tap **Open Sidequest**. Telegram supplies signed launch data; the server validates it before identifying the user.

You can also configure a Main Mini App with `/newapp` in BotFather. Use the deployed URL and share the resulting `t.me` link. The Menu Button is the quickest launch path.

## Security and scope

- The browser sends `Telegram.WebApp.initData` to the backend. The backend verifies Telegram's HMAC and rejects launch data older than 24 hours. `initDataUnsafe` is only used to show a provisional name before the server response; it is never trusted for API authorization.
- Posts, likes, replies, follows, and Telegram user profiles are shared across users of this single deployment.
- This is a small starter social network. It does not include image uploads, moderation tools, blocking/reporting, password sign-in, or production-grade abuse controls. Keep the app limited to a trusted group until those are added.
- SQLite is suitable for a modest single-instance community. A larger service should move to a managed PostgreSQL database and add rate limiting, moderation, backups, and operational monitoring.

## API

- `GET /api/me`, `/api/feed`, `/api/people`
- `POST /api/posts`, `/api/posts/{id}/like`, `/api/posts/{id}/comments`, `/api/people/{id}/follow`
- `GET /api/posts/{id}/comments`
- `GET /health` for hosting health checks

