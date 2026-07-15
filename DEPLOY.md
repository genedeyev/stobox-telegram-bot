# Deploying the Stobox bot (always-on)

For local testing see **[SETUP.md](SETUP.md)**. This is production: **Railway**
(24/7 worker) + **Supabase** (Postgres + pgvector) per ARCHITECTURE.md §6.

## Architecture on deploy

```
Supabase Postgres + pgvector  ◄── shared index ──►  Railway "stobox-bot" (worker, polling)
        ▲                                                    (answers Telegram + inline)
        └──── /api/reingest ◄── Railway "stobox-web" (optional: widget + self-update webhook)
```

- **stobox-bot** — the Telegram bot. A long-lived **worker** (Telegram long-poll),
  not a web server. This is the only service you strictly need.
- **stobox-web** — optional. Serves the chat-widget API and the HMAC
  `/api/reingest` webhook. Run it only if you want the widget or the deploy-time
  self-update. Point it at the **same `DATABASE_URL`** so both share the index.

## 1. Supabase (5 min)

1. Create a new Supabase project (a **dedicated** one — don't share the prod
   Compass DB, per §6).
2. **Enable pgvector:** Dashboard → Database → Extensions → enable **`vector`**.
3. Copy the connection string: Dashboard → Database → Connection string → **URI**
   (direct, port 5432): `postgresql://postgres:<password>@db.<ref>.supabase.co:5432/postgres`.
   That's your `DATABASE_URL`.

The app auto-creates its tables on first connect (`kb_chunks`, `user_profiles`,
`decision_log`) — no migrations to run.

## 2. Railway — the bot worker (5 min)

1. Railway → New Project → **Deploy from GitHub repo** →
   `genedeyev/stobox-telegram-bot`. It builds from the `Dockerfile`
   (`railway.json` sets the start command `python -m stobox_ai`).
2. Set **Variables** (Railway → service → Variables):

   | Variable | Value |
   |---|---|
   | `TELEGRAM_BOT_TOKEN` | from @BotFather |
   | `TELEGRAM_ADMIN_USER_IDS` | your numeric id(s) |
   | `ANTHROPIC_API_KEY` | reasoning |
   | `OPENAI_API_KEY` | embeddings |
   | `DATABASE_URL` | the Supabase URI from step 1 |
   | `STOBOX_ENV` | `production` |
   | `STOBOX_SYNC_ON_BOOT` | `1` (populate the index on first boot) |
   | `WEBHOOK_SECRET` | random string (only if using the web webhook) |
   | `GITHUB_TOKEN` | optional (higher GitHub sync rate limit) |
   | `STOBOX_VALUATION` | optional (current Eqvista mark for /valuation) |

3. Deploy. Watch the logs: you'll see the **preflight report**, then
   `index.ready`, then `telegram.start`. DM your bot to confirm.

> Because `DATABASE_URL` is set, knowledge/memory/leads **persist** across
> restarts. With `STOBOX_SYNC_ON_BOOT=1` the bot refreshes from stobox.io + GitHub
> on every boot (hash-gated, so unchanged content is skipped); the daily 04:00 UTC
> cron keeps it current after that.

## 3. (Optional) Railway — the web service

Add a second service from the same repo, override the start command to
`stobox-web`, and give it the same `DATABASE_URL` + `WEBHOOK_SECRET` +
`OPENAI_API_KEY`/`ANTHROPIC_API_KEY`. Railway assigns it a public URL — that's
your `BOT_REINGEST_URL` for the GitHub Action, and where the chat widget points.

Then wire the self-update loop: add
[deploy/stobox-v15-reingest.yml](deploy/stobox-v15-reingest.yml) to
`genedeyev/stobox-v15`, and set that repo's secrets `BOT_REINGEST_URL` +
`BOT_WEBHOOK_SECRET` (matching `WEBHOOK_SECRET`).

## 4. Verify

- Bot logs show `READY TO START ✅` and `telegram.start`.
- DM `/health` (admin) → chunk count > 0.
- If you ran the web service: `curl https://<web-url>/health` → `{"status":"ok",...}`.
- Run the compliance gate against production creds anytime: `stobox-golden`.

## Rollback / incident

- **Kill switch:** DM `/pause` (bot answers only static FAQ). `/resume` restores.
- **Bad deploy:** Railway → Deployments → redeploy a previous build.
- **Bad knowledge sync:** `/sync` re-pulls; canonicals (PR-gated) are unaffected.

## Other targets

- **Render:** `render.yaml` blueprint is included (worker + web).
- **Fly.io / Heroku:** use the `Procfile` (`worker:` / `web:`).
- **Docker Compose (single box):** `docker compose up --build` (bundles pgvector).
