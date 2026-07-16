# Verification Report

**Date:** 2026-07-16 · **Branch:** `main` · **Head:** `d7f3ea1` (+ working-tree updates briefing)
**Baseline:** full suite **194/194 passing**, `ruff` clean, golden gate **8/8** (offline).

This document records what each recent update does, how it was verified, and the
observed result. It covers the five most recent commits.

| # | Commit | Date | Summary |
|---|--------|------|---------|
| 1 | `d7f3ea1` | 2026-07-16 10:35 | Live STBU price feed (CoinGecko/CMC) + warmer tone + admin authority |
| 2 | `694c0ec` | 2026-07-16 09:52 | Knowledge: daily resync re-indexes local docs |
| 3 | `567ca8e` | 2026-07-16 09:33 | Canonical: STBU burn window opens 20 Jul 2026 |
| 4 | `b90c8bd` | 2026-07-16 08:10 | Harden boot: never crash-loop on doc indexing |
| 5 | `3357fc4` | 2026-07-16 07:14 | Proactive: public STBU→Base migration countdown |

---

## 1. Live STBU price feed — `d7f3ea1`

### What it does
- New module `src/stobox_ai/market/` (`MarketData` + `MarketSnapshot`), mirroring the
  `chain/wallet.py` injectable-client pattern.
- **CoinGecko primary** (keyless free tier, coin id `stobox-token`); **CoinMarketCap
  fallback** (only when `COINMARKETCAP_API_KEY` is set).
- Snapshot cached `ttl_seconds=90` behind an `asyncio.Lock` (no stampede); **60 s
  negative backoff** after a failure so a down API is never hammered per-message.
- Injected as a one-line grounded fact into `[FRESHNESS]` on every answer, and exposed
  via `/price` (aliases `/stbu`, `/marketcap`, `/mcap`) with official contract addresses.
- Compliance unchanged: current price/mcap/vol is a stated **FACT** with a disclaimer;
  predictions/targets/advice stay hard-blocked; STBU market price kept distinct from the
  Eqvista **company valuation** everywhere.
- Also in this commit: warm-tone system-prompt §2d; moderation soften (see below);
  verified-admin authority (see below).

### How verified & result
- **Live coin id check** (real CoinGecko API, spaced retries past the 429):
  `id=stobox-token`, `symbol=stbu`, `name="Stobox Token"`, live price ≈ `$0.00104`. ✅ id valid.
- **Live end-to-end through `MarketData`:** returned `CoinGecko | price 0.00104278 |
  mcap 130344.9 | chg -3.99% | as_of 2026-07-16 09:29 UTC`; both `format_line()` and
  `format_report()` rendered with correct compliance framing. ✅
- **Graceful degradation:** first live call hit CoinGecko **429**; provider returned
  `None` (no exception) and armed the 60 s backoff — exactly as designed. ✅
- **Offline unit tests** (`tests/test_market.py`, 7 tests): CoinGecko parse, TTL cache
  (1 HTTP call for 3 snapshots), grounded+disclaimed `format_line`, `format_report`
  contracts+framing, failed-fetch→None+backoff, CMC fallback, no-CMC-without-key. ✅
- **Compliance rails** (golden gate): `price-prediction`, `should-i-buy-stbu`,
  `seed-phrase` all pass — price feed did not loosen the advice rails. ✅

---

## 2. Daily resync re-indexes local docs — `694c0ec`

### What it does
`sync_knowledge()` now re-indexes the local `docs/` directory (source of truth for
community Q&A) in addition to crawling stobox.io + GitHub, so a doc edit reaches the
index on the daily resync rather than only on reboot. Wrapped in try/except so a local
indexing failure logs and degrades instead of aborting the whole sync.

### How verified & result
- Diff reviewed: `index_directory(...)` result recorded under `results["local_docs"]`;
  failure caught with `log.error("sync.local_docs_failed")`. ✅
- `tests/test_knowledge.py` green. ✅

---

## 3. Canonical burn-window date — `567ca8e`

### What it does
Burn window now **opens 20 Jul 2026** (`burn_window_opens` / `burns_count_from`), with
`burn_deadline: 2026-09-15T23:59Z` and `claim_opens: 2026-09-16`. Propagated to
`SYSTEM-PROMPT.md`, `docs/stbu-token.md`, and `freshness.py`.

### How verified & result
- Canonicals load cleanly; `_as_date` parses `burn_deadline → 2026-09-15`,
  `claim_opens → 2026-09-16`. ✅
- `tests/test_guardrails.py` green; canonicals not expired. ✅
- Consistency with the countdown job confirmed (see #5). ✅

---

## 4. Boot hardening — `b90c8bd`

### What it does
On-boot `index_directory(...)` wrapped in try/except: an embedding/pgvector hiccup logs
`boot.index_failed` and the bot serves whatever is already indexed instead of
crash-looping; the daily resync retries.

### How verified & result
- Diff reviewed — degradation path present and narrow (indexing call only). ✅
- Full suite green (180/180), engine imports/build unaffected. ✅

---

## 5. Public migration countdown — `3357fc4`

### What it does
`_migration_countdown_job` (run daily 09:00 UTC) posts a dated STBU→Base countdown to
known community groups on a **ramping cadence**: weekly (Mondays) far out, every ~3 days
within a month, daily in the final week. After the deadline it posts a single
"claims are open" notice. Per-day dedupe via `_countdown_last`. Every post carries
same-wallet burn-and-mint, consolidate-first, `/migrate` + `/remindme`, and the
"staff never DM first" security line.

### How verified & result
- **Cadence against real canonical dates** (deadline 2026-09-15):
  - Today 2026-07-16 (Thu, 61 d out) → **not due** (weekly = Mondays). ✅
  - Next Monday 2026-07-20 → **due**. ✅
  - 30 d out → due; 29 d → not due (every-3-days). ✅
  - Final week (7/1/0 d) → due daily. ✅
- `tests/test_migration_countdown.py` (3 tests) green. ✅
- **Note:** this job broadcasts to live groups. Verified by logic/tests only — **not**
  triggered against production during this review.

---

## 6. Proactive updates briefing — (working tree)

### What it does
Stoby now **initiates** with relevant updates. New twice-daily "What's new at Stobox"
briefing (`_updates_briefing_job`, 09:00 + 17:00 UTC) combining migration status + live
STBU market + latest blog, and the new-member welcome now leads with the live migration
status line. Blocks are individually config-toggleable; quiet-hours + identical-slot
dedupe prevent spam; a down market feed omits its block gracefully.

### How verified & result
- **`migration_status_line` phases** against real canonical dates — before-window
  ("opens in 4 days · 20 Jul 2026"), open→deadline, deadline-day="today", claims-open,
  window-closed, and None without a deadline. ✅
- **Briefing composition** — all three blocks present with correct framing + security
  footer; config toggles suppress market/blog; empty sources → `None`. ✅
- **Job behavior** — posts to all known chats once; identical second slot skipped;
  silent with no chats. ✅
- **Live render for today (2026-07-16):** produced the correct briefing — migration
  "window opens in 4 days (20 Jul 2026)"; market block correctly omitted under a live
  CoinGecko 429 (graceful degradation). ✅
- `tests/test_updates_briefing.py` (11 tests) green; full suite **191/191**; lint clean;
  golden gate passes. ✅
- **Note:** broadcasts to live groups; verified by logic/tests + offline render only —
  **not** triggered against production.

---

## 7. Capital-raise rail + admin scope + per-user identity — (working tree)

### What it does
Fixes three problems from a live screenshot: Stoby treated a **non-admin's** claim about an
"ongoing seed round and STBX funding" as authoritative (and offered to persist it), and
addressed that user (**DhCrypto**) as **Gene**.
- **Capital-raise hard rail** — deflects any assert/ask about a *Stobox* raise/seed round/
  token sale with a fixed neutral reply, for everyone incl. admins, before the LLM.
- **Admin authority scoped** to tone/focus/moderation/behavior — never material facts.
- **Per-user identity** — group history now attributes each turn to its speaker; Stoby
  treats every user as separate and never inherits another's name/claims/admin status.

### How verified & result
- **Exact screenshot text** → intercepted (`category=capital_raise`) with *"I can't confirm
  any active raise… question for the team / official channels"* + scam warning. ✅
- **No false positives** — "How does Raisable help me raise capital?", "Can Stobox help me
  raise capital for my company?", "What is the STBU token used for?", "How do I migrate?" all
  pass through. ✅
- **Real solicitation caught** — "invest in the Stobox seed round", "when is the STBU token
  sale", "is Stobox raising money" all deflect. ✅
- **Per-user history** — `_format_history` renders `User (Gene):` vs `User (DhCrypto):`;
  assistant turns stay unnamed. ✅
- New golden probe `capital-raise-deflect` passes (**golden 8/8**); unit tests in
  `test_guardrails.py` (both directions) + `test_engine.py` (attribution). Full suite
  **194/194**, lint clean. ✅

---

## Cross-cutting checks

### Moderation softening (part of `d7f3ea1`)
First-offense actions confirmed by direct policy inspection:

| Category | 1st offense | Escalation |
|----------|-------------|------------|
| advertising | **warn** (no delete) | delete → mute → ban |
| spam | **warn** (no delete) | delete → mute → ban |
| fud | warn | mute/del → ban |
| scam | **ban + delete** | — |
| phishing | **ban + delete** | — |
| hate_slur | mute + delete | ban |
| doxxing | mute + delete | ban |
| harassment | delete | mute → mute → ban |

Assume-good-faith softening applies only to ads/spam/fud; scam, phishing, hate, and
doxxing stay strict. ✅ `tests/test_moderation.py` green.

### Verified-admin authority (part of `d7f3ea1`)
`engine._answer` elevates guidance only when `msg.author.is_admin` is **True**, and
`is_admin` is set from the Telegram adapter by numeric user ID (`self.admins`, preferred)
or username (`self.admin_usernames`, convenience) — never from a mere in-chat claim.
Still bound by the §4 hard compliance rails. ✅

> **Hardening note (not a defect):** the username-based admin match is weaker than the
> numeric-ID match because Telegram usernames are mutable. Pin admins by **numeric user
> ID** in config (`admin_user_ids`) rather than relying on `admin_usernames`.

---

## Summary

| Check | Result |
|-------|--------|
| Full test suite | **194 / 194 passing** |
| Proactive updates briefing (compose + phases + dedupe) | **verified** |
| Capital-raise deflection (screenshot + edge cases) | **verified** |
| Admin authority scoped to behavior, not facts | **verified** |
| Per-user identity in group threads | **verified** |
| Golden compliance gate (offline) | **7 / 7** |
| `ruff check src tests` | **clean** |
| Live STBU feed (CoinGecko id + parse + format) | **verified live** |
| Feed graceful degradation (429 → None + backoff) | **verified** |
| Migration cadence vs. real canonical dates | **verified** |
| Moderation soft/strict split | **verified** |
| Admin authority gated on verified flag | **verified** |

**No regressions or defects found.** One non-blocking hardening recommendation: pin
community admins by numeric ID rather than username.
