# Stoby — build roadmap

Stoby is the resident AI of the Stobox community. This tracks what's shipped and
what's planned. "Shipped" = in `main`, tested, golden-gated.

> **🟢 LIVE in production** — Railway (worker) + Supabase (pgvector), running
> 24/7. Handle `@stobox_assistant_bot`, display name "Stoby | AI Assistant".
> Persisted state on an `/app/data` volume. Waves 1 & 2 complete.

## ✅ Shipped

**Core** — grounded RAG with citations, 3-block compliance prompt (CORE +
CANONICALS + FRESHNESS), deterministic rails, golden-gate regression suite,
confidence gating, self-updating knowledge (stobox.io llms.txt + site crawl +
GitHub), hot-reload docs watcher.

**Conversation** — human/emotional v3 answers, English-only, adaptive depth,
**short answers with progressive disclosure** (📖 More detail / 📩 Email me this
/ 💬 Continue in DM buttons), instant "checking the docs…" placeholder, share
buttons, community-QA canonical answers, **interactive /guide** (button-navigated
user tour), Stoby identity (name/description/command-menu set via Bot API).

**Community & growth** — new-member welcome, deep-link attribution + referral
tracking (`/stats`), blog auto-announcements (OG cards), native polls, share
nudges, unanswered-question loop (capture → notify → `/answer`/`/approve` →
deliver, mirrored to stobox-v15 register), opt-in migration reminders
(`/remindme`), **email follow-up** (`/email` → detailed write-up via SMTP or
CRM lead handoff).

**Moderation** — 4-layer stack (deterministic filters + LLM classifier + strike
ledger + severity policy), impersonation defense, mod-log with Pardon/Ban
buttons, admin commands (`/warn /mute /ban /strikes /modstats`…), `/appeal`.

**Ops** — rate limiting + spend cap, `/pause` kill switch, self-update webhook +
daily cron, preflight doctor, Railway + Supabase deploy (LIVE).

## 🔜 Planned

### Wave 2 — the Stobox-specific engagement engine  ✅ COMPLETE
- ✅ **On-chain wallet migration checker** — SHIPPED. `/check 0x…` (or just paste
  an address) reads the 4 eligible STBU contracts across Ethereum/BNB/Polygon/
  Arbitrum via public RPC → per-chain balances + exact migration path. Read-only,
  never touches keys; a pasted private key triggers a compromise warning.
- ✅ **AMA collector** — SHIPPED. /amaopen [topic] announces to groups; members
  submit with /ama (similar questions merge = implicit upvote); everyone upvotes
  with a 👍 button (toggle); /amalist + /amaclose hand you a vote-ranked list.
  `engagement/ama.py`. Zero-effort AMA prep.
- ✅ **Quiz nights + XP / streaks / leaderboard** — SHIPPED. Native Telegram quiz
  polls (auto-scored via PollAnswer, +10 XP for correct), XP for helpful
  questions/referrals/daily streaks, levels (Newcomer→Community OG), /rank +
  /leaderboard (weekly), admin /quiz. `engagement/xp.py`.
- **Referral leaderboard** — monthly recognition on top of the existing ref
  tracking; measurable K-factor.

### Wave 3 — revenue & retention depth
- **AXIS pre-qualifier in chat** — 5 readiness questions → mini-score → warm
  lead → discovery-call link.
- **Case-study & jurisdiction matcher** — match the asker's profile to the right
  guide + case studies.
- **Twenty CRM** — finish the real connector so qualified leads land in pipeline
  with the conversation summary.
- **Topic subscriptions** (`/subscribe migration|rwa-news|product`),
  **win-back** DMs (14-day inactive, opt-in only).

### Wave 4 — ops polish
- **Content flywheel** — top unanswered themes → auto-drafted blog outlines as
  stobox-v15 issues.
- **Real-time FUD/sentiment alarm** (immediate, not just the daily digest).
- **Analytics dashboard UI** (data already served at `/insights/*`).

### Requested backlog (this session)
- ✅ Shorter answers + ask-if-more + DM offer + email offer — **shipped**.
- Multimodal ingestion of inbound images/voice.
- Discord / Slack adapters (interface ready).
