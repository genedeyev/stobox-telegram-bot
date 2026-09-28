# Stoby 1.1.0 · the AI helper of the Stobox community

`@stobox_assistant_bot` in the Stobox Community group on Telegram. Version 1.0.0 released **28 September 2026**,
1.1.0 the same day.

Stoby answers questions about Stobox and the STBU token, points people to the official links, and posts every new
blog article to the group. It holds **no facts of its own**: every fact in an answer is read, at answer time, from
the live stobox.io reference file, the Stobox Intelligence Graph (SIG) and Base mainnet.

- What changed and when: [CHANGELOG.md](CHANGELOG.md)
- Acceptance: `scripts/acceptance_stage1.py`
- Code: `src/stoby/` (version 1.1.0). `src/stobox_ai/` is the previous bot, kept one day as the rollback.

---

## What it does

| | |
|---|---|
| **Answers** | In a private chat, or in the group when someone mentions `@stobox_assistant_bot`, calls it Stoby, or replies to its message. Short, friendly English with a few emojis, whatever language the question is in. |
| **Contracts** | Every contract or pool address is labelled and shown on its own line in monospace, so one tap copies it. |
| **Useful links** | Under every STBU answer: STBU on stobox.io, the official Uniswap v4 pool, CoinGecko, the pool safety check. The readiness score is added in `/stbu` and the buy answer. |
| **Visuals** | `/blog` shows the newest article's cover image with the latest articles under it. Any answer that links to stobox.io carries a small preview card with that page's own image; external links (Uniswap, CoinGecko) never get the card. |
| **Blog announcements** | Every new stobox.io blog post goes to the group's **Announcements** topic and to **General Chat**, once each, with its cover image. |
| **Safety** | Warns about scams, never asks for or repeats a seed phrase, removes any non-official link or unknown address from its own answers. |
| **Leads** | In a private chat, an email plus a company question becomes a lead: one note to the admins, and the CRM webhook when it is configured. |

What it does **not** do: moderate (ChatKeeper moderates the group; Stoby never deletes, mutes, bans or welcomes),
give investment advice, predict prices, state market cap, confirm any capital raise, name a securities exemption,
or post anything on a timer.

## Commands

| Command | Where | What |
|---|---|---|
| `/stbu` | anywhere | The new STBU on Base: token contract, official pool, links, old-holder claims, switched-off legacy contracts |
| `/check 0xAddress` | anywhere | STBU on Base and any legacy STBU in a public wallet, read from the chain |
| `/blog` | anywhere | The newest article's cover and the five latest articles from the Stobox blog |
| `/help`, `/start` | private chat; in the group only as `/help@stobox_assistant_bot` | What Stoby does |
| `/sources` | same as `/help` | Official Stobox channels, from the site |
| `/contact` | same as `/help` | support@stobox.io |

In the group, bare `/help`, `/start`, `/sources` and `/contact` are left to ChatKeeper, so the two bots never
answer the same command. ChatKeeper's own commands (`/rules`, `/report`, `/ca`, `/base`, `/adminlist`,
`/partner`) are never in Stoby's menu.

The command menu Telegram shows is set by the code at every boot (`setMyCommands`, default, private and group
scopes), so it always matches the commands above. A command Stoby does not have never goes to the model: in a
private chat or addressed to Stoby it gets the list of real commands; bare in the group it is left alone.

## How one answer is made

```
question
  → rails before the model      seed phrase, prompt injection, advice, raise questions: fixed reply, no model
  → daily spend cap ($5)        over the cap: a polite pause, no model
  → site                        stobox.io/llms-full.txt, 10-minute cache, last good copy kept 24 h
  → buy intent?                 "where / how do I buy STBU": the site's own words, no model
  → SIG context                 stobox_answer_context over MCP, partner token
  → claude-sonnet-5             adaptive thinking, the site block cached in the system prompt
  → grounding check             every figure and address in the draft must appear in the sources
  → SIG fact_check              a named canon rule rejects the draft (one regeneration, then a fixed reply)
  → rails after the model       official links only, known addresses only, no em dash, no buy invitations,
                                claim questions always name stbu.stobox.io, disclaimer where needed
  → Telegram HTML               bold titles, <code> addresses, bullets; plain text if Telegram rejects the markup
  → one JSON "answer" log line  outcome, dollars, verdict, latency
```

Any source down (site, SIG, model) means a fixed "I can't check that right now" reply and **no model call**:
Stoby never answers from the model's memory. Precedence when sources differ: site, then chain, then SIG.

## Code map

| File | Role |
|---|---|
| `src/stoby/__main__.py` | Boot: refuse to run as root, wire the sources, start polling and the announcer |
| `src/stoby/config.py` | Settings from the environment (no facts) |
| `src/stoby/sources/site.py` | Reads and parses stobox.io/llms-full.txt; fails closed if the STBU section is missing |
| `src/stoby/sources/sig.py` | SIG MCP client: batched JSON-RPC, read tools only, 8 s timeout, circuit breaker, question scrubbing |
| `src/stoby/sources/chain.py` | `/check` balance reads on Base and the legacy chains |
| `src/stoby/answer.py` | The pipeline above |
| `src/stoby/llm.py` | The one model call and its cost |
| `src/stoby/verify.py` | Grounding check |
| `src/stoby/rails.py` | Compliance and output rails |
| `src/stoby/format.py` | Telegram HTML, the useful-links block |
| `src/stoby/commands.py` | Fixed-text commands |
| `src/stoby/announce.py` | Blog announcer (site RSS, every 10 minutes) |
| `src/stoby/leads.py` | Leads |
| `src/stoby/ledger.py` | JSON logs, dollars per answer, daily spend |
| `config/prompts/stoby.md` | Behaviour prompt: tone and rules only, no figures, dates or addresses |

## Configuration

| Variable | Needed | Meaning |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token |
| `ANTHROPIC_API_KEY` | yes | Model key |
| `STOBY_SIG_TOKEN` | yes in production | SIG partner token (the public tier allows 20 calls a day) |
| `TELEGRAM_ADMIN_USER_IDS` | yes | Numeric ids that receive lead notes |
| `STOBY_ANNOUNCE_CHATS` | for announcements | Comma list of `chat` or `chat:topic`. Empty: no posting (test bots) |
| `STOBY_ANSWER_MODEL`, `STOBY_ANSWER_EFFORT` | no | Default `claude-sonnet-5`, `medium` |
| `STOBY_DAILY_CAP_USD` | no | Default 5 |
| `STOBY_SITE_URL`, `STOBY_SIG_URL`, `STOBY_BASE_RPC` | no | Source addresses |
| `STOBY_STATE_DIR` | no | Defaults to the Railway volume (`/app/data`) |
| `CRM_WEBHOOK_URL`, `CRM_WEBHOOK_SECRET` | no | Lead intake (https only) |

## Run, test, accept

```bash
pip install -e ".[dev]"
python -m pytest -q                                   # unit tests, offline fakes
python -m evals.run_stoby --set all --max-usd 2.5 --out evals.json   # golden 31 + injection 22, real model
python scripts/acceptance_stage1.py --evals evals.json --live-sig-down
python -m stoby                                       # needs TELEGRAM_BOT_TOKEN and ANTHROPIC_API_KEY
```

The eval run costs real money (about $0.35 for 53 questions); run it once per release, not in a loop.

## Deploy

Railway service `Stoby AI`, deployed automatically from `main`. The image starts
`python /entrypoint.py python -m stoby`: the entrypoint hands the volume to the app user and drops root.
Rollback to the previous bot: set the start command in `railway.json` back to `python -m stobox_ai`.

## Acceptance of 1.0.0 (28.09.2026)

| # | Attempt | Result |
|---|---|---|
| A1-1 | No fact files in the tree | Package clean; the previous bot's files go with the cleanup |
| A1-2 | A site edit reaches the answer with no commit | Done (offline) |
| A1-3 | Golden traps | 31/31 |
| A1-4 | Prompt injection set | 22/22 |
| A1-5 | SIG down means no model call | Done, offline and live |
| A1-6 | A person tests in the group | Pending |
| A1-7 | Cost per answer | mean $0.0092, p95 $0.0101 (ceiling $0.03 / $0.06) |
