# Architecture of Stoby (version 1.1.0)

How the current bot, `src/stoby`, is built and why. The previous bot, `src/stobox_ai`, has its own documents at the
repository root (`ARCHITECTURE.md`, `DEPLOY.md`, `SETUP.md`, `ROADMAP.md`); they describe the July 2026 design with a vector
store and a knowledge base, and do not describe `src/stoby`.

## Principles

1. **No facts in code.** Stoby is a thin client over three sources, read at answer time: the site reference file, the Stobox
   Intelligence Graph (SIG) and Base mainnet. The behaviour prompt (`config/prompts/stoby.md`) holds tone and rules, no
   figures, dates or addresses. A change on the site reaches the answers without a commit here.
2. **Fail closed.** A source that is down means a fixed "I can't check that right now" reply and no model call.
3. **Rails do not depend on the model.** Compliance checks before and after the model are deterministic code with golden
   tests, so a model slip cannot become an incident.
4. **Cost is bounded in code.** A daily stop is checked before every model call; per-answer cost is measured.
5. **Observable.** One JSON log line per answer and per feed check; the boot line carries the version and the commit.

## How one answer is made

```
question
  → rails before the model      seed phrase, prompt injection, advice, raise questions: fixed reply, no model
  → daily spend cap             over the cap: a polite pause, no model
  → site                        llms-full.txt, 10-minute cache, last good copy kept up to 24 hours
  → buy intent?                 "where / how do I buy STBU": the site's own words, no model
  → SIG context                 stobox_answer_context over MCP, partner token
  → model                       one call, adaptive thinking, the site block cached in the system prompt
  → grounding check             every figure and address in the draft must appear in the sources
  → SIG fact_check              a named canon rule rejects the draft (one regeneration, then a fixed reply)
  → rails after the model       official links only, known addresses only, no long dash, no buy invitations,
                                claim questions always name the official claims page, disclaimer where needed
  → Telegram HTML               bold titles, <code> addresses, bullets; plain text if Telegram rejects the markup
  → one JSON "answer" log line  outcome, dollars, verdict, latency
```

Precedence when sources differ: the site, then the chain, then SIG (SIG was stale on the token until 27 September 2026,
which is why the site outranks it).

## Sources

| Source | Module | Behaviour |
|---|---|---|
| Site | `sources/site.py` | Fetches `llms-full.txt` with a short cache; keeps a last good copy on the volume and uses it for at most `site_stale_hours` (24) when the site is down; if the file is unreadable or the STBU section is missing, Stoby knows nothing and says so. |
| SIG | `sources/sig.py` | MCP JSON-RPC over HTTP; one batched call per question (`stobox_answer_context`, then `stobox_fact_check` for the draft); 8 second timeout; circuit breaker; the question is scrubbed before it leaves. Any failure raises `SigDown` and the answer is not generated. Stoby never calls the one write tool, `stobox_start_tokenization`. |
| Chain | `sources/chain.py` | Read-only balance reads for `/check` on Base and the legacy chains. Which contracts to read is parsed from the site's STBU section at request time; nothing is hardcoded. |

## Modules

| File | Role |
|---|---|
| `__main__.py` | Boot: refuse to run as root, wire the sources, start polling, the heartbeat and the announcer |
| `config.py` | Settings from the environment: addresses, models, budgets; no facts |
| `answer.py` | The pipeline above |
| `llm.py` | The one model call and its cost; static cached behaviour prompt, volatile source blocks after the cache breakpoint |
| `verify.py` | Grounding: each `0x` address and number in a draft is looked up in the source text; small counting words are allowed |
| `rails.py` | Compliance rails: pre-intercepts (seed phrase, injection, price speculation, "should I buy") and post-processing (disclaimer, anti-impersonation warning, link and address scrub, buy-invitation and raise-sentence removal, no long dash) |
| `format.py` | Telegram HTML subset; everything else escaped; the useful-links block |
| `commands.py` | Fixed-text commands and the menu set at every boot |
| `telegram.py` | aiogram router; addressing rules; `setMyCommands` for default, private and group scopes; the heartbeat |
| `announce.py` | Blog announcer: site RSS feed polled every 10 minutes; the first run only records what exists; each new post goes once per chat with its cover image |
| `leads.py` | An email counts only in a private chat, only the first per user, never one of our own mailboxes; one CRM post per user and email, a daily cap, https only |
| `ledger.py` | JSON log lines and the spend ledger on the volume |

## What Stoby does not do

It does not moderate (the moderation bot moderates the group; Stoby never deletes, mutes, bans or welcomes), give
investment advice, predict prices, state a market cap, confirm any capital raise, name a securities exemption, or post
anything on a timer other than the blog announcer.

## Commands

| Command | Where | What |
|---|---|---|
| `/stbu` | anywhere | The STBU on Base: token contract, official pool, links, old-holder claims, switched-off legacy contracts |
| `/check 0xAddress` | anywhere | STBU on Base and any legacy STBU in a public wallet, read from the chain |
| `/blog` | anywhere | The newest article's cover and the five latest articles |
| `/help`, `/start` | private chat; in the group only as `/help@stobox_assistant_bot` | What Stoby does |
| `/sources` | same as `/help` | Official Stobox channels, from the site |
| `/contact` | same as `/help` | The support contact |

In the group, bare `/help`, `/start`, `/sources` and `/contact` are left to the moderation bot (ChatKeeper), so two bots never
answer the same command, and its own commands are never in Stoby's menu. A command Stoby does not have never goes to the
model: in a private chat or addressed to Stoby it gets the list of real commands; bare in the group it is left alone.

## State

Everything mutable lives under `STOBY_STATE_DIR` (default: the Railway volume): the spend ledger, the last good copy of the
site file, the announcer's record of posts already sent, and the lead record. The container starts as root only so
`deploy/entrypoint.py` can hand the volume to the unprivileged user (uid 10001) and then drop root; without this step every
state write failed with a permission error (27 September 2026).

## Evaluation

- `evals/golden.yaml`: 31 traps; `evals/injection.yaml`: 22 prompt-injection cases. `python -m evals.run_stoby` runs the real
  pipeline, plants a random canary in the system prompt and fails any answer that echoes it, carries a key-like string, a
  bot-token pattern or an internal name.
- `evals/run_golden.py` and `evals/run_evals.py` are the offline gates CI runs.
- Cost ceilings (set 27 September 2026): mean 0.03 USD and p95 0.06 USD per answer; measured at 1.0.0: mean 0.0092, p95 0.0101.

## Acceptance of 1.0.0 (28 September 2026)

| # | Attempt | Result |
|---|---|---|
| A1-1 | No fact files in the tree | Package clean; the previous bot's files go with the cleanup |
| A1-2 | A site edit reaches the answer with no commit | Done (offline) |
| A1-3 | Golden traps | 31/31 |
| A1-4 | Prompt injection set | 22/22 |
| A1-5 | SIG down means no model call | Done, offline and live |
| A1-6 | A person tests in the group | Pending at the time of release |
| A1-7 | Cost per answer | Mean 0.0092 USD, p95 0.0101 USD (ceiling 0.03 / 0.06) |

`scripts/acceptance_stage1.py` reports A1-1 as "not ready" while `docs/`, `canonicals.yaml`, `SYSTEM-PROMPT.md` or
`src/stobox_ai/` are still tracked; that, and the fact that `docs/` is read by the previous bot's ingester and copied into its image, is why the documentation of this bot lives in `documentation/`, not in `docs/`.

## The previous bot

`src/stobox_ai` is the stage 0 bot: a channel-agnostic retrieval agent with a Postgres and pgvector store, a knowledge
ingester that reads `docs/`, moderation, engagement and insight modules, and a web channel. It stays in the tree as the
rollback; `railway.json` starts the current bot. TODO(owner): decide when the previous bot and its documents are removed
(the README of 1.0.0 said it would be kept for one day).
