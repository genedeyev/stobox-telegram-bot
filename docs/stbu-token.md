---
title: STBU Token Overview
version: "2026-09-27"
author: Stobox
date: 2026-09-27
category: tokenomics
product: STBU
language: en
visibility: public
source_url: https://www.stobox.io/stbu
---

# STBU Token Overview

Source: https://www.stobox.io/llms-full.txt, section "STBU, the token", read on
27 September 2026. The record with figures read from the chain is
https://www.stobox.io/stbu.

## What STBU is

STBU is a utility token issued by Stobox Innovations Ltd. on Base, at
`0xe0c0F44A84CC4a60206360006ebA237a5e8fC2dd` (chain id 8453, ERC-20). It carries
no ownership of Stobox, no yield, no dividend, no staking, no governance vote and
no buy-back. It is not equity, not a security, not a debt and not a payment
instrument. Stobox does not forecast, target or promise its price. The Stobox
equity is a separate token, STBX, on Arbitrum.

It may pay for services of the Stobox group at a 10% discount on the list price,
for each service whose published terms allow payment in STBU; no service has
published such terms yet. Terminal volume is meant to count toward a badge tier
that lowers the swap fee between 1.00% and 0.10%; that is not switched on, so
every swap pays 1.00% today.

## The one issuer pool

The only pool deployed by the issuer is Uniswap v4 on Base, STBU against USDC,
pool id `0x28bd1d1afcc57d766c5d1c5c38bd8beb376a0c98de719460cdcef60900f7b8b5`.
Other pools on Base carry the STBU name and were not deployed by Stobox
Innovations Ltd. How to tell them apart: https://www.stobox.io/stbu/safety.

Stobox Innovations Ltd. does not sell STBU and is not offering it for sale. There
are no plans for a centralized exchange at this time.

## The migration is over

The burn window closed before 15 September 2026, 00:00 UTC. Nobody can burn or
migrate legacy STBU any more.

Holders who burned before 15 September 2026 claim one for one on Base at
https://stbu.stobox.io. Claims close on 31 December 2026 at 23:59 UTC. Nobody has
the authority to extend that date, and anything unclaimed after it is never
minted. The migration claim contract is
`0xec5B3e512de13cb5DdFD84B2DF3167230F2e66a4` on Base.

The legacy contracts were discontinued on 15 September 2026. They are not STBU
and cannot be migrated, even where some interfaces still show a price for them:

- Ethereum `0xa6422e3e219ee6d4c1b18895275fe43556fd50ed`
- BNB Chain `0xb0c4080a8fa7afa11a09473f3be14d44af3f8743`
- Polygon `0xcf403036bc139d30080d2cf0f5b48066f98191bb`
- Arbitrum `0x1cb9bd2c6e7f4a7de3778547d46c8d4c22abc093`

Two of them are named "Stobox Token v.2" and "Stobox Token v.3", which reads as
newer than the live token and is not.

## Supply

The contract cap is 250,000,000 STBU, which no one can raise. The issuance the
company has determined is 216,563,456 STBU.

## Help

Questions about a specific burn or claim: support@stobox.io. Never share a seed
phrase or private key with anyone, including Stobox.
