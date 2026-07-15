# Stoby — the resident AI of the Stobox community

> *"Stoby — the resident AI of the Stobox community. Part monster, part mind, fully awake.
> Ask me anything about tokenization."*

Stoby is an enterprise-grade, compliance-gated community AI for Telegram. It answers from
stobox.io's published content (with citations), defends the brand, grows and retains the
community, qualifies leads, moderates, and keeps itself up to date — **running 24/7 in
production** (Railway + Supabase), no human in the loop for day-to-day operation.

**Status: 🟢 LIVE.** Handle `@stobox_assistant_bot` · display name "Stoby | AI Assistant".

- **Setup / run locally:** [SETUP.md](SETUP.md)
- **Deploy (Railway + Supabase):** [DEPLOY.md](DEPLOY.md)
- **What's planned:** [ROADMAP.md](ROADMAP.md)
- **Compliance spec:** [SYSTEM-PROMPT.md](SYSTEM-PROMPT.md) · [canonicals.yaml](canonicals.yaml) · [ARCHITECTURE.md](ARCHITECTURE.md)

---

## What's live

### 🧠 Answers you can trust
- **Grounded RAG with citations** — every Stobox fact comes from `canonicals.yaml`, the live
  [FRESHNESS] state, or retrieved docs; never invented. Below a confidence threshold Stoby
  says *"I don't know based on the current documentation"* and flags it, rather than guessing.
- **Three-block compliance prompt** — `[CORE]` behavior + `[CANONICALS]` facts (PR-gated) +
  `[FRESHNESS]` (live date, migration phase, valuation, latest posts). Precedence:
  **CANONICALS > FRESHNESS > retrieved**.
- **Deterministic rails** — no financial advice, no price predictions, no securities-exemption
  language, never accepts seed phrases/keys, proactive anti-impersonation warnings, prompt-
  injection resistance, forbidden-claim blocking. Enforced in code, not just the prompt.
- **Golden-question gate** — a trap suite (`evals/golden.yaml`) that must pass before any
  prompt/index change ships; runs in CI.
- **English-only, human, adaptive** — understands any language, always replies in English;
  short answers with **📖 More detail** / **📩 Email me this** progressive disclosure; adapts
  depth to the user; warm and expressive, sober on compliance topics.

### 📚 Knowledge that updates itself
- Ingests **stobox.io `llms.txt`/`llms-full.txt`**, crawls the site, and pulls the
  **StoboxTechnologies GitHub repos** — plus the private **Community Q&A register**.
- **Self-updating:** daily 04:00 UTC reconciliation + an HMAC `/api/reingest` webhook +
  a hot-reload docs watcher. New blog posts are announced automatically with OG-image cards.

### 🌱 Growth & 🔁 retention
- **New-member welcome**, **deep-link attribution** (`?start=blog|x|ref_<id>`), **referral
  tracking**, **share buttons**, **email follow-up** (`/email`).
- **Engagement engine** — XP, daily streaks, levels (Newcomer → Community OG), **weekly
  leaderboard**, **native quiz nights** (auto-scored), **AMA collector** (crowd-ranked).
- **Opt-in migration reminders** (`/remindme`) counting down to the Sep 15 deadline.

### 🛡 Moderation (Stoby is a group admin)
- 4 layers: deterministic filters (slurs, doxxing, scams, flood) + LLM classifier + **strike
  ledger** (30-day decay) + **severity policy** (scam = instant ban, hate = mute→ban,
  harassment = delete→mute→ban; **honest criticism never touched**).
- **Impersonation defense** (fake "Stobox Support" → alert or ban), **mod-log** with one-tap
  Pardon/Ban, offender DMs with `/appeal`.

### 💰 Leads & conversion
- Buying-intent detection, lead scoring, **on-chain wallet migration checker** (`/check` reads
  STBU balances across chains, read-only), CRM handoff (`source=telegram-bot`).

### 🤖 Runs itself + keeps you in control
- **Unanswered-question loop** — Stoby captures what it can't answer, proposes a draft, DMs
  admins; you tap `/approve` or `/answer`, and it replies to everyone who asked + saves the
  wording to the register.
- **Daily digest**, **weekly FAQ**, **documentation-gap** detection, full **decision log**.
- **Ops safety:** per-user rate limiting + global spend cap + `/pause` kill switch.

### 🔌 Channel-agnostic core
Telegram, a Web/HTTP API, and Discord are three adapters over the same engine (proven by a
same-engine multi-channel test).

---

## Commands

**Everyone**
`/guide` (interactive tour) · `/migrate` · `/check <address>` · `/compass` · `/valuation` ·
`/blog` · `/sources` · `/rank` · `/leaderboard` · `/ama <question>` · `/remindme` ·
`/email <addr>` · `/contact` · `/report` · `/feedback` · `/about` · `/help`

**Admins** (allowlisted via `TELEGRAM_ADMIN_USER_IDS`)
- Knowledge/ops: `/sync` `/reindex` `/stats` `/health` `/digest` `/faq` `/gaps` `/pause`
  `/resume`
- Unanswered-question loop: `/pending` `/answer <id> <text>` `/approve <id>`
- Moderation (reply to a user): `/warn` `/mute [min]` `/unmute` `/ban` · `/unban <id>`
  `/strikes` `/clearstrikes` `/modstats`
- Engagement: `/quiz` · `/amaopen [topic]` `/amaclose` `/amalist` `/amaclear`

---

## Architecture (brief)

```
Telegram / Web / Discord (adapters)
        │
   AgentEngine (channel-agnostic)
        ├─ guardrails/   3-block prompt · canonicals · rails · golden gate
        ├─ knowledge/    ingest · chunk · pgvector · hybrid retrieval · sources · sync
        ├─ moderation/   filters · classifier · strikes · policy
        ├─ engagement/   xp · quiz · ama
        ├─ qa/           unanswered-question loop → register
        ├─ ops/          rate limit · reminders · email · webhook
        ├─ chain/        on-chain STBU wallet checker
        ├─ leads/ · insights/ · analytics/ · memory/
        └─ agents/       intent router · confidence
```

Postgres + pgvector in production (Supabase); in-memory fallback for local dev. Persisted
state (strikes, XP, reminders, question queue) lives under `/app/data` (Railway volume).

---

## Develop & test

```bash
pip install -e ".[dev]"
stobox-doctor          # preflight: what's configured / missing
pytest -q              # 85 offline tests
stobox-golden          # compliance gate (needs API keys for the full run)
ruff check src evals tests
```

Console scripts: `stobox-bot` (run) · `stobox-web` · `stobox-doctor` · `stobox-sync` ·
`stobox-golden`. `scripts/set_identity.py` sets Stoby's Telegram name/description/menu.

---

## What's planned

See **[ROADMAP.md](ROADMAP.md)**. Highlights: AXIS pre-qualifier in chat, case-study &
jurisdiction matcher, real Twenty CRM connector, topic subscriptions, win-back, content
flywheel, sentiment alarm, analytics dashboard, multimodal ingestion, Slack adapter.

---

## Safety & compliance

Stoby represents a regulated-securities issuer. It never invents roadmap/tokenomics/pricing/
partnerships, distinguishes documentation from opinion, gives no financial or legal advice,
and treats every wallet-adjacent conversation as a potential scam. `canonicals.yaml` is
changed only by human-reviewed PR — the auto-sync pipeline updates *data*, never *claims*.
