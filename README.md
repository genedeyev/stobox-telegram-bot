# Stobox AI — Telegram Community Manager

An enterprise-grade, **channel-agnostic AI community-manager platform**. Telegram
is implemented as one adapter over a reusable RAG + agent core, so Discord,
Slack, a web widget, or a voice agent can reuse 100% of the reasoning later.

It ingests Stobox documentation, answers questions with **inline citations** and
a **confidence gate** (it says *"I don't know based on the current documentation"*
rather than hallucinating), remembers users across conversations, moderates
spam/scams, qualifies sales leads, and proactively educates the community.

> **Status:** runnable vertical slice — ingest → RAG → cited Telegram answers →
> memory → moderation → leads → analytics → evals — with clean interfaces for the
> broader spec. See [Roadmap](#roadmap) for what's stubbed vs. complete.

---

## Architecture

```
Telegram (adapter)  ─┐
Web / HTTP (adapter) ─┼─►  AgentEngine (channel-agnostic core)
Discord (adapter)    ─┤         │
Slack / … (future)   ─┘         │
                                ├─ IntentRouter      (mode · persona · language · intent)
                                ├─ HybridRetriever   (BM25 + vector + rerank + multi-hop)
                                ├─ ConfidenceEngine  (anti-hallucination gate + citations)
                                ├─ Moderator         (spam/scam/FUD/toxicity/flood)
                                ├─ MemoryStore       (conversation + long-term profiles)
                                ├─ LeadQualifier     (scoring + CRM handoff)
                                └─ DecisionLog       (full audit trail + analytics)

Knowledge:  docs/ ──► Ingest ──► SemanticChunker ──► Embeddings ──► VectorStore (pgvector)
                        ▲                                                │
                        └────────── DocsWatcher (hot re-index) ──────────┘
```

Everything is replaceable behind an interface: **LLM providers** (`llm/base.py`),
**embeddings**, **vector store** (`knowledge/store.py`), **memory**, **channels**
(`channels/base.py`). Reasoning defaults to **Anthropic Claude**; OpenAI is a
drop-in swap. Embeddings default to **OpenAI** and feed **pgvector**.

## ▶ Test it in Telegram

See **[SETUP.md](SETUP.md)** for the 10-minute runbook (BotFather → `.env` →
`stobox-doctor` → `stobox-sync` → `python -m stobox_ai`). `stobox-doctor` tells
you exactly what's configured and what's missing before you launch.

## Quick start (local, no infra)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env         # add TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY, OPENAI_API_KEY
python -m stobox_ai          # boots, indexes docs/, starts polling
```

Without a `DATABASE_URL` the app uses an **in-memory vector + memory store**, and
without API keys it uses **offline stub providers** — so it always boots and the
test/eval suites run in CI without secrets.

## Production (Postgres + pgvector)

```bash
cp .env.example .env         # fill secrets
docker compose up --build    # postgres(pgvector) + bot, docs mounted read-only
```

## Channels (the platform is genuinely channel-agnostic)

Telegram, a Web/HTTP API, and Discord are three adapters over the **same**
`AgentEngine`. A test (`tests/test_channels.py`) drives one engine instance from
two channels and asserts both get cited answers from the same knowledge base —
so channel-agnosticism is verified, not just claimed.

```bash
# Telegram (default)
python -m stobox_ai

# Web chat API / widget backend  (pip install -e ".[web]")
stobox-web                      # POST /chat, GET /health on :8080
#   curl -s localhost:8080/chat -d '{"user_id":"u1","text":"What is Compass?"}'
#   demo UI: open examples/web-widget.html

# Discord  (pip install -e ".[discord]", set DISCORD_BOT_TOKEN)
python scripts/run_discord.py
```

Adding a channel = implementing the tiny `Channel` contract in
[`channels/base.py`](src/stobox_ai/channels/base.py) (native event →
`IncomingMessage`, render `AgentResponse`). No engine changes.

## Configuration

Non-secret config lives in [`config/config.yaml`](config/config.yaml) (models,
temperature, retrieval weights, confidence threshold, moderation ladder,
languages, proactive intervals, rate limits — all the spec's "Configurable"
knobs). Secrets come only from env/vault. YAML supports `${VAR:-default}`
interpolation. **Prompts** are versioned YAML under
[`config/prompts/`](config/prompts/) with built-in A/B bucketing — never
hardcoded.

## Where the bot's knowledge comes from

The bot has **no built-in knowledge** — it answers only from an indexed corpus,
retrieved per question and cited. Three ways content gets in:

1. **Local files** — drop Markdown / PDF / DOCX / HTML / TXT into `docs/`.
   Markdown supports YAML front-matter (title, version, author, date, category,
   product, language, visibility, confidence, source_url). The watcher
   re-parses/chunks/embeds/indexes **without a restart**. Manual: `stobox-ingest
   --rebuild` or `/reindex`.
2. **stobox.io `llms.txt` / `llms-full.txt`** — the site's curated, AI-authored
   reference (the [llms.txt standard](https://llmstxt.org)). This is the
   canonical, highest-confidence source, ingested as-is.
3. **stobox.io (web crawl)** — discovers its page inventory from
   `llms-full.txt` (the site has no sitemap), else polite BFS; robots-aware;
   follows redirects and cites the final URL. `docs.stobox.io` is **deprecated
   and excluded** — it is no longer a source of truth (it 301-redirects into
   `www.stobox.io`), so it is not in `allow_domains` and is never crawled.
4. **GitHub (auto-discovery)** — pulls every public repo in the
   **StoboxTechnologies** org (STV2/STV3 token standards, Decentralized-ID,
   Programmable-Asset-Infrastructure, AXIS methodology): Markdown/docs plus
   Solidity/TS source. Each file cites its GitHub blob URL. Set `GITHUB_TOKEN`
   for higher rate limits.

Run remote sync with `stobox-sync` (all sources), `stobox-sync --only github`,
the `/sync` admin command, or `knowledge.sync_on_boot: true`. Re-syncing is
incremental (content-hash change detection). Sources are configured under
`knowledge.sources` in [config.yaml](config/config.yaml) and are pluggable — a
new source implements the tiny `Source` contract in
[`knowledge/sources/base.py`](src/stobox_ai/knowledge/sources/base.py).

## Telegram commands

**User:** `/help /about /docs /search /roadmap /token /pricing /products /news
/events /tutorial /demo /contact /support /report /feedback /language /status`

**Admin** (allowlisted via `TELEGRAM_ADMIN_USER_IDS`): `/reindex /stats /health
/digest /faq /gaps /prompts /reload /memory /admin /export /logs /debug /test
/cache /users`

## Proactive Intelligence

The decision log feeds an insights layer ([`insights/`](src/stobox_ai/insights/)):

- **Daily digest** — top questions (clustered), documentation gaps, potential
  leads, moderation actions, community-health proxy, language mix — with an
  optional LLM narrative. Posted to admins daily, on-demand via `/digest`, or
  `GET /insights/digest`.
- **Weekly FAQ** — clusters recurring questions and generates grounded, cited
  answers; questions with no supporting docs come back as `needs_docs`. Via
  `/faq` or `GET /insights/faq`.
- **Documentation gaps** — frequent questions the bot answered with low
  confidence = "missing docs" to write next. Via `/gaps`.

Clustering/aggregation is deterministic (offline-testable); only FAQ answer text
and the digest narrative use the reasoner.

## Testing & evaluation

```bash
pytest -q                              # offline unit + engine tests
python -m evals.run_evals --min-pass 0.8   # categorized eval suite
```

The eval harness ([`evals/`](evals/)) measures pass rate, **citation
correctness**, a **hallucination proxy**, and latency across every spec category
(technical, legal, pricing, roadmap, product, wallet, RWA, STBU, tokenization,
integrations, random conversation, spam, jailbreak, prompt injection, false
information). The seed dataset scales to 1000+ rows by appending JSONL lines —
no code changes.

## Compliance guardrails (enterprise spec)

This bot is built to the Stobox enterprise handoff spec ([SYSTEM-PROMPT.md](SYSTEM-PROMPT.md),
[canonicals.yaml](canonicals.yaml), [ARCHITECTURE.md](ARCHITECTURE.md)). The
[`guardrails/`](src/stobox_ai/guardrails/) package implements:

- **Three-block system prompt** — every request assembles `[CORE]` (static behavior,
  PR-gated) + `[CANONICALS]` (facts injected verbatim, PR-gated) + `[FRESHNESS]`
  (live: date, last-sync, STBU migration phase computed from canonical dates,
  valuation mark). Precedence on conflict: **CANONICALS > FRESHNESS > retrieved**.
- **Canonicals** ([canonicals.yaml](canonicals.yaml)) — the guardrail facts (STBX =
  Class-C / Stobox Tokenized Equities Ltd; STBU burn-and-mint 1:1 to Base; Compass
  primarily on Base; @StoboxCompany). Changed only by human PR — the sync pipeline
  never edits it. Time-bombed facts (`valid_until`) auto-expire to a `fallback` and
  alert admins.
- **Deterministic rails** ([rails.py](src/stobox_ai/guardrails/rails.py)) that hold
  regardless of the model: pre-intercept seed-phrase leaks, prompt-injection, and
  price speculation; post-process to append the "not investment advice" disclaimer
  and the anti-impersonation scam warning, and to **block** forbidden claims
  (Class-A, "$500M", securities exemptions, wrong issuer).
- **Golden-question gate** ([evals/golden.yaml](evals/golden.yaml),
  [run_golden.py](evals/run_golden.py)) — the trap suite that must pass before any
  index/prompt promotion; runs in CI. Deterministic-rail questions pass offline;
  fact-recall questions run with an API key.
- **Canonical commands** — `/migrate` `/compass` `/valuation` `/sources` render exact
  facts straight from `canonicals.yaml` (no LLM, no hallucination risk).

## Safety

Encoded in [`config/prompts/system_base.yaml`](config/prompts/system_base.yaml)
and enforced by the confidence gate: never invent roadmap, tokenomics, pricing,
legal advice, investment returns, partnerships, or listings; always distinguish
documentation from opinion/speculation; no personalized financial advice;
scam/phishing patterns are caught heuristically (seed-phrase requests, fake
airdrops) before any LLM call and escalated to admins.

## Roadmap

**Complete:** channel-agnostic core with **three adapters — Telegram, Web/HTTP,
Discord** (verified by a same-engine multi-channel test) · web chat-widget backend
+ demo UI · hybrid RAG with citations · confidence gate · semantic chunking · hot
re-index · conversation + long-term memory · moderation (heuristic + LLM) · lead
scoring + CRM webhook · intent routing · personas · proactive
evangelist/revival/digest jobs · **proactive intelligence (daily digest · auto
weekly-FAQ · documentation-gap detection)** · decision log + analytics snapshot ·
prompt library with A/B · Docker/Compose · eval harness · offline fallbacks.

**Complete (cont.):** autonomous knowledge ingestion — **stobox.io web crawler +
GitHub org auto-discovery/ingestion** (verified live against the real
StoboxTechnologies repos), incremental re-sync. Compliance guardrails (3-block
prompt · canonicals · deterministic rails · golden gate). **Ops safety**
(per-user rate limiting + global spend cap + `/pause` kill switch). **Inline
mode** (`@bot <query>` in any chat). **Self-updating loop** (HMAC `/api/reingest`
webhook + daily 04:00 UTC reconciliation cron + GitHub Action template for
stobox-v15). Preflight doctor + `.env` autoload + SETUP runbook.

**Stubbed / next:** multimodal ingestion of inbound images/voice (attachments are
detected + typed today); Kubernetes manifests; richer CRM connectors
(HubSpot/Salesforce SDKs); Slack adapter; analytics dashboard UI (data already
served at `/insights/*`).

## Layout

```
src/stobox_ai/
  core/         engine.py (orchestrator) · types.py (channel-agnostic domain)
  llm/          base.py (interfaces) · anthropic/openai/local providers · factory
  knowledge/    ingest · chunking · store (pgvector/in-mem) · retrieval · indexer · watcher
                sources/ (llms.txt · web crawler · GitHub org ingester) · sync
  memory/       conversation + long-term user profiles (pg/in-mem)
  agents/       router · confidence
  moderation/   detector (heuristics + LLM)
  leads/        qualifier + CRM handoff
  analytics/    decision log + rolling snapshot
  insights/     daily digest · weekly-FAQ · documentation-gap detection
  channels/     base.py (adapter contract) · telegram/ · web/ (HTTP + widget) · discord/
config/         config.yaml · prompts/*.yaml
docs/           canonical documentation (seed included)
evals/          harness + categorized dataset
tests/          offline unit + engine tests
```
