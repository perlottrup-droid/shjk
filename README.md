# Sidequest — Telegram social mini app

Sidequest is a small social app for Telegram with a shared feed, posts, likes, replies, and follows. The free-tier deployment uses Vercel Functions for the API and Supabase Postgres for shared data.

## Free-tier notes

- Vercel Hobby is free for personal, non-commercial projects and includes automatic HTTPS.
- Supabase Free currently includes a 500 MB Postgres database. Free projects with very little database activity can be paused after a week, and can be restored from the dashboard.
- These plans are good for a hobby app and may have downtime or usage limits. See the provider plans before inviting a large audience.

## Deploy

1. Create a Supabase Free project.
2. In Supabase SQL Editor, run `supabase/schema.sql` once.
3. Copy the project's URL and service-role key from Supabase API settings.
4. Import this GitHub repository into Vercel and deploy with the default settings. Vercel detects the static frontend and `api/[...path].js` function.
5. In Vercel Project Settings → Environment Variables, add:
   - `SUPABASE_URL`
   - `SUPABASE_SERVICE_ROLE_KEY`
   - `TELEGRAM_BOT_TOKEN`
6. Redeploy after setting the environment variables.
7. In Telegram, create a bot with `@BotFather`. Use `/setmenubutton`, select the bot, choose a button title such as **Open Sidequest**, and set the URL to the Vercel HTTPS deployment URL.

Keep the Supabase service-role key and Telegram bot token in Vercel's secret settings. Never put either in browser code or commit them to GitHub.

## Local preview

The static preview still works in a browser and uses local demo data. To run the bundled local Python preview, set `TELEGRAM_BOT_TOKEN` and run `python server.py`; browser-only use does not need the token. The deployed Vercel API uses Supabase and does not run `server.py`.

## Current scope

Profiles are based on verified Telegram launch data. Shared posts, likes, comments, and follows are stored in Supabase. This starter does not include image uploads, moderation, blocking/reporting, or abuse rate limits.

