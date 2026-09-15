"""Per-request ManageBac credential resolution.

HTTP credentials live in the MCP server's encrypted account vault. Life OS and
other clients supply only resource-scoped OAuth tokens and opaque account IDs.
Environment credentials are supported only when no request resolver is active
(the sync-only CLI and local development).
"""

from __future__ import annotations

import base64
import os
from typing import Callable, Optional, Tuple

from .errors import AppError

AUTH_MISSING_CREDENTIALS = "AUTH_MISSING_CREDENTIALS"

Credentials = Tuple[str, str]
Resolver = Callable[[], Optional[Credentials]]

_resolver: Optional[Resolver] = None


def set_resolver(resolver: Optional[Resolver]) -> None:
    """Register the callback used to read credentials from the live request."""

    global _resolver
    _resolver = resolver


def parse_basic_auth(header: Optional[str]) -> Optional[Credentials]:
    """Return ``(username, password)`` from an ``Authorization: Basic`` header."""

    if not header:
        return None
    try:
        scheme, value = header.split(" ", 1)
    except ValueError:
        return None
    if scheme.lower() != "basic":
        return None
    try:
        raw = base64.b64decode(value).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return None
    if ":" not in raw:
        return None
    username, password = raw.split(":", 1)
    if not username or not password:
        return None
    return username, password


def require_credentials(config) -> Credentials:
    """Resolve credentials for the current request, or raise ``AppError``.

    Order: the registered request resolver first (Life OS Basic auth), then the
    environment variables named in the ManageBac config (CLI/local fallback).
    """

    if _resolver is not None:
        creds = _resolver()
        if creds and creds[0] and creds[1]:
            return creds
        raise AppError(AUTH_MISSING_CREDENTIALS, "Подключите аккаунт на managebac.archik.tech.")
    username = os.getenv(config.auth.username_env)
    password = os.getenv(config.auth.password_env)
    if username and password:
        return username, password
    raise AppError(
        AUTH_MISSING_CREDENTIALS,
        "ManageBac credentials were not provided for this request. "
        "Connect the account in Life OS → Settings → My connections.",
    )
