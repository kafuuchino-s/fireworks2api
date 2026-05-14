from __future__ import annotations

import hashlib
import hmac


def fingerprint_secret(secret: str, length: int = 12) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:length]


def redact_secret(secret: str | None, visible: int = 4) -> str:
    if not secret:
        return ""
    if len(secret) <= visible * 2:
        return "***"
    return f"{secret[:visible]}****{secret[-visible:]}"


def hmac_prefix(secret: str, value: str, length: int = 12) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()[:length]


__all__ = ["fingerprint_secret", "redact_secret", "hmac_prefix"]
