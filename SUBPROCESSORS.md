# Sub-processors

**Product:** Stobox AI Community Assistant ("Stoby") — Telegram / Discord / web assistant
**Last updated:** 2026-07-16
**Maintainer:** Stobox · gd@stoboxplatform.com

This page lists the third-party sub-processors that may process **personal data** on
Stobox's behalf when operating the Stobox AI Community Assistant, as engaged under
GDPR Art. 28. It is derived from the services this application actually integrates with
in source. Sub-processors are engaged only where needed to deliver the service, and each
is bound by a data-processing agreement (DPA) with confidentiality and security terms.

> ⚠️ **Compliance note.** This document is generated from the codebase as a working
> register. Legal entity names, corporate locations, and DPA references below should be
> **verified by Stobox legal / the DPO against each vendor's current DPA** before this is
> relied upon externally. Which sub-processors are actually engaged depends on deployment
> configuration (see "Conditional on configuration" below).

---

## Active sub-processors

| Sub-processor | Service provided | Personal data processed | Location* |
|---|---|---|---|
| **Anthropic, PBC** | LLM inference (Claude) — default reasoning, classification, and generation | Message content submitted by community members; conversation context | USA |
| **OpenAI, L.L.C.** | LLM inference (fallback reasoning, `gpt-4.1`) **and** text embeddings (`text-embedding-3-large`) that index content into pgvector | Message content; knowledge-base text | USA |
| **Telegram (Telegram Messenger Inc. / FZ-LLC)** | Messaging platform the bot operates on | Telegram user IDs, usernames, display names, message content | International |
| **Discord, Inc.** | Alternate community channel (Discord adapter) | Discord user IDs, usernames, message content | USA |
| **Resend, Inc.** | Transactional email delivery — `/email` write-ups and lead (MQL) summaries to the team inbox | Recipient email address; message/summary content | USA |
| **Application hosting provider** (Render, Inc. — `render.yaml`; or Railway — `railway.json`) | Runtime hosting of the worker + web services; environment/secrets | All data in transit and at rest handled by the service | USA / EU (region-dependent) |
| **Managed PostgreSQL provider** (via `DATABASE_URL`; typically the hosting provider above) | Persistent storage — conversation memory, engagement/XP, captured leads, message log, knowledge index (pgvector) | Stored user identifiers, message history, lead details | USA / EU (region-dependent) |

\* *Location is indicative and must be confirmed against each vendor's DPA and the region
your instance is deployed in.*

---

## Conditional on configuration

These are engaged **only if** the corresponding option is configured; otherwise the
feature degrades gracefully and the sub-processor is not used.

| Sub-processor | When engaged | Notes |
|---|---|---|
| **OpenAI** | Always for embeddings; for reasoning only when the Anthropic primary fails over (`fallback_provider: openai`) | See above |
| **Resend** | Only when `RESEND_API_KEY` is set | If unset, email sending is disabled; leads are still captured |
| **Self-hosted SMTP provider** | Only when `SMTP_*` env vars are set instead of Resend | Provider is whatever SMTP host the operator configures |
| **CoinMarketCap** | Only when `COINMARKETCAP_API_KEY` is set (price-feed fallback) | Receives **no personal data** — see below |

---

## Not sub-processors (outbound data reads only)

The following third parties are contacted by the assistant but **do not process personal
data** — the bot only *reads* public data from them and transmits no user identifiers or
message content. They are listed here for transparency, not as Art. 28 sub-processors.

| Third party | Purpose | Data sent to them |
|---|---|---|
| **CoinGecko** (`api.coingecko.com`) | Live STBU price / market cap / volume for `/price` and `[FRESHNESS]` | Coin id only (`stobox-token`) — no personal data |
| **CoinMarketCap** (`pro-api.coinmarketcap.com`) | Price-feed fallback | Coin symbol/id only — no personal data |
| **Public blockchain RPC endpoints** (`eth.llamarpc.com`, `bsc-dataseed.binance.org`, `polygon-rpc.com`, `arb1.arbitrum.io`) | On-chain STBU balance lookup for `/check` | A **wallet address the user voluntarily provides** is sent to the public RPC. Wallet addresses may be personal data in some contexts; no other identifier is sent. |
| **GitHub API** (`api.github.com`) | Knowledge ingestion from Stobox public repos | None — outbound content fetch only |
| **stobox.io** | Knowledge crawl (docs/blog) | None — outbound content fetch only |

---

## Change process

When a sub-processor is added, removed, or changed:

1. Update this file and the **Last updated** date in the same pull request that introduces
   the integration.
2. Have Stobox legal / the DPO confirm the DPA is in place before the change ships.
3. Notify data-subjects / customers per the applicable DPA notice period where required.

For questions about this list or to exercise data-subject rights, contact
**gd@stoboxplatform.com**.
