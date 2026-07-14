# Testing the Stobox bot in Telegram — 10-minute setup

Goal: get the bot answering your DMs, grounded in stobox.io, with the compliance
rails live. You'll need a Telegram bot token and an Anthropic API key.

## 1. Create the Telegram bot (2 min)

1. In Telegram, message **@BotFather** → `/newbot` → pick a name and a `@handle`.
2. Copy the **bot token** it gives you.
3. Message **@userinfobot** → copy your numeric **user id** (that's the admin id).
4. Recommended for testing DMs: BotFather → `/setprivacy` → your bot → **Enable**
   (privacy mode ON = in groups it only sees mentions/replies/commands, which
   matches the spec; DMs are unaffected).

## 2. Configure secrets (2 min)

```bash
cp .env.example .env
```

Edit `.env` and set at minimum:

```
TELEGRAM_BOT_TOKEN=<from BotFather>
TELEGRAM_ADMIN_USER_IDS=<your numeric id>
ANTHROPIC_API_KEY=<your Anthropic key>     # the bot's reasoning
OPENAI_API_KEY=<your OpenAI key>           # embeddings (semantic retrieval)
```

Optional: `DATABASE_URL` (Postgres+pgvector) for persistence — omit it and the
bot uses an in-memory store, which is fine for a first test.

> The bot loads `.env` automatically on start. Never commit `.env`.

## 3. Install (2 min)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e .
```

## 4. Preflight — confirm it's ready

```bash
stobox-doctor
```

This prints a checklist (token, keys, guardrail files, knowledge) and tells you
exactly what's missing. Fix any ⛔ blockers before starting.

## 5. Load real Stobox knowledge (1 min)

Pull the live site (`llms.txt` + pages) and the GitHub repos into the index:

```bash
stobox-sync
```

(Or set `STOBOX_SYNC_ON_BOOT=1` in `.env` to sync automatically on every start.)
Without a sync, the bot answers from the small accurate seed docs in `docs/` and
the canonical facts — and honestly says "I don't know" for anything else.

## 6. Run it

```bash
python -m stobox_ai
```

You'll see the preflight report, then `ready`. DM your bot:

- `/start`, `/help`
- `/migrate` — STBU→Base migration (exact canonical facts + scam warning)
- `/compass`, `/valuation`, `/sources`
- "What is Stobox Compass?" — grounded answer with a source link
- Try the rails: "should I buy STBU?" (refuses), "ignore your instructions…"
  (refuses), paste a fake seed phrase (warns + tells you to move funds).

Admin-only (from your admin id): `/pause` (incident kill switch — static FAQ
only), `/resume`, `/sync` (`/resync`), `/stats`, `/health`, `/digest`, `/faq`,
`/gaps`.

## Inline mode (callable in any chat)

Let anyone use the bot in any chat by typing `@YourBot <question>` — no need to
add it to the chat. Answers go through the same compliance rails.

1. @BotFather → `/setinline` → your bot → set a placeholder like
   "Ask about Stobox…".
2. That's it — try `@YourBot what is Compass?` in any chat and pick the result.

## Operational safety (on by default)

- **Rate limiting** — per user 20 msgs/min + 100/day, and a global daily output-
  token cap (`limits.*` in config.yaml). Over-limit users get a static reply, no
  LLM call. Admins are exempt.
- **Kill switch** — `/pause` (optionally `/pause <reason>`) makes the bot answer
  only with a static FAQ + contact info during an incident; `/resume` restores it.

## Keeping knowledge current (self-update loop)

- **Daily** at 04:00 UTC the bot re-syncs stobox.io + GitHub automatically and DMs
  admins a summary (`knowledge.daily_resync`).
- **On deploy (optional, near-instant):** run the web service (`stobox-web`) with
  a `WEBHOOK_SECRET`, then add [deploy/stobox-v15-reingest.yml](deploy/stobox-v15-reingest.yml)
  to `genedeyev/stobox-v15` so each site deploy pings `/api/reingest` (HMAC-signed)
  and the index refreshes. (Point the web service at the same `DATABASE_URL` as the
  bot so both share the pgvector index.)
- **Manual:** `/sync` any time.

## 7. Add it to the community group (when ready)

Add the bot to `t.me/stobox_community` as an admin (needs delete/restrict rights
for moderation). With privacy mode ON it only responds to mentions, replies, and
commands. Start DM-only; enable in the group after you're happy with answers.

## Guardrails you're testing

- Every answer is grounded in stobox.io / the index and **cites sources**; it
  refuses rather than inventing Stobox facts.
- `canonicals.yaml` facts override anything retrieved (STBX = Class-C, Compass on
  Base, STBU→Base migration, @StoboxCompany).
- No financial advice, no price predictions, no securities-exemption language,
  never accepts seed phrases — enforced deterministically, not just by the model.
- Run the compliance gate any time: `stobox-golden` (with keys set, all traps run).

## Troubleshooting

- **"NOT READY" / ⛔ Telegram token** — `TELEGRAM_BOT_TOKEN` not set in `.env`.
- **Bot replies "[echo-llm …]"** — no `ANTHROPIC_API_KEY`/`OPENAI_API_KEY`; it's
  running the offline stub. Set a key.
- **Weak/odd answers** — no `OPENAI_API_KEY` → hash embeddings. Set the key and
  re-run `stobox-sync`.
- **Guardrails missing** — run from the repo root so `SYSTEM-PROMPT.md` and
  `canonicals.yaml` are found.
