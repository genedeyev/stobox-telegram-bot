# Stobox AI Telegram Bot — Production Engineering Audit

> **Status update (2026-07-16, branch `audit/p0-fixes`):** the **P0 batch** is implemented and committed — C1 (LLM timeouts + concurrent updates + copy discipline), C2 (preflight exit code), C3 (atomic JSON ledgers + quarantine + per-recipient reminder delivery), H1/H2 (insights auth, body caps, IP rate limit), H3 (flood-wait handling), H4 (HTML escaping), H5 (update-type filter), H8/H9 (persistent `data/` + persisted known-chats/countdown state), H10 (iteration copies).
> The **P1 batch** is also implemented — H6 (absolute confidence signal: rerank > raw cosine > fused fallback, plus SOURCES-declared citedness), H7 (compliance rails on evangelist/quiz/FAQ/digest outputs), H11 (runtime `FallbackProvider` cross-provider failover), H12 (schema-drift-tolerant hydration for profiles + all dataclass stores), H13 (ru/uk/es rails patterns + multilingual IDK markers + English-pinned marker sentence), M1 (hash-gated remote resync), M5 (paragraph-boundary message splitting), M6 (command cooldowns on /support /report /appeal /check), M7 (best-effort post-answer profile save), M9 (tracebacks at top-level catchers). 227 tests pass.
> The **P2 batch** is implemented as well — M2 (Anthropic prompt caching of the stable [CORE]+[CANONICALS] prefix via split system messages), M3 (rerank/multi-hop on the classifier model), M8 (multi-stage Dockerfile, prod-only deps, heartbeat-based HEALTHCHECK), M10 (GDPR `/forgetme` erasure across profile/messages/decisions/XP/opt-ins + decision-log `at` index, boot backfill of the analytics ring, nightly 90-day retention prune; message-log per-line load tolerance + atomic compaction), M11 (pgvector dimension guard with auto-rebuild + HNSW instead of empty-trained ivfflat), M17 (`requirements.lock` via uv, Dockerfile installs from it), M19 (config reconciliation: missing keys added, dead sections removed). 233 tests pass. Remaining open: P3 items (engine decomposition, books→Postgres, shared send/HTTP helpers, multi-replica leader lock, metrics endpoint, Telegram-surface coverage push).

**Date:** 2026-07-16 · **Scope:** full repo (working tree incl. uncommitted changes) · **Method:** mechanical verification (build, tests, coverage, ruff, mypy, bandit, pip-audit, offline evals) + six parallel deep code reviews (security, Telegram platform, AI/RAG, storage, reliability/DevOps, code quality/tests). Every finding below was verified against actual code; each lists severity, location, root cause, failure scenario, fix, and effort (S/M/L).

---

## 1. Executive Report

**Verdict: NOT production-ready yet — but close, and the gap is narrow and well-defined.**
The codebase is unusually clean for its age (zero dead code, zero TODOs, zero CVEs, zero lint errors, 194 passing offline tests, real CI, thoughtful guardrails and degradation paths). What blocks production is a small cluster of availability and state-durability defects, most of which are S/M-effort fixes: the bot processes all updates on a single lane with no LLM timeout, a failed preflight exits 0 (silent outage), and all operational state is non-atomic JSON on an ephemeral disk.

| Dimension | Score | Rationale |
|---|---|---|
| Security | 7/10 | No criticals; injection/secrets/webhook story is clean. Public unauth `/insights` PII leak + spoofable `/chat` identity are the gaps. |
| Reliability | 4/10 | Exit-0 on preflight failure, whole-bot blocking on one slow call, state amnesia on every redeploy, no flood-wait handling. |
| Performance | 5/10 | Sequential update lane; up to 4 LLM round-trips + 2 embeddings per question; daily full re-embed of remote corpus; no prompt caching; sync file I/O on the event loop. |
| Maintainability | 7.5/10 | Excellent hygiene (no dead code, no cycles that bite, consistent style); engine god-object and config sprawl are the debt. |
| Test quality | 6/10 | 61% coverage; core logic well-tested and offline-deterministic; the entire Telegram surface (adapter 9%, commands 12%) is effectively untested. |
| Scalability | 3/10 | Hard single-instance design (in-memory rate limits, dedupe, known-chats; polling). Fine for one community, documented nowhere. |
| **Overall** | **56/100** | Production gate fails on: high-severity bugs > 0, coverage < 95%, state durability. |

**Risk framing:** the highest business risk is timed to the STBU→Base migration crunch — exactly when question volume and reminder-blast size peak, the sequential lane (C1) and flood-wait blindness (H3) degrade the bot, and a redeploy can double-fire deadline reminders (C3/H8) or silently stop countdowns (H9).

---

## 2. Mechanical Verification Results (Phases 1–3, 15, 16)

- **Build:** fresh venv install works; every one of 88 modules imports cleanly; console scripts resolve. No lockfile (see M-DEV3).
- **Tests:** 194/194 pass in ~4s, fully offline (conftest strips API keys, Echo LLM + hash embeddings). Golden compliance gate and eval harness both run offline.
- **Coverage:** **61%** overall. Strong: engine 78%, rails 99%, guardrails 85–100%, ops 85–95%. Weak: telegram/adapter.py **9%**, telegram/commands.py **12%**, proactive.py 36%, knowledge/sync.py 15%, knowledge/store.py 48%, memory/store.py 49%.
- **Lint:** ruff — 0 issues. **mypy:** 50 errors (mostly `Optional[self.app]` narrowing noise; real items: adapter.py:190 `raise last_exc` can raise `None`, web.py URL-type confusion).
- **Security tooling:** pip-audit — **0 known CVEs**. bandit — 0 high, 1 medium (0.0.0.0 bind — expected for PaaS), 46 low (`try/except/pass` — see M-DEV2). No hardcoded secrets in tracked files; `.env` gitignored, never in git history.
- **CI:** exists and gates ruff + offline pytest + eval smoke + golden gate + docker build. No CD, no coverage gate.

---

## 3. Bug Report (deduplicated, ranked)

### CRITICAL

**C1. Entire bot is single-lane; one slow/hung LLM call blocks every user and all moderation**
`src/stobox_ai/channels/telegram/adapter.py:106-115`, `src/stobox_ai/llm/anthropic_provider.py:32`, `openai_provider.py:23`
`ApplicationBuilder()` never sets `.concurrent_updates(...)` (PTB default: strictly sequential) and all handlers block. Compounding it, the Anthropic/OpenAI SDK clients are built with **no `timeout`** (SDK default 600s + 2 internal retries, × 3 tenacity attempts ⇒ worst case ~30 min). One hung call ⇒ every user's messages, scam-message deletion, and admin commands queue behind it.
**Fix:** `AsyncAnthropic(..., timeout=30.0, max_retries=0)` (same for OpenAI); `.concurrent_updates(True)` — together with H10's copy-discipline fixes. Effort: S–M.

**C2. Preflight failure exits with code 0 — orchestrator thinks it's a clean shutdown**
`src/stobox_ai/__main__.py:27-29`. `if not pf.ready: return` exits 0. Railway `restartPolicyType: ON_FAILURE` does not restart ⇒ silent total outage on a bad env push; compose `unless-stopped` becomes a zero-backoff crash loop.
**Fix:** `raise SystemExit(1)`. Effort: S (one line).

**C3. All eight JSON stores write non-atomically and silently reset to empty on corrupt load**
`engagement/xp.py:136-142`, `moderation/strikes.py:142-153`, `qa/register.py:116-124`, `ops/subscriptions.py:134-139`, `ops/reminders.py:68-75`, `ops/winback.py:59-64`, `content/flywheel.py:147-152`, `engagement/ama.py:124-132`
`path.write_text(json.dumps(...))` truncates in place; kill mid-write ⇒ corrupt file ⇒ `_load` swallows and resets to `{}`. Consequences: reminders `sent` ledger wiped ⇒ **mass duplicate deadline blasts**; strikes/bans forgiven; QA register lost.
**Fix:** shared helper — write temp + fsync + `os.replace`; quarantine corrupt file (`.corrupt-<ts>`) + admin alert instead of silent reset. Effort: S.

### HIGH

**H1. Unauthenticated `/insights` endpoints leak community PII on the public web service** — `channels/web/adapter.py:169-186` + `render.yaml`. Dashboard/digest/FAQ expose verbatim member questions, lead keys, capture status; `/insights/faq` triggers LLM spend on demand. **Fix:** bearer auth or drop routes when `STOBOX_ENV=production`. Effort: S. (OWASP A01)

**H2. `/chat` identity is client-supplied — rate-limit bypass, cost DoS, memory poisoning** — `channels/web/adapter.py:105-116`. Rotating `user_id` bypasses per-user limits (only backstop: global daily token cap) and writes into arbitrary users' profiles. No body-size cap either (`request.json()` unbounded). **Fix:** authenticate, derive identity server-side, IP-based limiter, 8KB body cap. Effort: M. (OWASP A01/A04)

**H3. No Telegram flood-wait (`RetryAfter`) handling anywhere; reminder blasts permanently lose recipients** — `proactive.py:402-460`, `commands.py:1160-1174`. All send loops swallow every exception; `RetryAfter` drops sends, then `mark_sent(tag)` records the blast done ⇒ those users **never** get the burn-deadline reminder. **Fix:** PTB `AIORateLimiter` or catch `RetryAfter`+sleep+retry; distinguish `Forbidden` (unsubscribe); mark sent per-user. Effort: M.

**H4. Unescaped user content interpolated into `parse_mode="HTML"`** — welcome names (`adapter.py:327-353`), modlog, AMA text, `/log`, `/whosaid`, admin DMs (`commands.py:1272-1302` etc.). A member named `<a href="https://scam">Stobox Support</a>` becomes a live link in the bot's own message (impersonation vector for an anti-scam bot); stray `<x` breaks sends/admin commands. Zero `html.escape` usage in the channel. **Fix:** escape helper at every interpolation. Effort: S.

**H5. Edited messages and channel posts re-run the full answer pipeline** — `adapter.py:123-125`; `is_edited` set (`:1085`) but never read. Each typo edit = another LLM spend + duplicate reply; channel posts share one `"unknown"` identity/rate-bucket. **Fix:** `& filters.UpdateType.MESSAGES` or early-return. Effort: S.

**H6. Confidence gate is largely inert — min-max normalization makes top score ≈ 1.0 for any query** — `knowledge/retrieval.py:27-33,86-102` → `agents/confidence.py:48-54`. Any retrieval hit (however irrelevant) yields `retrieval_signal ≈ 1.0`, and `cited` is true whenever anything was retrieved — the flagship IDK/anti-hallucination gate almost never triggers; also breaks WeeklyFAQ gap detection (`insights/faq.py:59-66`). **Fix:** use absolute cosine similarity (pgvector already returns it) or the rerank score; verify model `SOURCES:` against citations. Effort: M.

**H7. Public LLM output paths bypass the compliance rails** — `proactive.py:613-645` (`_evangelist_job` posts `result.text[:4096]` to all groups with no `rails.post_process`, no canonicals in context, legacy prompt); same for quiz explanations, WeeklyFAQ, digest narrative. The exact claims `_FORBIDDEN` exists to block can be broadcast unmoderated. **Fix:** run `post_process` + assembled system prompt on every public path. Effort: S.

**H8. All operational state lives on an ephemeral disk — data loss and double-fires every redeploy** — `render.yaml` (no disk), `railway.json`, `docker-compose.yml` (no `data/` volume). Strikes/bans, reminder ledgers, win-back cooldowns, XP, QA register vanish per deploy. **Fix:** persistent volume at `/app/data` now; migrate books to the existing Postgres medium-term. Effort: S (volume) / M (Pg).

**H9. Proactive amnesia on restart: broadcasts silently stop; claims-open announcement re-fires** — `adapter.py:74` (`known_chats` in-memory ⇒ after deploy, countdown/briefings no-op until a human posts in each group), `proactive.py:167,316-322` (`_countdown_last` in RAM ⇒ every restart after claims open re-posts "🟢 claims are open" to every group at 09:00). Quiz polls unscored, milestones re-celebrated, `drop_pending_updates=True` discards everything sent during downtime. **Fix:** persist `known_chats`, `_countdown_last`, quiz map (same helper as C3). Effort: S–M.

**H10. Job-queue tasks iterate the live `known_chats` set while handlers mutate it** — `proactive.py:605-606` returns the actual set; `RuntimeError: set changed size during iteration` mid-broadcast skips remaining chats. Becomes far more likely once C1's `concurrent_updates` lands (all shared dict/session state needs the same review). **Fix:** return copies; per-user guards on await-spanning read-modify-write. Effort: S–M.

**H11. Configured runtime LLM fallback does not exist** — `llm/factory.py:34-55` vs `config/config.yaml:19-21` ("Fallback provider used if the primary errors out" — false; consulted only at build time). An Anthropic outage ⇒ every reply fails despite a healthy OpenAI key. **Fix:** `FallbackProvider` wrapper catching primary exhaustion. Effort: M.

**H12. Schema-brittle hydration: one dataclass field rename bricks existing users** — `memory/store.py:29-33` (`UserProfile(**d)`, Pg path uncaught ⇒ `handle()` raises for every returning user), same pattern in xp/strikes/qa/ama JSON stores. The defensive pattern already exists in `knowledge/store.py:33-40` — just not applied. **Fix:** filter to known fields + try/except. Effort: S.

**H13. English-only deterministic rails in a 12-language bot** — `guardrails/rails.py` regexes vs `languages.supported` (12) and ru/uk IDK strings. "Стоит ли покупать STBU?" reaches the LLM un-intercepted; post-process rails are also English-only; IDK detection (`engine.py:870`) is an English substring, so non-English IDK skips QA capture. **Fix:** multilingual patterns or classifier-gated intercepts + unicode tests. Effort: M.

### MEDIUM (selected — full detail in agent findings)

- **M1. Daily resync re-embeds the entire remote corpus** — `knowledge/sync.py:92-93` bypasses the content-hash skip that `index_directory` has; ~700 docs re-embedded daily (recurring OpenAI spend, rate-limit pressure). Fix mirrors the local path. S.
- **M2. Prompt caching designed for but never wired** — `guardrails/assembly.py` exposes `stable_prefix()`; `anthropic_provider.py` sends a plain string ⇒ ~8K static tokens billed full-rate on every call. Add `cache_control` blocks. S–M.
- **M3. Up to 4 LLM round-trips + 2 embeddings per doc question** — router (Haiku) + `_followups` + `_rerank` (both on the **expensive reasoner**, `retrieval.py:104-127`) + synthesis; `multi_hop`+`rerank` on by default. Point hop/rerank at the classifier model. S.
- **M4. Every group message costs 2 classifier calls before rate-limit/engage checks** — `engine.py:541-634`; spam floods ⇒ ~200 Haiku calls/min that no cap constrains (spend cap only counts reasoner output). Reorder pipeline; count classifier spend. M.
- **M5. Message >4096 chars: `text[:4096]` can bisect an HTML tag** ⇒ BadRequest ⇒ plain-text fallback loses all formatting, or the citations/compliance footer silently truncates. Split on paragraph boundaries. S.
- **M6. Command paths bypass all rate limiting** — `/support`, `/report`, `/appeal` DM every admin per call, callable by anyone in-group (admin-DM flood); `/check` = 4 chain RPCs. Add command cooldowns. S.
- **M7. Postgres blip after the answer is generated throws the answer away** — `engine.py:683` `save_profile` unguarded post-LLM; wrap best-effort. S.
- **M8. Docker: single-stage image ships build-essential + dev deps; `COPY src` before `pip install` kills layer caching; no `.dockerignore`** (context ships `.env`, `.git`, `data/`, `.venv` to the daemon); healthcheck is `import stobox_ai` (a no-op for liveness); base not digest-pinned. M.
- **M9. No exception tracebacks anywhere** — `format_exc_info` configured but zero `exc_info=True`/`log.exception` call sites; production errors log as bare `str(exc)`. S.
- **M10. PII: decision log persists full question text to Postgres indefinitely (no retention job, no index, never read back); stdout copy logs `q=` at INFO; no GDPR Art. 17 erasure path (`/forgetme`) for profiles or message log; in-memory analytics ring resets on deploy so digests/FAQ/flywheel see only post-restart data.** M.
- **M11. pgvector: dimension change silently breaks upserts forever** (`CREATE TABLE IF NOT EXISTS` no-op; probe + rebuild path needed); **ivfflat index built on an empty table** (poor recall for the corpus's life — use HNSW); message-log compaction non-atomic and aborted whole-file by one corrupt line. S–M each.
- **M12. Forum supergroups: all proactive sends omit `message_thread_id`** ⇒ land in General or fail silently. S.
- **M13. Privacy-mode assumption unchecked** — half the feature set (moderation, FUD, engage-on-question) needs `can_read_all_group_messages`; verify at boot and warn. S.
- **M14. `/reindex` admin command can race the 04:00 resync job and boot sync** — no lock around index mutations. S.
- **M15. Username-based admin auth is re-registerable** — `config.py:92-96`; resolve to numeric IDs at startup. S.
- **M16. Reflected XSS in `examples/web-widget.html`** — `innerHTML` on model output. S.
- **M17. No dependency lockfile** — `>=` ranges install untested upstream majors on every rebuild. Add `uv lock`/`pip-compile`. M.
- **M18. Horizontal scaling unsafe but only implicitly guarded** — two replicas ⇒ getUpdates 409s + doubled broadcasts; document + leader lock. S–M.
- **M19. Config drift** — 13+ keys read in code but absent from YAML; whole sections (`agent:`, `languages:`, `memory.summarize_after_turns`, …) defined but never read. S.

### LOW (rollup)

Sync file I/O on the event loop (xp.json rewritten up to 3× per answered message; debounce + `to_thread`); `RateLimiter` per-user dicts never pruned; `_remember_question` uses builtin `hash()` (collision ⇒ wrong "More detail" answer — use uuid4); quiet-hours are UTC while config says chat-tz; `/digest` uses Markdown parse mode with LLM text and no fallback; shutdown never awaits cancelled sync task or closes pools/HTTP clients; broadcast errors swallowed with no counter; `_INJECTION` regex matches the name "Dan" (`\bDAN\b`); OpenAI provider retries non-retryable 4xx; classifier misconfig silently falls back to the expensive reasoner; `reply_cap` char/token confusion (`engine.py:824-837`); three Pg pools (up to 14 conns) where one would do; per-chunk sequential INSERTs; SSRF-hardening gap (redirect target checked only post-fetch — operator-configured hosts, low); `render.yaml` sets blocking `STOBOX_SYNC_ON_BOOT=1` though background sync exists; compose lacks resource limits; brittle test pins (canonicals version string; module-global datetime monkey-patch).

---

## 4. Security Report

**Zero critical vulnerabilities. Zero dependency CVEs. Zero exposed secrets** (env-only, log levels raised on token-bearing libs, `.env` never committed).

| Finding | OWASP | Exploitability | Priority |
|---|---|---|---|
| H1 unauth `/insights` PII | A01 | Trivial (public URL) | P0 |
| H2 spoofable `/chat` identity, cost DoS, no body cap | A01/A04/A05 | Trivial script | P0 |
| H4 HTML injection via names/messages | A03 | Easy (set display name) | P1 |
| M15 username-based admin authz | A01/A07 | Requires username lapse | P2 |
| M16 widget XSS (`innerHTML`) | A03 | Needs model echo | P2 |
| SSRF via redirects (post-fetch allowlist) | A10 | Needs trusted-host compromise | P3 |
| Prompt injection: regex pre/post rails bypassable; retrieved KB content unmarked in prompt | LLM01 | Moderate | P2 (defense-in-depth) |

**Verified clean:** parameterized SQL everywhere; no `shell=True`/eval/pickle; all YAML via `safe_load`; no path traversal; HMAC-SHA256 webhook with constant-time compare; non-root container; wallet checker validates addresses and rejects private keys; callback authz re-checked server-side; format-string injection impossible in prompt templates.

---

## 5. Performance Report

- **Latency:** worst-case reply = router + follow-up-gen + 2 embeds + rerank + synthesis, serially, on one lane shared by all users (C1/M3). Biggest levers: SDK timeout + concurrency (C1), classifier-model for hop/rerank (M3), pipeline reorder (M4).
- **Cost:** ~8K uncached static prompt tokens per call (M2); daily full re-embed of remote corpus (M1); classifier spend invisible to the daily cap (M4); edited messages double-bill (H5).
- **Memory:** unbounded growth in profile cache, QA register (O(n) similarity scan inline in the answer path), rate-limiter keys, per-thread deques; slow but real.
- **Disk/event-loop:** synchronous whole-file JSON rewrites on the hot path (up to 3×/message); message-log append per group message.
- **Verified good:** market-data cache (TTL, lock, negative backoff, last-good fallback) is exemplary; outbound HTTP timeouts everywhere; SMTP/GitHub pushes off-loop via `to_thread`; conversation windows strictly bounded.

---

## 6. Architecture Report

**Current:** clean vertical-slice monolith — channel adapters (telegram/web/discord) → `AgentEngine` orchestrator → domains (knowledge, guardrails, memory, moderation, engagement, leads, ops, insights). Layering holds (adapters orchestrate domains; never the reverse). Zero dead modules, zero TODOs, zero unreferenced symbols. Three lazy import cycles, all currently benign.

**Problems:** engine god-object (`__init__` 128 lines wiring 15 subsystems; `handle` 149; `_answer` 186); persistence scattered across 8 hand-rolled JSON stores + 3 Pg pools; 4 hand-rolled HTTP wrappers; 14× repeated send boilerplate; single-instance assumptions undocumented.

**Recommended:** (1) extract a composition-root/wiring module from `engine.__init__`; (2) one `atomic_json.py` store helper (fixes C3/H12 in one place) then migrate books to Postgres; (3) `safe_send()`/`dm_admins()` helpers with escape + flood-wait + truncation (fixes H3/H4/M5 at every call site); (4) shared HTTP client module; (5) split `_answer` into prompt-build / complete / gate stages. Unused deps to drop: `pydantic`, `pydantic-settings`, `markdown-it-py`.

---

## 7. Test Coverage Report

**61% now; 95% is not credible without testing the Telegram surface** (2,600 lines at 9–12%). Suite quality where it exists is genuinely good: offline-deterministic, behavior-level, regression-named tests; evals + golden gate wired into CI.

**Top-10 highest-risk untested behaviors:** (1) Pg profile round-trip vs dataclass drift; (2) >4096 splitting + HTML-fallback with real model output; (3) engine paused/rate-limited branches; (4) factory fallback chain; (5) daily resync reconciliation; (6) rerank/multi-hop merge; (7) docs-watcher debounce; (8) rails on non-English text; (9) `_manual_action` admin mute/ban; (10) `__main__.run` shutdown ordering.

**Missing test categories:** unicode inputs, concurrency/interleaving (prerequisite for C1's fix), provider-failure paths, corrupt-state loads (per C3), flood-wait handling (per H3).

---

## 8. Refactoring Plan (prioritized roadmap)

**P0 — this week (availability + duplicate-blast prevention; ~2–3 days):**
1. C2 exit-code (1 line) · 2. C1 SDK timeouts + `concurrent_updates` + H10 copy discipline · 3. C3 atomic-write helper + corrupt-quarantine across 8 stores · 4. H8 persistent `data/` volume + H9 persist `known_chats`/`_countdown_last` · 5. H1/H2 web auth + body cap · 6. H3 `AIORateLimiter` + per-user `mark_sent` · 7. H4 `html.escape` helper · 8. H5 update-type filter.

**P1 — next two weeks (correctness of the AI safety story):**
H6 absolute confidence signal · H7 rails on all public outputs · H11 runtime fallback provider · H12 defensive hydration · H13 multilingual rails · M1 hash-gated resync · M7 best-effort profile save · M9 tracebacks · M5 tag-safe splitting · M6 command cooldowns.

**P2 — this month (cost, ops, hygiene):**
M2 prompt caching · M3/M4 pipeline reorder + cheap models for hop/rerank · M8 multi-stage Docker + `.dockerignore` + real healthcheck · M10 retention/erasure (`/forgetme`) + decision-log index/backfill · M11 pgvector guard + HNSW · M17 lockfile · M19 config reconciliation · Telegram-surface test suite targeting the top-10 list (aim ≥80% on adapter/commands, ≥90% overall).

**P3 — quarter:** engine decomposition, books→Postgres, shared HTTP/send helpers, leader-lock for multi-replica, metrics endpoint + alerting.

---

## 9. Acceptance-Criteria Status

| Criterion | Status |
|---|---|
| Zero build failures | ✅ |
| Zero critical vulnerabilities | ✅ (0 security-critical; 3 reliability-critical bugs C1–C3) |
| Zero high-severity bugs | ❌ 13 high findings (H1–H13) |
| Zero dependency vulnerabilities | ✅ pip-audit clean |
| No exposed secrets | ✅ |
| No dead code / duplicate code | ✅ dead code; ❌ duplication (send/HTTP boilerplate) |
| No unhandled exceptions crash the app | ✅ (error handler + top-level catches) — but many silently swallowed (M9) |
| No memory/resource leaks | ❌ slow unbounded growth (caches, registers, limiter keys) |
| Test coverage ≥95% | ❌ 61% |
| All Telegram workflows validated | ❌ adapter/commands essentially untested |
| Complete observability | ❌ no metrics, no tracebacks, no worker liveness |
| Production deployment verified | ⚠️ deploys run, but ephemeral state + exit-0 preflight make them unsafe |

**Gate: BLOCKED** until P0 (and ideally P1) are landed.
