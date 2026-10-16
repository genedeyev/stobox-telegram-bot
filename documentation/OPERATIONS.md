# Operations

Running, testing, deploying and releasing Stoby (version 1.1.0). State as of 16 October 2026.

## Configuration

Names only. Values go in `.env` (ignored by git) locally and in the host's secret store in production. Defaults are in
`src/stoby/config.py`.

| Variable | Needed | Meaning | Default |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | yes | Bot token; the process exits with code 64 without it | none |
| `ANTHROPIC_API_KEY` | yes | Model key; exits with code 64 without it | none |
| `STOBY_SIG_TOKEN` | in production | SIG partner token (the public tier allows 20 calls a day) | none |
| `TELEGRAM_ADMIN_USER_IDS` | yes | Comma list of numeric ids that receive lead notes | none |
| `STOBY_ANNOUNCE_CHATS` | for announcements | Comma list of `chat` or `chat:topic`; empty means no posting (what a test bot must have) | empty |
| `STOBY_ANSWER_MODEL` | no | Answer model | `claude-sonnet-5` |
| `STOBY_ANSWER_EFFORT` | no | Thinking effort | `medium` |
| `STOBY_CLASSIFIER_MODEL` | no | Declared in the settings; `src/stoby` does not call it at this version | `claude-haiku-4-5` |
| `STOBY_DAILY_CAP_USD` | no | Daily spend stop, checked before every model call | `5` |
| `STOBY_SITE_URL`, `STOBY_SIG_URL`, `STOBY_BASE_RPC` | no | Source addresses | the site's `llms-full.txt`, the SIG MCP endpoint, a public Base RPC |
| `STOBY_STATE_DIR` | no | Mutable state; falls back to `RAILWAY_VOLUME_MOUNT_PATH`, then `data` | `data` |
| `CRM_WEBHOOK_URL`, `CRM_WEBHOOK_SECRET` | no | Lead intake (https only) | empty |
| `HEARTBEAT_FILE` | no | File touched every 60 seconds; the container health check reads its age | `/tmp/stobox-heartbeat` |

`.env.example` also lists variables of the previous bot (database, email, market data, web service); they are not read by
`src/stoby`.

## Run and test

```bash
pip install -e ".[dev,web,crm]" httpx
python -m pytest -q                         # 345 tests on 16 October 2026, offline fakes
ruff check src evals tests                  # lint, as CI does
python -m stoby                             # needs TELEGRAM_BOT_TOKEN and ANTHROPIC_API_KEY
```

The process refuses to run as root (exit 78) unless `STOBY_ALLOW_ROOT` is set; in a container start it through
`/entrypoint.py`, which drops root.

### Acceptance and evals (once per release)

```bash
python -m evals.run_stoby --set all --max-usd 2.5 --out evals.json   # golden 31 + injection 22, real model, real money
python scripts/acceptance_stage1.py --evals evals.json --live-sig-down
```

The real-model run costs about 0.35 USD for 53 questions; do not run it in a loop. `scripts/acceptance_stage1.py` prints one
outcome per attempt A1-1 to A1-7 and exits 1 on any failure.

## CI

`.github/workflows/ci.yml` runs on every push to `main` and every pull request, on Python 3.12: install, `ruff check src evals
tests`, `pytest -q`, an offline eval smoke (`evals.run_evals --min-pass 0.0`), the golden compliance gate
(`evals.run_golden`), and a Docker build of the image.

## Deploy

Railway builds the `Dockerfile` (multi-stage, slim runtime, non-root user, health check on the heartbeat file) from `main`
and starts `python /entrypoint.py python -m stoby` (`railway.json`: restart on failure, up to 10 retries, one replica).
State sits on a mounted volume at `/app/data`. Every release is deployed from `main`.

**Rollback to the previous bot:** set the start command in `railway.json` back to `python -m stobox_ai`. The previous bot
needs its own variables (see `.env.example`) and reads `docs/` and `canonicals.yaml`.

## Release procedure

1. Open a pull request that changes the version in `VERSION` and `src/stoby/__init__.py`, and adds the release section to
   `CHANGELOG.md`. TODO(owner): `pyproject.toml` still says `0.1.0`; set it in the same pull request.
2. CI green; run the acceptance commands above if behaviour changed; merge.
3. Tag the merge commit, annotated: `git tag -a vX.Y.Z -m "Stoby X.Y.Z · date"` and `git push origin vX.Y.Z`.
4. Confirm the boot log line shows the new version and commit (`boot` line: version, commit, model, whether SIG token and CRM
   are set, announce chats).

## Watching it

- **Logs:** one JSON `answer` line per answer (outcome, tokens, dollars, verdict, latency) and one line per feed check.
  Acceptance attempt A1-7 reads the `answer` lines.
- **Cost:** the daily spend is kept on the volume; hitting the cap gives users a polite pause and no model call.
- **Announcements:** the first run records existing posts and posts none; afterwards each new article goes once per chat.

## Things to know before touching it

- **`scripts/set_identity.py` carries the previous bot's command list** (`/guide`, `/qualify`, `/resources` and others). Do not
  run it for 1.1.0: the command menu is set by the code at every boot, and running the script would put the old list back.
- **Do not put documentation in `docs/`.** The previous bot ingests it as knowledge and the image copies it; this repository's
  documentation lives in `documentation/`.
- **Facts never go in code or in the prompt file.** Fix the source (the site, SIG), not the bot.

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| "I can't check that right now" on every answer | The site file, SIG or the model is unreachable; look at the `answer` lines for the failing source. By design there is no model call in that state. |
| Exit code 64 at boot | `TELEGRAM_BOT_TOKEN` or `ANTHROPIC_API_KEY` is missing |
| Exit code 78 at boot | Started as root; use `/entrypoint.py` |
| No blog announcements | `STOBY_ANNOUNCE_CHATS` is empty, or the bot lacks rights in the target chat or topic |
| State writes fail with permission errors | The volume is not owned by the app user; start through `/entrypoint.py` |
| Two bots answer one command in the group | A bare `/help`, `/start`, `/sources` or `/contact` should be left to the moderation bot; check the command scopes |

## Ownership

The repository owner is `genedeyev`; Stoby's programme and acceptance criteria live in the Stobox vault page `Stoby`.
TODO(owner): name who is on call for the Railway service and who holds the Telegram bot token.
