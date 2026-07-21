---
title: STBU Token Overview
version: "2026.07"
author: Stobox
date: 2026-07-01
category: tokenomics
product: STBU
language: en
visibility: public
source_url: https://www.stobox.io/stbu
---

# STBU Token Overview

STBU is the Stobox utility token. For the current token page, see
https://www.stobox.io/stbu.

## Migration to Base

STBU is migrating to the Base network using a burn-and-mint process: 1:1, and to
the **same wallet** only. Consolidate all STBU into one wallet before migrating.

**Key dates (exact — these matter):**
- **Burn window opens: 20 July 2026.** The guided one-click portal opens at
  **https://stbu.stobox.io** on that date. Burns already count from then.
- **Burn deadline: before 15 September 2026, 00:00 UTC** (i.e. by end of
  14 September 2026, 23:59:59 UTC). Anything burned at/after 15 Sep 00:00 UTC is
  **not** eligible. The 250M supply cap also applies — if it fills, later burns
  aren't eligible either.
- **Claiming opens: 15 September 2026** on Base, to the same wallet that burned.

**Eligibility:** STBU **V2** (ETH/BSC/Polygon) and STBU **V3** (Arbitrum) are
eligible. **Legacy V1 is NOT eligible** — invalid since January 2022, cannot be
migrated. After the deadline, V2 and V3 also become invalid for migration.

**Eligible contracts** (always tell users to verify the exact address on
https://stbu.stobox.io before sending anything):
- ETH: `0xa6422e3e219ee6d4c1b18895275fe43556fd50ed`
- BSC: `0xb0c4080a8Fa7afa11a09473f3be14d44AF3f8743`
- Polygon: `0xcf403036bc139d30080d2cf0f5b48066f98191bb`
- Arbitrum (V3): `0x1cb9bD2c6E7F4A7DE3778547d46C8D4c22abC093`

**Which path applies (ask the holder where their STBU is):**
- **Case 1 — in your own wallet:** sign in at https://stbu.stobox.io, connect the
  wallet, burn (early, before the deadline), watch it confirm, then claim from
  15 Sep on Base to the same wallet. Keep a little native gas on each chain.
- **Case 2 — in a Stobox 4 wallet, no exported key:** you're still included.
  Email support@stobox.io **from your Stobox 4 email** with the Base wallet
  address where you want to receive STBU (no exchange/CEX deposit addresses).
  The team verifies via a two-person review; then you claim from 15 Sep.
- **Case 3 — on an exchange (MEXC, Gate.io, …):** withdraw to your own
  self-custody wallet first, then follow Case 1.

Full guide: https://www.stobox.io/blog/stobox-4-setting-new-stobox-rising.
⚠️ Only send official STBU to the burn address shown on https://stbu.stobox.io.
Ignore any DM, ad, or link offering an "early claim" or asking you to "validate"
your wallet — Stobox will never DM you first.

For the current migration status and steps, use /migrate. Authoritative dates are
governed by the bot's canonical facts.

## Security

Stobox will never ask for your seed phrase, recovery phrase, or private key, and
staff **never DM you first and never ask you to DM them** (a common scam line lately
is "DM me since we can't DM you first" — that's a scammer). Only trust links from
stobox.io. Never share your seed phrase with anyone — if you have, treat that wallet
as compromised and move your funds immediately.

> Authoritative token facts (issuer, class, supply, migration dates) are governed
> by the bot's canonical facts and the live site, which take precedence over this
> summary.
