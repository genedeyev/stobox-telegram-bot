"""Reingest webhook security (ARCHITECTURE.md §2.1).

The GitHub Action in the site repo POSTs to `/api/reingest` after a successful
Vercel deploy, signing the body with a shared secret (GitHub-style
`X-Hub-Signature-256: sha256=<hex>`). We verify with a constant-time compare
before triggering a sync — an unauthenticated reingest is a DoS / poisoning
vector, so the signature is mandatory.
"""

from __future__ import annotations

import hashlib
import hmac


def sign(secret: str, body: bytes) -> str:
    """Produce the `sha256=<hex>` header value for a body (used by senders/tests)."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def verify_signature(secret: str | None, body: bytes, header: str | None) -> bool:
    """Constant-time verification. False if secret unset or header missing/bad."""
    if not secret or not header:
        return False
    try:
        return hmac.compare_digest(sign(secret, body), header.strip())
    except Exception:  # noqa: BLE001
        return False
