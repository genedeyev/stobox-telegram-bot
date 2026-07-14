---
title: ERC-3643 and RWA Tokenization
version: "2026.07"
author: Stobox
date: 2026-07-01
category: technical
product: Tokenization
language: en
visibility: public
source_url: https://www.stobox.io/learn/erc-3643-vs-stv3-transfer-restriction-models
---

# ERC-3643 and RWA Tokenization

This is general education about permissioned security-token standards used for
real-world asset (RWA) tokenization.

## What ERC-3643 is

ERC-3643 (the T-REX standard) is an open standard for permissioned security
tokens on EVM chains. Unlike ERC-20, transfers are validated against on-chain
compliance rules and verified investor identities, so legal transfer
restrictions are enforced at the protocol level.

## Core components

- Token contract with transfer restrictions.
- Identity registry mapping wallets to verified identities.
- Compliance module encoding eligibility rules.

## Stobox's approach

Stobox develops its own security-token standard (STV3). For how STV3 compares to
ERC-3643's transfer-restriction model, see
https://www.stobox.io/learn/erc-3643-vs-stv3-transfer-restriction-models. Whether
a specific standard applies to STBX or a given offering is a question for the
offering documents and the Stobox team.
