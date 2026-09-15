"""Per-request ManageBac credential resolution.

HTTP credentials live in the MCP server's encrypted account vault. Life OS and
other clients supply only resource-scoped OAuth tokens and opaque account IDs.
Environment credentials are supported only when no request resolver is active
(the sync-only CLI and local development).
"""

from __future__ import annotations

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


def require_credentials(config) -> Credentials:
    """Resolve credentials for the current request, or raise ``AppError``.

    Use the server-side request resolver in HTTP mode. Environment variables
    are consulted only when no resolver is registered (CLI/local mode).
    """

    if _resolver is not None:
        creds = _resolver()
        if creds and creds[0] and creds[1]:
            return creds
        raise AppError(AUTH_MISSING_CREDENTIALS, "Connect an account on managebac.archik.tech.")
    username = os.getenv(config.auth.username_env)
    password = os.getenv(config.auth.password_env)
    if username and password:
        return username, password
    raise AppError(
        AUTH_MISSING_CREDENTIALS,
        "ManageBac credentials were not provided for this request. "
        "Connect the account in Life OS → Settings → My connections.",
    )
