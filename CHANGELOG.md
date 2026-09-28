# Changelog

All times UTC. Every release is deployed to Railway from `main`.

## 1.1.0 · 28 September 2026

Visual support (Gene, 28.09.2026).

- `/blog` sends the newest article's cover image (its og:image from stobox.io) with the list of the latest
  articles as the caption; plain list if the image is missing.
- Every message that links to stobox.io shows a small preview card of that page, with the site's own artwork
  (og-stbu, og-blog, og-readiness and so on). The card is always the first stobox.io link; Uniswap and CoinGecko
  links never get one. `/stbu` previews stobox.io/stbu.
- Blog announcements already carry each post's cover (1.0.0).

## 1.0.0 · 28 September 2026

The bot is rebuilt as `src/stoby`: a thin client with no facts of its own.

**Live since 08:58, current deployment 09:18 (`dbb2ad4`, then this release).**

- **Facts from the sources, not the code.** Every fact comes from stobox.io/llms-full.txt, the SIG MCP server
  (partner token) and Base mainnet, read at answer time. Every figure and address in a draft must appear in the
  sources, and SIG's named canon rules can reject a draft. A source down means a fixed reply and no model call.
- **Voice.** Short, friendly English with a few emojis; the new STBU on Base up front; one contact address,
  support@stobox.io; Medium is not listed among the official channels.
- **Layout.** Telegram HTML: bold titles, labelled contracts in `<code>` on their own line (one tap copies),
  a Useful links block under STBU answers: stobox.io/stbu, the official Uniswap v4 pool, CoinGecko, the pool
  safety check, the readiness score.
- **Blog announcements.** Each new blog post goes to the Announcements topic and General Chat of the Stobox
  Community group, once each, with its cover image.
- **Alongside ChatKeeper.** ChatKeeper moderates; Stoby never deletes, mutes or bans. In the group, bare
  `/help` `/start` `/sources` `/contact` are left to ChatKeeper.
- **Model.** `claude-sonnet-5` with adaptive thinking; the site block is cached in the system prompt.
  Measured: $0.0092 mean, $0.0101 p95 per answer; daily stop at $5.
- **Commands.** `/stbu`, `/check`, `/blog`, `/sources`, `/contact`, `/help`. The Telegram menu is set at every
  boot, so it can never lag behind the code (before this it still listed the previous bot's 16 commands, and a
  menu click on `/blog` fell through to the model). Unknown commands never reach the model.
- **Observability.** One JSON line per answer (outcome, dollars, verdict, latency) and per feed check; the boot
  line carries the version and the commit.
- Pull requests: #7 (package and cutover), #9 (forum topic, ChatKeeper), #10 (this release: menu, `/blog`,
  unknown commands, feed-check log, version in the boot line, documentation).

## 0.9 · 27–28 September 2026 · stage 0 on the previous bot

The previous bot (`src/stobox_ai`) was stopped from saying the STBU migration was still open.

- 27.09 18:14 · #3: facts rewritten from the live site (the burn window closed before 15 September 2026; claims
  close 31 December 2026, 23:59 UTC; legacy contracts discontinued); every timer post off; output rails for
  links and addresses; volume permissions fixed; leads hardened.
- 27.09 18:27 · #4: buy intent answered from the site's words; no invitations on trade and STBX questions.
- 27.09 20:57 · #5: claim questions always name stbu.stobox.io.
- 27.09 21:18 · #6: answers on `claude-sonnet-5` instead of `claude-opus-4-8`.
- 28.09 08:37 · #8: blog announcements also to the announcements target; `@stobox_official` removed from the
  impostor list.
